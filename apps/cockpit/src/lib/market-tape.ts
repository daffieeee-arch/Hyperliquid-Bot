import { existsSync } from "node:fs";
import { join } from "node:path";

import { asyncBufferFromFile, parquetReadObjects } from "hyparquet";
import { compressors } from "hyparquet-compressors";

import { bindVenueCaptureRun } from "./capture-runs";
import { DEFAULT_CAPTURE_FRESH_MAX_S, resolveCaptureFreshMaxSeconds } from "./capture-freshness";
import {
  applyTapeRow,
  createVenueTapeState,
  lastEventIso,
  snapshotInstruments,
  type RawTapeRow,
  type VenueTapeState,
} from "./market-tape-decode";
import {
  MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S,
  type MarketTapeStrip,
  type VenueMarketTape,
} from "./market-tape-types";
import { listPublishedPartFiles, type PublishedPartFile } from "./parquet-parts";
import {
  VENUE_CAPTURE_CONTRACTS,
  VENUE_CAPTURE_STRIP_ORDER,
  findRepoRoot,
  requirePaperTradingMode,
  type VenueCaptureContract,
  type VenueCaptureQuery,
} from "./paths";
import { secondsBetween } from "./poll-state";
import type { CaptureBindingSource } from "./types";

/** Parts parsed when a run is first seen; older parts are counted, not read. */
export const MARKET_TAPE_COLD_START_PARTS = 3;
/** Runs kept in memory; least recently touched is evicted. */
export const MARKET_TAPE_MAX_CACHED_RUNS = 12;

const TAPE_COLUMNS = [
  "venue",
  "product",
  "channel",
  "direction",
  "frame_type",
  "received_utc_ns",
  "payload_bytes",
] as const;

type RunTapeCache = {
  runDir: string;
  processed: Set<string>;
  state: VenueTapeState;
  skippedOnColdStart: number;
  /** Last event received inside the newest processed part, for publication lag. */
  newestProcessed: { name: string; mtimeUtc: string; lastEventUtc: string | undefined } | undefined;
  touched: number;
  /**
   * Refresh currently running for this run. `applyTapeRow` is not idempotent
   * (counters, recent-trade ring), so two overlapping refreshes must never both
   * fold the same part into `state`; callers chain on this promise instead.
   */
  inflight: Promise<unknown> | undefined;
};

type TapeCacheStore = Map<string, RunTapeCache>;

const CACHE_KEY = Symbol.for("hyperliquid-bot.cockpit.market-tape-cache");

/**
 * Process-wide cache that survives Next.js dev-mode module re-evaluation.
 *
 * Published parts are immutable (the writer renames a complete file into
 * place), so a part name only ever has to be parsed once per process.
 */
function cacheStore(): TapeCacheStore {
  const holder = globalThis as typeof globalThis & { [CACHE_KEY]?: TapeCacheStore };
  holder[CACHE_KEY] ??= new Map();
  return holder[CACHE_KEY];
}

export function resetMarketTapeCache(): void {
  cacheStore().clear();
}

export function marketTapeCacheSize(): number {
  return cacheStore().size;
}

function touchRun(runDir: string): RunTapeCache {
  const store = cacheStore();
  let entry = store.get(runDir);
  if (entry === undefined) {
    entry = {
      runDir,
      processed: new Set(),
      state: createVenueTapeState(),
      skippedOnColdStart: 0,
      newestProcessed: undefined,
      touched: 0,
      inflight: undefined,
    };
    store.set(runDir, entry);
  }
  entry.touched = Date.now();
  if (store.size > MARKET_TAPE_MAX_CACHED_RUNS) {
    // Never evict a run mid-refresh: a new caller would cold-start a second
    // entry and parse the same parts in parallel with the discarded one.
    const oldest = [...store.values()]
      .filter((candidate) => candidate.inflight === undefined && candidate.runDir !== runDir)
      .sort((left, right) => left.touched - right.touched)[0];
    if (oldest !== undefined) {
      store.delete(oldest.runDir);
    }
  }
  return entry;
}

function isRawTapeRow(value: unknown): value is RawTapeRow & { payload_bytes: unknown } {
  return typeof value === "object" && value !== null;
}

function payloadText(value: unknown): string | undefined {
  if (typeof value === "string") {
    return value;
  }
  if (value instanceof Uint8Array) {
    return new TextDecoder().decode(value);
  }
  return undefined;
}

export type IngestedPart = {
  rows: number;
  /** Newest receive time inside this part; undefined when it had no decodable row. */
  lastEventUtc: string | undefined;
};

/** Read one published part and fold every row into the venue state. */
export async function ingestPart(
  state: VenueTapeState,
  part: PublishedPartFile,
): Promise<IngestedPart> {
  const file = await asyncBufferFromFile(part.path);
  const rows = await parquetReadObjects({ file, compressors, columns: [...TAPE_COLUMNS] });
  let ingested = 0;
  let lastNs: bigint | undefined;
  for (const raw of rows) {
    if (!isRawTapeRow(raw)) {
      continue;
    }
    const payload = payloadText(raw.payload_bytes);
    const receivedNs = raw.received_utc_ns;
    if (
      payload === undefined ||
      typeof raw.venue !== "string" ||
      typeof raw.product !== "string" ||
      typeof raw.channel !== "string" ||
      typeof raw.direction !== "string" ||
      typeof raw.frame_type !== "string"
    ) {
      continue;
    }
    const ns =
      typeof receivedNs === "bigint"
        ? receivedNs
        : typeof receivedNs === "number"
          ? BigInt(Math.trunc(receivedNs))
          : undefined;
    if (ns === undefined) {
      continue;
    }
    applyTapeRow(state, {
      venue: raw.venue,
      product: raw.product,
      channel: raw.channel,
      direction: raw.direction,
      frame_type: raw.frame_type,
      received_utc_ns: ns,
      payload,
    });
    ingested += 1;
    if (lastNs === undefined || ns > lastNs) {
      lastNs = ns;
    }
  }
  return {
    rows: ingested,
    lastEventUtc:
      lastNs === undefined ? undefined : new Date(Number(lastNs / 1_000_000n)).toISOString(),
  };
}

/**
 * Bring a run's cache up to date with the parts currently published.
 *
 * Only parts not seen before are parsed. On the first visit at most
 * `MARKET_TAPE_COLD_START_PARTS` newest parts are read; the rest are counted so
 * volume is still reported without re-reading the whole retain.
 */
export async function refreshRunTape(
  runDir: string,
  coldStartParts: number = MARKET_TAPE_COLD_START_PARTS,
): Promise<{ cache: RunTapeCache; parts: PublishedPartFile[]; parsed: number }> {
  const cache = touchRun(runDir);
  // Serialise per run: a second caller (StrictMode double-fetch, a second tab,
  // two workspaces on their own clocks) waits for the running refresh and then
  // sees the parts it would have parsed as already processed.
  const previous = cache.inflight ?? Promise.resolve();
  const run = previous
    .catch(() => undefined)
    .then(() => refreshRunTapeExclusive(cache, coldStartParts));
  cache.inflight = run;
  try {
    return await run;
  } finally {
    if (cache.inflight === run) {
      cache.inflight = undefined;
    }
  }
}

async function refreshRunTapeExclusive(
  cache: RunTapeCache,
  coldStartParts: number,
): Promise<{ cache: RunTapeCache; parts: PublishedPartFile[]; parsed: number }> {
  const runDir = cache.runDir;
  const parts = listPublishedPartFiles(join(runDir, "raw"));
  const pending = parts.filter((part) => !cache.processed.has(part.name));
  let toParse = pending;
  if (cache.processed.size === 0 && pending.length > coldStartParts) {
    toParse = pending.slice(pending.length - coldStartParts);
    for (const skipped of pending.slice(0, pending.length - coldStartParts)) {
      cache.processed.add(skipped.name);
      cache.skippedOnColdStart += 1;
    }
  }
  let parsed = 0;
  for (const part of toParse) {
    const ingested = await ingestPart(cache.state, part);
    cache.processed.add(part.name);
    parsed += 1;
    cache.newestProcessed = {
      name: part.name,
      mtimeUtc: part.mtimeUtc,
      lastEventUtc: ingested.lastEventUtc,
    };
  }
  return { cache, parts, parsed };
}

const MISSING_ERROR_PATTERN =
  /missing|not pointed|needs ARTIFACT_ROOT|needs a run_id|fail closed|Counts are not invented|no live retain/i;

function emptyTape(
  contract: VenueCaptureContract,
  observedAt: string,
  status: VenueMarketTape["status"],
  error: string,
  runId: string | undefined,
  bindingSource: CaptureBindingSource,
): VenueMarketTape {
  return {
    id: contract.id,
    chip: contract.chip,
    series: contract.series,
    venue: contract.venue,
    contractProduct: contract.product,
    runId,
    bindingSource,
    status,
    error,
    parts: {
      published: 0,
      processed: 0,
      skippedOnColdStart: 0,
      bytes: 0,
      newestPartName: undefined,
      newestPartMtimeUtc: undefined,
    },
    instruments: [],
    lastEventUtc: undefined,
    lastEventAgeS: undefined,
    publicationLagS: undefined,
    note: "Stored market data is not invented when the run cannot be read.",
    observedAt,
  };
}

export async function loadVenueMarketTape(
  contract: VenueCaptureContract,
  env: NodeJS.Dict<string>,
  repoRoot: string,
  query: VenueCaptureQuery,
  observedAt: string,
  freshMaxSeconds: number = DEFAULT_CAPTURE_FRESH_MAX_S,
  coldStartParts: number = MARKET_TAPE_COLD_START_PARTS,
): Promise<{ tape: VenueMarketTape; parsed: number }> {
  let runId: string | undefined;
  let bindingSource: CaptureBindingSource = "unbound";
  try {
    const resolved = bindVenueCaptureRun(contract, env, repoRoot, query, {
      now: observedAt,
      freshMaxSeconds,
    });
    runId = resolved.runId;
    bindingSource = resolved.binding_source;
    if (!existsSync(resolved.runDir) || !existsSync(join(resolved.runDir, "capture-claim.json"))) {
      return {
        tape: emptyTape(
          contract,
          observedAt,
          "missing",
          `${contract.refuseLabel} run directory or capture-claim.json is missing under ${resolved.runDir}.`,
          runId,
          bindingSource,
        ),
        parsed: 0,
      };
    }
    const { cache, parts, parsed } = await refreshRunTape(resolved.runDir, coldStartParts);
    const newest = parts.at(-1);
    const lastEventUtc = lastEventIso(cache.state);
    const publicationLagS =
      cache.newestProcessed === undefined
        ? undefined
        : secondsBetween(cache.newestProcessed.lastEventUtc, cache.newestProcessed.mtimeUtc);
    const instruments = snapshotInstruments(cache.state);
    const note =
      parts.length === 0
        ? "No published Parquet part yet; the writer publishes after ≤60s or 5 000 records."
        : publicationLagS !== undefined && publicationLagS > MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S
          ? `Publication lag ${String(publicationLagS)}s exceeds the writer's ≤${String(MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S)}s rotation; data age includes unpublished in-memory segments.`
          : `Values come from published parts only; the current in-memory segment (≤${String(MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S)}s) is not visible yet.`;
    return {
      tape: {
        id: contract.id,
        chip: contract.chip,
        series: contract.series,
        venue: contract.venue,
        contractProduct: contract.product,
        runId,
        bindingSource,
        status: "ok",
        error: undefined,
        parts: {
          published: parts.length,
          processed: cache.processed.size - cache.skippedOnColdStart,
          skippedOnColdStart: cache.skippedOnColdStart,
          bytes: parts.reduce((sum, part) => sum + part.bytes, 0),
          newestPartName: newest?.name,
          newestPartMtimeUtc: newest?.mtimeUtc,
        },
        instruments,
        lastEventUtc,
        lastEventAgeS: secondsBetween(lastEventUtc, observedAt),
        publicationLagS,
        note,
        observedAt,
      },
      parsed,
    };
  } catch (error: unknown) {
    const message =
      error instanceof Error
        ? error.message
        : `${contract.refuseLabel} market tape is unavailable.`;
    return {
      tape: emptyTape(
        contract,
        observedAt,
        MISSING_ERROR_PATTERN.test(message) ? "missing" : "error",
        message,
        runId,
        bindingSource,
      ),
      parsed: 0,
    };
  }
}

export async function loadMarketTapeStrip(
  env: NodeJS.Dict<string> = process.env,
  repoRoot: string = findRepoRoot(),
  query: VenueCaptureQuery = {},
  now: () => string = () => new Date().toISOString(),
  coldStartParts: number = MARKET_TAPE_COLD_START_PARTS,
): Promise<MarketTapeStrip> {
  requirePaperTradingMode(env.TRADING_MODE);
  const observedAt = now();
  const freshMaxSeconds = resolveCaptureFreshMaxSeconds(env);
  const venues: VenueMarketTape[] = [];
  let partsParsedThisCall = 0;
  for (const id of VENUE_CAPTURE_STRIP_ORDER) {
    const { tape, parsed } = await loadVenueMarketTape(
      VENUE_CAPTURE_CONTRACTS[id],
      env,
      repoRoot,
      query,
      observedAt,
      freshMaxSeconds,
      coldStartParts,
    );
    venues.push(tape);
    partsParsedThisCall += parsed;
  }
  return {
    observed_at: observedAt,
    venues,
    cache: { runsCached: marketTapeCacheSize(), partsParsedThisCall },
  };
}
