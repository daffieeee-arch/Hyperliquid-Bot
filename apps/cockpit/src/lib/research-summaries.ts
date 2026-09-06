import { existsSync, readFileSync } from "node:fs";
import { relative, resolve, sep } from "node:path";

import {
  listResearchOutSummaries,
  parsePanelSummary,
  researchOutRoot,
  unavailableSufficiency,
  type ResearchSufficiency,
} from "./research-p0";

export type ResearchSummaryListItem = {
  path: string;
  verdict: string;
  panelVersion: string;
};

export type ResearchSummaryList = {
  root: string | undefined;
  items: ResearchSummaryListItem[];
  note: string;
};

export function resolveResearchSummaryPath(root: string, requested: string): string {
  const trimmed = requested.trim();
  if (trimmed === "" || trimmed.includes("\0") || trimmed.includes("..")) {
    throw new Error("research-out path is not a panel-summary.json under the pointed root.");
  }
  const normalized = trimmed.replaceAll("\\", "/");
  if (!normalized.endsWith("panel-summary.json")) {
    throw new Error("research-out path must name panel-summary.json.");
  }
  const resolved = resolve(root, normalized);
  const rootResolved = resolve(root);
  const prefix = rootResolved.endsWith(sep) ? rootResolved : `${rootResolved}${sep}`;
  if (resolved !== rootResolved && !resolved.startsWith(prefix)) {
    throw new Error("research-out path escaped the pointed root.");
  }
  if (!existsSync(resolved)) {
    throw new Error("panel-summary.json is missing; values are not invented.");
  }
  return resolved;
}

export function listResearchSummaries(env: NodeJS.Dict<string>): ResearchSummaryList {
  const root = researchOutRoot(env);
  if (root === undefined || !existsSync(root)) {
    return {
      root,
      items: [],
      note: "research-out is not pointed; WP-Q1 panel summaries stay UNAVAILABLE",
    };
  }
  const files = listResearchOutSummaries(root).sort();
  const items = files.map((path) => {
    const relativePath = relative(root, path);
    try {
      const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
      const sufficiency = parsePanelSummary(parsed, relativePath);
      return {
        path: relativePath,
        verdict: sufficiency.verdict,
        panelVersion: sufficiency.panelVersion,
      };
    } catch (error: unknown) {
      if (error instanceof Error && error.message.includes("fails closed")) {
        throw error;
      }
      return {
        path: relativePath,
        verdict: "UNAVAILABLE",
        panelVersion: "UNAVAILABLE",
      };
    }
  });
  return {
    root,
    items,
    note:
      items.length === 0
        ? "No panel-summary.json under research-out; sufficiency stays UNAVAILABLE"
        : `Copied ${String(items.length)} panel-summary.json file(s)`,
  };
}

export function readResearchSummary(
  env: NodeJS.Dict<string>,
  requestedPath: string,
): ResearchSufficiency {
  const root = researchOutRoot(env);
  if (root === undefined || !existsSync(root)) {
    return unavailableSufficiency(
      "research-out/panel-summary.json is not pointed; sufficiency stays UNAVAILABLE",
    );
  }
  const resolved = resolveResearchSummaryPath(root, requestedPath);
  const parsed: unknown = JSON.parse(readFileSync(resolved, "utf8"));
  return parsePanelSummary(parsed, relative(root, resolved));
}
