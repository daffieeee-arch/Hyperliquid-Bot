import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { DATA1A_PARQUET_GLOB_PREFIX, DATA1A_PARQUET_SUFFIX } from "./paths";
import type { Data1APartListing } from "./types";

function isPublishedParquetPart(name: string): boolean {
  return (
    name.startsWith(DATA1A_PARQUET_GLOB_PREFIX) &&
    name.endsWith(DATA1A_PARQUET_SUFFIX) &&
    !name.startsWith(".")
  );
}

export type PublishedPartFile = {
  name: string;
  path: string;
  bytes: number;
  mtimeUtc: string;
};

/**
 * Published parts in publication order.
 *
 * Part names are `part-<6-digit number>-…`, so a lexical sort is the writer's
 * order. Hidden `.…partial` files are never listed: only an atomically
 * renamed `.parquet` counts as published.
 */
export function listPublishedPartFiles(rawDir: string): PublishedPartFile[] {
  if (!existsSync(rawDir)) {
    return [];
  }
  return readdirSync(rawDir)
    .filter((name) => isPublishedParquetPart(name))
    .sort()
    .map((name) => {
      const path = join(rawDir, name);
      const stats = statSync(path);
      return { name, path, bytes: stats.size, mtimeUtc: new Date(stats.mtimeMs).toISOString() };
    });
}

export function listPublishedParquetParts(rawDir: string): Data1APartListing {
  if (!existsSync(rawDir)) {
    return {
      raw_dir_present: false,
      count: undefined,
      last_part_name: undefined,
      last_part_mtime_utc: undefined,
      bytes: undefined,
    };
  }

  const names = readdirSync(rawDir).filter((name) => isPublishedParquetPart(name));
  if (names.length === 0) {
    return {
      raw_dir_present: true,
      count: 0,
      last_part_name: undefined,
      last_part_mtime_utc: undefined,
      bytes: 0,
    };
  }

  let lastName = names[0] ?? "";
  let lastMtimeMs = Number.NEGATIVE_INFINITY;
  let bytes = 0;
  for (const name of names) {
    const stats = statSync(join(rawDir, name));
    bytes += stats.size;
    if (stats.mtimeMs >= lastMtimeMs) {
      lastMtimeMs = stats.mtimeMs;
      lastName = name;
    }
  }
  return {
    raw_dir_present: true,
    count: names.length,
    last_part_name: lastName,
    last_part_mtime_utc: new Date(lastMtimeMs).toISOString(),
    bytes,
  };
}
