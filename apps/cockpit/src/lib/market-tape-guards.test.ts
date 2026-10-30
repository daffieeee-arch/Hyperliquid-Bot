import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import * as parquet from "hyparquet";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyTapeRow,
  createVenueTapeState,
  snapshotInstruments,
  type RawTapeRow,
} from "./market-tape-decode";
import { loadVenueMarketTape, refreshRunTape, resetMarketTapeCache } from "./market-tape";
import { VENUE_CAPTURE_CONTRACTS } from "./paths";
import { loadVenueCaptureChip } from "./venue-capture";

vi.mock("hyparquet", { spy: true });

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const fixtureRoot = join(repoRoot, "tests/fixtures/market_tape/artifact-root");
const runId = "20260905t180000z-live-retained";
const observedAt = "2026-09-05T18:01:00.000Z";
const levels = [
  { side: "bid", price: "100.00", qty: "1" },
  { side: "ask", price: "101.00", qty: "2" },
];

function bookRow(payload: unknown, session = "public-1"): RawTapeRow {
  return {
    venue: "kraken",
    product: "BTC/USD",
    channel: "normalized_book",
    direction: "local",
    frame_type: "marker",
    session_id: session,
    received_utc_ns: 1_788_631_200_000_000_000n,
    payload: JSON.stringify(payload),
  };
}

function snapshot(session = "public-1"): RawTapeRow {
  return bookRow({ message_type: "snapshot", events: levels }, session);
}

beforeEach(() => resetMarketTapeCache());
afterEach(() => vi.mocked(parquet.parquetReadObjects).mockRestore());

describe("bounded L2 quote provenance", () => {
  it.each([
    ["kraken", "normalized_book"],
    ["bitvavo", "normalized_book"],
    ["bitvavo", "normalized_mdpro_book"],
  ])("does not invent a bootstrap from %s %s deltas", (venue, channel) => {
    const state = createVenueTapeState();
    applyTapeRow(state, { ...bookRow({ message_type: "update", events: levels }), venue, channel });
    const instrument = snapshotInstruments(state)[0];
    expect(instrument?.lastBbo).toBeUndefined();
    expect(instrument?.bboCount).toBe(0);
    expect(instrument?.bboUnavailableReason).toMatch(/no L2 snapshot/);
    applyTapeRow(state, { ...snapshot(), venue, channel });
    expect(snapshotInstruments(state)[0]?.lastBbo).toMatchObject({ bid: "100.00", ask: "101.00" });
  });

  it("clears the cached BBO when a side disappears and restores only from the known book", () => {
    const state = createVenueTapeState();
    applyTapeRow(state, snapshot());
    applyTapeRow(
      state,
      bookRow({ message_type: "update", events: [{ side: "bid", price: "0100.0", qty: "0" }] }),
    );
    expect(snapshotInstruments(state)[0]?.lastBbo).toBeUndefined();
    expect(snapshotInstruments(state)[0]?.bboUnavailableReason).toMatch(/no complete bid and ask/);
    applyTapeRow(
      state,
      bookRow({ message_type: "update", events: [{ side: "bid", price: "99.00", qty: "1" }] }),
    );
    expect(snapshotInstruments(state)[0]?.lastBbo).toMatchObject({ bid: "99.00", ask: "101.00" });
  });

  it.each([
    { message_type: "update", events: [{ side: "bid", price: "NaN", qty: "1" }] },
    { message_type: "update", events: [{ side: "bid", price: "100", qty: "Infinity" }] },
    { message_type: "update", events: [{ side: "bid", price: "100", qty: "-1" }] },
    { message_type: "update", events: [{ side: "wrong", price: "100", qty: "1" }] },
    { message_type: "update", events: [{ side: "bid", price: "102", qty: "1" }] },
    { message_type: "update", events: [null] },
    { message_type: "update" },
    { message_type: "unknown", events: levels },
    null,
  ])("invalidates malformed/crossed books until another snapshot: %j", (payload) => {
    const state = createVenueTapeState();
    applyTapeRow(state, snapshot());
    applyTapeRow(state, bookRow(payload));
    expect(snapshotInstruments(state)[0]?.lastBbo).toBeUndefined();
    applyTapeRow(state, bookRow({ message_type: "update", events: levels }));
    expect(snapshotInstruments(state)[0]?.lastBbo).toBeUndefined();
    applyTapeRow(state, snapshot());
    expect(snapshotInstruments(state)[0]?.lastBbo?.bid).toBe("100.00");
  });

  it("invalidates an undecodable book frame instead of retaining the previous BBO", () => {
    const state = createVenueTapeState();
    applyTapeRow(state, snapshot());
    applyTapeRow(state, { ...bookRow({}), payload: "{broken" });
    applyTapeRow(state, bookRow({ message_type: "update", events: levels }));
    expect(snapshotInstruments(state)[0]?.lastBbo).toBeUndefined();
  });

  it("requires a new snapshot when the quote stream's session changes without a marker", () => {
    const state = createVenueTapeState();
    applyTapeRow(state, snapshot());
    applyTapeRow(state, bookRow({ message_type: "update", events: levels }, "public-2"));
    expect(snapshotInstruments(state)[0]?.lastBbo).toBeUndefined();
    applyTapeRow(state, snapshot("public-2"));
    expect(snapshotInstruments(state)[0]?.lastBbo?.ask).toBe("101.00");
  });

  it.each([
    ["session", "disconnected"],
    ["session", "reconnected"],
    ["session", "session_started"],
    ["data_quality", "gap_detected"],
    ["data_quality", "checksum_error"],
    ["data_quality", "sequence_gap"],
  ])("invalidates cached books on %s/%s before more deltas", (channel, event) => {
    const state = createVenueTapeState();
    applyTapeRow(state, snapshot());
    applyTapeRow(state, { ...bookRow({ event, stream: "public" }), channel });
    applyTapeRow(state, bookRow({ message_type: "update", events: levels }));
    expect(snapshotInstruments(state)[0]?.lastBbo).toBeUndefined();
  });

  it("keeps public L2 independent of Kraken's separate L3 session", () => {
    const state = createVenueTapeState();
    applyTapeRow(state, snapshot());
    applyTapeRow(state, {
      ...bookRow({ event: "reconnected", stream: "l3" }, "l3-2"),
      channel: "session",
    });
    applyTapeRow(state, { ...bookRow({ events: [] }, "l3-2"), channel: "normalized_level3" });
    expect(snapshotInstruments(state)[0]?.lastBbo?.bid).toBe("100.00");
  });

  it("compares top levels exactly even when Number would round away their difference", () => {
    const state = createVenueTapeState();
    applyTapeRow(
      state,
      bookRow({
        message_type: "snapshot",
        events: [
          { side: "bid", price: "100.00000000000000000001", qty: "1" },
          { side: "bid", price: "100.00000000000000000002", qty: "1" },
          { side: "ask", price: "100.00000000000000000003", qty: "1" },
        ],
      }),
    );
    expect(snapshotInstruments(state)[0]?.lastBbo).toMatchObject({
      bid: "100.00000000000000000002",
      ask: "100.00000000000000000003",
    });
  });

  it("keeps a three-part cold start unavailable when its snapshot was in a skipped part", async () => {
    const root = mkdtempSync(join(tmpdir(), "market-tape-bounded-bootstrap-"));
    mkdirSync(join(root, "raw"));
    for (let index = 1; index <= 4; index += 1) {
      writeFileSync(
        join(root, "raw", `part-${String(index).padStart(6, "0")}.parquet`),
        Buffer.alloc(16),
      );
    }
    const read = vi.mocked(parquet.parquetReadObjects).mockImplementation(async () => {
      const { payload, ...rest } = bookRow({ message_type: "update", events: levels });
      return [{ ...rest, payload_bytes: payload }];
    });
    const result = await refreshRunTape(root);
    expect(result.parsed).toBe(3);
    expect(result.cache.skippedOnColdStart).toBe(1);
    expect(read).toHaveBeenCalledTimes(3);
    expect(snapshotInstruments(result.cache.state)[0]?.lastBbo).toBeUndefined();
  });
});

describe("capture claim validation before tape ingestion or cache hits", () => {
  function prepare() {
    const root = mkdtempSync(join(tmpdir(), "market-tape-claim-"));
    const source = join(fixtureRoot, "data-1a/hyperliquid/BTC-PERP", runId);
    const run = join(root, "data-1a/hyperliquid/BTC-PERP", runId);
    mkdirSync(join(run, "raw"), { recursive: true });
    copyFileSync(join(source, "capture-claim.json"), join(run, "capture-claim.json"));
    copyFileSync(
      join(source, "raw/part-000001-000000000001-000000000003-fixture00001.parquet"),
      join(run, "raw/part-000001.parquet"),
    );
    const env = { TRADING_MODE: "PAPER", ARTIFACT_ROOT: root, COCKPIT_DATA1A_RUN_ID: runId };
    return { root, run, env };
  }

  it.each([
    { schema: "unknown" },
    { path_contract: "wrong-path" },
    { run_id: "another-run" },
    { signing: true },
    { twenty_four_seven: true },
  ])("refuses a changed invalid claim even after caching valid quotes: %j", async (mutation) => {
    const { run, env } = prepare();
    const contract = VENUE_CAPTURE_CONTRACTS.hl;
    const valid = await loadVenueMarketTape(contract, env, repoRoot, {}, observedAt);
    expect(valid.tape.status).toBe("ok");
    expect(valid.tape.instruments[0]?.lastBbo).toBeDefined();
    const path = join(run, "capture-claim.json");
    const claim = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
    writeFileSync(path, JSON.stringify({ ...claim, ...mutation }));
    const changed = await loadVenueMarketTape(contract, env, repoRoot, {}, observedAt);
    expect(changed.tape.status).toBe("error");
    expect(changed.parsed).toBe(0);
    expect(changed.tape.instruments).toEqual([]);
    expect(loadVenueCaptureChip(contract, env, repoRoot, {}, observedAt).status).toBe("DEGRADED");
  });

  it("reports malformed JSON as unreadable instead of reusing cached quotes", async () => {
    const { run, env } = prepare();
    await loadVenueMarketTape(VENUE_CAPTURE_CONTRACTS.hl, env, repoRoot, {}, observedAt);
    writeFileSync(join(run, "capture-claim.json"), "{broken");
    const result = await loadVenueMarketTape(
      VENUE_CAPTURE_CONTRACTS.hl,
      env,
      repoRoot,
      {},
      observedAt,
    );
    expect(result.tape.status).toBe("error");
    expect(result.tape.instruments).toEqual([]);
  });
});
