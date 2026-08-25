import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const packagePath = fileURLToPath(new URL("../../package.json", import.meta.url));

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function loadPackageManifest(): Promise<Record<string, unknown>> {
  const source = await readFile(packagePath, "utf8");
  const parsed: unknown = JSON.parse(source);

  if (!isRecord(parsed)) {
    throw new TypeError("package.json must contain a JSON object.");
  }

  return parsed;
}

describe("root package safety", () => {
  it("is private and pinned to the approved package manager", async () => {
    const manifest = await loadPackageManifest();

    expect(manifest.private).toBe(true);
    expect(manifest.packageManager).toBe("pnpm@11.23.0");
  });

  it("does not define publish or deploy scripts", async () => {
    const manifest = await loadPackageManifest();
    const scripts = manifest.scripts;

    if (!isRecord(scripts)) {
      throw new TypeError("package.json scripts must contain a JSON object.");
    }

    const unsafeScriptNames = Object.keys(scripts).filter((name) => /publish|deploy/i.test(name));

    expect(unsafeScriptNames).toEqual([]);
  });
});
