import {
  cpSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import {
  loadNativePaperProjection,
  NATIVE_LEDGER_BYTE_LIMIT,
  NATIVE_LEDGER_RECORD_LIMIT,
} from "./native-paper";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const fixture = join(repoRoot, "tests/fixtures/native_paper/native-demo");
const now = "2026-10-01T12:00:04.000Z";
const temporary: string[] = [];
function copy(source = fixture): string {
  const directory = mkdtempSync(join(tmpdir(), "native-paper-contract-"));
  temporary.push(directory);
  cpSync(source, directory, { recursive: true });
  return directory;
}
function load(directory: string, clock = now) {
  return loadNativePaperProjection(
    { TRADING_MODE: "PAPER", COCKPIT_NATIVE_PAPER_RUN_DIR: directory },
    repoRoot,
    clock,
  );
}
function edit(directory: string, name: string, mutation: (data: Record<string, unknown>) => void) {
  const path = join(directory, name);
  const data = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
  mutation(data);
  writeFileSync(path, JSON.stringify(data));
}
function both(directory: string, mutation: (data: Record<string, unknown>) => void) {
  edit(directory, "state.json", mutation);
  edit(directory, "health.json", mutation);
}
function append(directory: string, record: Record<string, unknown>) {
  const path = join(directory, "ledger.jsonl");
  writeFileSync(path, readFileSync(path, "utf8") + JSON.stringify(record) + "\n");
}
afterEach(() => {
  for (const directory of temporary.splice(0)) rmSync(directory, { recursive: true, force: true });
});

describe("bounded native PAPER projection", () => {
  it("reads actual Python-engine artifacts with explicit DEMO and gross/cash semantics", () => {
    const before = readFileSync(join(fixture, "ledger.jsonl"), "utf8");
    const result = load(fixture);
    expect(result).toMatchObject({
      schema: "native-paper-cockpit-v1",
      origin: "demo",
      state: "ok",
    });
    expect(result.snapshot).toMatchObject({
      runId: "native-demo",
      status: "COMPLETED",
      runClosed: true,
      positionBtc: "0",
      cashUsdc: "99999.9852",
      equityUsdc: "99999.9852",
      realizedGrossUsdc: "-0.0004",
      feesUsdc: "0.0144",
      unrealizedUsdc: "0",
      openOrder: null,
    });
    expect(result.snapshot?.orders).toHaveLength(2);
    expect(result.snapshot?.fills).toHaveLength(2);
    expect(result.message).toContain("not venue-reconciled");
    expect(result.message).toContain("liveness and atomic event completion are unverified");
    expect(readFileSync(join(fixture, "ledger.jsonl"), "utf8")).toBe(before);
  });

  it("reads all 120 orders/fills from ledger, beyond state.json's 100-record suffix", () => {
    const result = load(
      join(repoRoot, "tests/fixtures/native_paper/history/native-demo"),
      "2026-10-01T12:02:02Z",
    );
    expect(result.state).toBe("ok");
    expect(result.snapshot?.orders).toHaveLength(120);
    expect(result.snapshot?.fills).toHaveLength(120);
    expect(result.snapshot?.orders[0]?.id).toBe("native-demo-000001");
    expect(result.snapshot?.orders.at(-1)?.id).toBe("native-demo-000120");
  });

  it("never infers LIVE from RUNNING or a fresh source timestamp", () => {
    const directory = copy();
    edit(directory, "health.json", (data) => {
      data.status = "RUNNING";
      data.run_closed = false;
    });
    expect(load(directory)).toMatchObject({
      origin: "historical",
      state: "ok",
      snapshot: { status: "RUNNING", runClosed: false },
    });
  });

  it("classifies fixture origin by canonical path, including directory symlinks", () => {
    const directory = copy();
    const alias = join(directory, "alias");
    symlinkSync(fixture, alias, "dir");
    expect(load(alias).origin).toBe("demo");
    // A configured path's name does not make a non-fixture run DEMO.
    expect(load(directory).origin).toBe("historical");
  });

  it.each([undefined, ""])(
    "leaves absent/empty binding %s UNBOUND without a fixture fallback",
    (binding) => {
      expect(
        loadNativePaperProjection({ COCKPIT_NATIVE_PAPER_RUN_DIR: binding }, repoRoot, now),
      ).toMatchObject({ origin: "unbound", state: "missing", snapshot: null });
    },
  );

  it("does not leak the directory or parser exception for a missing/broken binding", () => {
    const directory = copy();
    unlinkSync(join(directory, "health.json"));
    const result = load(directory);
    expect(result).toMatchObject({ state: "missing", snapshot: null });
    expect(JSON.stringify(result)).not.toContain(directory);
  });

  it.each(["LIVE", "SHADOW", "TESTNET", "paper"])("fails closed for trading mode %s", (mode) => {
    expect(
      loadNativePaperProjection(
        { TRADING_MODE: mode, COCKPIT_NATIVE_PAPER_RUN_DIR: fixture },
        repoRoot,
        now,
      ),
    ).toMatchObject({ state: "error", snapshot: null });
  });

  it("labels old source times stale without claiming a dead or finished writer", () => {
    const result = load(fixture, "2026-10-30T12:00:00Z");
    expect(result).toMatchObject({ state: "stale", snapshot: { status: "COMPLETED" } });
    expect(result.message).toContain("liveness");
  });

  it("retains stale-data kill state separately from wall-clock age", () => {
    const directory = copy();
    append(directory, { type: "kill_switch", state: "HALT_NEW", reason: "stale_data" });
    both(directory, (data) => {
      data.kill_switch = "HALT_NEW";
      data.kill_reason = "stale_data";
    });
    edit(directory, "health.json", (data) => {
      data.status = "HALT_NEW";
      data.stale = true;
    });
    expect(load(directory)).toMatchObject({
      state: "stale",
      snapshot: { killReason: "stale_data" },
    });
  });

  it.each(["FAILED", "ledger_write_failed"])(
    "withholds all history and economics after %s",
    (failure) => {
      const directory = copy();
      edit(directory, "health.json", (data) => {
        if (failure === "FAILED") data.status = "FAILED";
        else data.ledger_write_failed = true;
      });
      expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
    },
  );

  it("retains missing mark and unrealized/equity as unavailable for an open position", () => {
    const directory = copy();
    const path = join(directory, "ledger.jsonl");
    const lines = readFileSync(path, "utf8").trimEnd().split("\n");
    // First accepted/fill/completed sequence is an actual producer prefix.
    writeFileSync(path, lines.slice(0, 3).join("\n") + "\n");
    both(directory, (data) => {
      data.position_quantity = "0.0002";
      data.average_entry_price = "80001";
      data.realized_pnl_usdc = "0";
      data.fees_usdc = "0.00720009";
      data.unrealized_pnl_usdc = null;
      data.order_count = 1;
      data.fill_count = 1;
    });
    edit(directory, "state.json", (data) => {
      data.orders = (data.orders as unknown[]).slice(0, 1);
      data.fills = (data.fills as unknown[]).slice(0, 1);
      data.cash_usdc = "99983.99259991";
    });
    edit(directory, "health.json", (data) => {
      data.mark_price = null;
      data.mark_source = null;
      data.equity_usdc = null;
    });
    expect(load(directory)).toMatchObject({
      state: "ok",
      snapshot: { positionBtc: "0.0002", markPrice: null, unrealizedUsdc: null, equityUsdc: null },
    });
  });

  it("permits a flat position with no mark and actual zero unrealized", () => {
    const directory = copy();
    edit(directory, "health.json", (data) => {
      data.mark_price = null;
      data.mark_source = null;
    });
    expect(load(directory)).toMatchObject({
      state: "ok",
      snapshot: { markPrice: null, unrealizedUsdc: "0", equityUsdc: "99999.9852" },
    });
  });

  it.each([
    "order_count",
    "fill_count",
    "risk_rejection_count",
    "position_quantity",
    "fees_usdc",
    "kill_reason",
    "source_commit",
  ])("rejects a mixed state/health generation at %s", (field) => {
    const directory = copy();
    edit(directory, "health.json", (data) => {
      data[field] = typeof data[field] === "number" ? 1 : "changed";
    });
    expect(load(directory).snapshot).toBeNull();
  });

  it("rejects ledger-ahead writes even when the JSON projections agree", () => {
    const directory = copy();
    append(directory, { type: "kill_switch", state: "HALT_NEW", reason: "operator" });
    expect(load(directory)).toMatchObject({ state: "pending", snapshot: null });
  });

  it("rejects counts and suffix mutations even when state/health counts agree", () => {
    const directory = copy();
    both(directory, (data) => {
      data.order_count = 3;
    });
    expect(load(directory)).toMatchObject({ state: "pending", snapshot: null });
    both(directory, (data) => {
      data.order_count = 2;
    });
    edit(directory, "state.json", (data) => {
      (data.orders as Record<string, unknown>[])[0]!.reason = "different";
    });
    expect(load(directory)).toMatchObject({ state: "pending", snapshot: null });
  });

  it("compares Python nanosecond integers exactly instead of rounding them to JS numbers", () => {
    const directory = copy();
    const path = join(directory, "state.json");
    const original = readFileSync(path, "utf8");
    writeFileSync(path, original.replace("1790856002000000000", "1790856002000000001"));
    expect(load(directory)).toMatchObject({ state: "pending", snapshot: null });
  });

  it("accepts a closed run with one accepted open order and no terminal count", () => {
    const directory = copy();
    const path = join(directory, "ledger.jsonl");
    const accepted = readFileSync(path, "utf8").split("\n")[0]!;
    writeFileSync(path, accepted + "\n");
    both(directory, (data) => {
      data.position_quantity = "0";
      data.average_entry_price = "0";
      data.realized_pnl_usdc = "0";
      data.fees_usdc = "0";
      data.unrealized_pnl_usdc = "0";
      data.order_count = 0;
      data.fill_count = 0;
    });
    edit(directory, "state.json", (data) => {
      data.orders = [];
      data.fills = [];
      data.open_order = "native-demo-000001";
      data.cash_usdc = "100000";
    });
    edit(directory, "health.json", (data) => {
      data.equity_usdc = "100000";
    });
    expect(load(directory)).toMatchObject({
      state: "ok",
      snapshot: { runClosed: true, openOrder: "native-demo-000001", orders: [] },
    });
  });

  it.each(["run-claim.json", "state.json", "health.json"])(
    "rejects non-PAPER identity in %s",
    (name) => {
      const directory = copy();
      edit(directory, name, (data) => {
        data.mode = "LIVE";
      });
      expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
    },
  );

  it.each(["schema", "venue", "instrument_id", "resume", "venue_orders_submitted"])(
    "rejects unsupported %s",
    (field) => {
      const directory = copy();
      edit(directory, "health.json", (data) => {
        data[field] = typeof data[field] === "boolean" ? true : "unsupported";
      });
      expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
    },
  );

  it("does not trust an incomplete ledger line", () => {
    const directory = copy();
    const path = join(directory, "ledger.jsonl");
    writeFileSync(path, readFileSync(path, "utf8").trimEnd());
    expect(load(directory)).toMatchObject({ state: "pending", snapshot: null });
  });

  it("rejects malformed JSON and unknown ledger types without exposing input", () => {
    const directory = copy();
    append(directory, { type: "future_type", private_detail: "sensitive fixture value" });
    expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
    writeFileSync(join(directory, "health.json"), "{sensitive malformed contents");
    expect(JSON.stringify(load(directory))).not.toContain("sensitive");
  });

  it("rejects impossible calendar dates instead of normalizing them into valid observations", () => {
    const directory = copy();
    edit(directory, "health.json", (data) => {
      data.observed_at_utc = "2026-02-30T12:00:03Z";
    });
    expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
  });

  it("rejects an unknown fill source rather than presenting it as a native fill model", () => {
    const directory = copy();
    const path = join(directory, "ledger.jsonl");
    writeFileSync(
      path,
      readFileSync(path, "utf8").replace(/"source"\s*:\s*"bbo"/, '"source":"unknown"'),
    );
    expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
  });

  it("rejects future source timestamps", () => {
    expect(load(fixture, "2026-10-01T12:00:00Z")).toMatchObject({ state: "error", snapshot: null });
  });

  it("caps ledger bytes and records without partial histories", () => {
    const directory = copy();
    writeFileSync(join(directory, "ledger.jsonl"), " ".repeat(NATIVE_LEDGER_BYTE_LIMIT + 1));
    expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
    writeFileSync(join(directory, "ledger.jsonl"), "{}\n".repeat(NATIVE_LEDGER_RECORD_LIMIT + 1));
    expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
  });

  it("rejects symlink artifacts and nonblocking FIFO artifacts", () => {
    const directory = copy();
    unlinkSync(join(directory, "health.json"));
    symlinkSync(join(fixture, "health.json"), join(directory, "health.json"));
    expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
    unlinkSync(join(directory, "health.json"));
    execFileSync("mkfifo", [join(directory, "health.json")]);
    expect(load(directory)).toMatchObject({ state: "error", snapshot: null });
  });
});
