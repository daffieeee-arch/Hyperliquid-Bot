import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

const boundary = vi.hoisted(() => ({ afterRead: undefined as (() => void) | undefined }));
vi.mock("node:fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:fs")>();
  return {
    ...actual,
    readSync: (...args: Parameters<typeof actual.readSync>) => {
      const result = actual.readSync(...args);
      boundary.afterRead?.();
      return result;
    },
  };
});

import { loadNativePaperProjection } from "./native-paper";

const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const temporary: string[] = [];
afterEach(() => {
  boundary.afterRead = undefined;
  for (const path of temporary.splice(0)) rmSync(path, { recursive: true, force: true });
});

describe("native snapshot write boundaries", () => {
  it("withholds a snapshot if the ledger grows during the bounded fd read", () => {
    const directory = mkdtempSync(join(tmpdir(), "native-paper-race-"));
    temporary.push(directory);
    cpSync(join(repoRoot, "tests/fixtures/native_paper/native-demo"), directory, {
      recursive: true,
    });
    let reads = 0;
    boundary.afterRead = () => {
      reads += 1;
      if (reads !== 7) return;
      const path = join(directory, "ledger.jsonl");
      writeFileSync(path, readFileSync(path, "utf8") + "{}\n");
    };
    const result = loadNativePaperProjection(
      { COCKPIT_NATIVE_PAPER_RUN_DIR: directory },
      repoRoot,
      "2026-10-01T12:00:04Z",
    );
    expect(result).toMatchObject({ state: "pending", snapshot: null });
  });

  it("withholds individually stable files that change between the first and second pass", () => {
    const directory = mkdtempSync(join(tmpdir(), "native-paper-race-"));
    temporary.push(directory);
    cpSync(join(repoRoot, "tests/fixtures/native_paper/native-demo"), directory, {
      recursive: true,
    });
    let reads = 0;
    boundary.afterRead = () => {
      reads += 1;
      if (reads !== 8) return;
      const path = join(directory, "state.json");
      writeFileSync(
        path,
        readFileSync(path, "utf8").replace(
          '"cash_usdc": "99999.9852"',
          '"cash_usdc": "99999.9851"',
        ),
      );
    };
    const result = loadNativePaperProjection(
      { COCKPIT_NATIVE_PAPER_RUN_DIR: directory },
      repoRoot,
      "2026-10-01T12:00:04Z",
    );
    expect(result).toMatchObject({ state: "pending", snapshot: null });
  });
});
