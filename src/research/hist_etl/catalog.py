"""Regenerate pipeline DuckDB views without replacing an existing catalog."""

from __future__ import annotations

import difflib
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from research.hist_etl.errors import HistEtlError
from research.hist_etl.files import atomic_write_text, warn

_BLOCK = re.compile(
    r"-- BEGIN research\.hist_etl\n.*?-- END research\.hist_etl\n?",
    re.DOTALL,
)
_VIEW_DECL = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_VIEW_STMT = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+.*?;\s*",
    re.IGNORECASE | re.DOTALL,
)
_VIEW_NAME = re.compile(r"[a-z][a-z0-9_]*")
_BINANCE_FILE = re.compile(r"^([A-Z0-9]+)-\d{4}-\d{2}\.parquet$")
_KRAKEN_FILE = re.compile(r"^\d{4}-\d{2}\.parquet$")
_FUNDING_VIEW = re.compile(r"hist_hl_funding_[a-z0-9]+")
_BEGIN = "-- BEGIN research.hist_etl"
_END = "-- END research.hist_etl"
_LOCK_ATTEMPTS = 5


def refresh_catalog(
    root: Path,
    *,
    hyperliquid_files: Mapping[str, Sequence[Path]],
    replace_legacy_views: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Write ``catalog.sql`` and create the generated views.

    Existing ``hist_bn_*`` / ``hist_kr_*`` statements outside the generated block
    stay in place. A view or table that already exists in ``research.duckdb`` and
    is not named in the previous hist_etl block is foreign: it is not replaced,
    and the second tuple lists those names so the caller can exit 2.
    ``replace_legacy_views`` copies ``catalog.sql`` to a backup, prints a diff,
    and then lets the generated names replace those statements and relations.
    The SQL file is replaced only after DuckDB accepts the new block.
    ``hyperliquid_files`` maps each manifest coin to the funding month files
    the manifest selects (see ``hyperliquid.funding_view_files``); it has no
    default, so no caller drops or keeps funding views by omission. A
    funding view of the previous block with no selected file is dropped.
    """

    views = render_statements(
        root,
        hyperliquid_files=hyperliquid_files,
        replace_legacy_views=replace_legacy_views,
    )
    catalog_path = root / "catalog.sql"
    existing = catalog_path.read_text(encoding="utf-8") if catalog_path.is_file() else ""
    owned = _managed_names(existing)
    stale = _stale_funding_views(views, owned)
    live = _live_relations(root)
    # A relation that already exists in DuckDB, and was not emitted by the previous
    # hist_etl block, belongs to the operator. CREATE OR REPLACE would destroy it.
    foreign = tuple(
        name
        for name, _statement in views
        if name in live and name not in owned and not replace_legacy_views
    )
    if foreign:
        blocked = set(foreign)
        views = tuple((name, statement) for name, statement in views if name not in blocked)
        for name in foreign:
            warn(
                f"refusing to replace live relation {name}: it is not in the previous "
                "hist_etl catalog block; pass --replace-legacy-views to replace it"
            )
    merged, names, skipped = merge_catalog(
        existing,
        views,
        replace_legacy=replace_legacy_views,
    )
    for name in skipped:
        warn(
            f"skipping view {name}: an existing catalog definition is outside the "
            "hist_etl block; pass --replace-legacy-views to replace it"
        )
    preserved = _preserved_statements(existing, set(names) | set(stale), live)
    if preserved:
        merged = merged.replace(f"{_END}\n", preserved + f"{_END}\n", 1)
    if replace_legacy_views and existing:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = catalog_path.with_name(f"catalog.sql.bak.{stamp}")
        atomic_write_text(backup, existing)
        diff = "".join(
            difflib.unified_diff(
                existing.splitlines(keepends=True),
                merged.splitlines(keepends=True),
                fromfile="catalog.sql",
                tofile="catalog.sql",
            )
        )
        print(diff if diff else "catalog\tno changes\n")
        print(f"catalog\tbackup\t{backup}")
    apply_catalog(root, merged, drop=stale)
    atomic_write_text(catalog_path, merged)
    return names, foreign


def render_statements(
    root: Path,
    *,
    hyperliquid_files: Mapping[str, Sequence[Path]],
    replace_legacy_views: bool = False,
) -> tuple[tuple[str, str], ...]:
    """Pipeline views. Each one reads only files this pipeline wrote.

    Hyperliquid funding files come from the manifest, not a directory scan:
    a file left behind by a renamed or re-ranged dataset would otherwise be
    unioned with the current one and charge a bar's funding twice.
    """

    views: list[tuple[str, str]] = []
    binance = root / "parquet" / "hist_etl" / "binance"
    if binance.is_dir():
        for market_dir in sorted(path for path in binance.iterdir() if path.is_dir()):
            for slug_dir in sorted(path for path in market_dir.iterdir() if path.is_dir()):
                for symbol, files in _binance_files(slug_dir).items():
                    relative = tuple(_relative(root, path) for path in files)
                    for name in _binance_names(
                        market_dir.name,
                        symbol,
                        slug_dir.name,
                        replace_legacy=replace_legacy_views,
                    ):
                        views.append((name, _view(name, relative)))
    kraken = root / "parquet" / "hist_etl" / "kraken" / "ohlcvt"
    if kraken.is_dir():
        for pair_dir in sorted(path for path in kraken.iterdir() if path.is_dir()):
            for interval_dir in sorted(path for path in pair_dir.iterdir() if path.is_dir()):
                month_files = _kraken_files(interval_dir)
                if not month_files:
                    continue
                relative = tuple(_relative(root, path) for path in month_files)
                for name in _kraken_names(
                    pair_dir.name,
                    interval_dir.name,
                    replace_legacy=replace_legacy_views,
                ):
                    views.append((name, _view(name, relative)))
    for coin, funding_files in sorted(hyperliquid_files.items()):
        if not funding_files:
            continue
        relative = tuple(_relative(root, path) for path in funding_files)
        name = f"hist_hl_funding_{coin.lower()}"
        views.append((name, _view(name, relative)))
    return tuple(views)


def _stale_funding_views(views: Sequence[tuple[str, str]], owned: set[str]) -> tuple[str, ...]:
    """Funding views of the previous block that have no selected file now.

    Kept, they would go on reading month files the manifest no longer selects:
    a month whose window changed, or a coin whose datasets were removed. They
    are dropped instead, so a query fails loudly rather than reading stale
    rows. The next run that selects a file creates the view again.
    """

    emitted = {name for name, _statement in views}
    return tuple(name for name in sorted(owned - emitted) if _FUNDING_VIEW.fullmatch(name))


def merge_catalog(
    existing: str,
    views: Sequence[tuple[str, str]],
    *,
    replace_legacy: bool,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Return merged SQL, emitted view names, and names left untouched."""

    without_block = _BLOCK.sub("", existing)
    outside = {match.group(1) for match in _VIEW_DECL.finditer(without_block)}
    kept: list[tuple[str, str]] = []
    skipped: list[str] = []
    for name, statement in views:
        if name in outside and not replace_legacy:
            skipped.append(name)
            continue
        kept.append((name, statement))
    if replace_legacy:
        without_block = _strip_views(without_block, {name for name, _statement in kept})
    statements = "".join(statement for _name, statement in kept)
    body = (
        f"{_BEGIN}\n"
        "-- Generated by python -m research.hist_etl. Do not edit inside this block.\n"
        f"{statements if statements else '-- No pipeline parquet files yet.\n'}"
        f"{_END}\n"
    )
    prefix = without_block.rstrip()
    merged = prefix + "\n\n" + body if prefix else body
    names = tuple(name for name, _statement in kept)
    return merged, names, tuple(skipped)


def _preserved_statements(existing: str, emitted: set[str], live: set[str]) -> str:
    """Keep prior pipeline views that this run is not replacing.

    A missing sidecar must not drop the view from the block. If it did, the next
    run would treat the live relation as foreign and refuse to refresh it.
    """

    match = _BLOCK.search(existing)
    if match is None:
        return ""
    chunks: list[str] = []
    for statement in _statements(match.group(0)):
        found = _VIEW_DECL.search(statement)
        if found is None:
            continue
        name = found.group(1)
        if name in emitted or name not in live:
            continue
        text = statement.strip()
        if not text.endswith(";"):
            text += ";"
        chunks.append(text + "\n")
    return "".join(chunks)


def _managed_names(existing: str) -> set[str]:
    match = _BLOCK.search(existing)
    if match is None:
        return set()
    return {found.group(1) for found in _VIEW_DECL.finditer(match.group(0))}


def _live_relations(root: Path) -> set[str]:
    """User view and table names in the main schema. System objects are omitted."""

    connection = _connect(root / "research.duckdb")
    try:
        rows = connection.execute(
            """
            SELECT view_name FROM duckdb_views()
            WHERE schema_name = 'main' AND NOT internal
            UNION ALL
            SELECT table_name FROM duckdb_tables()
            WHERE schema_name = 'main' AND NOT internal
            """
        ).fetchall()
    finally:
        connection.close()
    return {str(row[0]) for row in rows if row[0] is not None}


def apply_catalog(root: Path, catalog_sql: str, *, drop: Sequence[str] = ()) -> None:
    """Run the block, then drop the generated views in ``drop``, on one connection."""

    match = _BLOCK.search(catalog_sql)
    if match is None:
        raise HistEtlError("catalog.sql is missing the hist_etl block", exit_code=2)
    rendered = match.group(0).replace("__HIST__", _sql_root(root))
    database = root / "research.duckdb"
    connection = _connect(database)
    try:
        connection.execute("SET TimeZone='UTC'")
        for statement in _statements(rendered):
            connection.execute(statement)
        _drop_views(connection, drop)
    finally:
        connection.close()


def _drop_views(connection: duckdb.DuckDBPyConnection, names: Sequence[str]) -> None:
    if not names:
        return
    rows = connection.execute(
        "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main' AND NOT internal"
    ).fetchall()
    live_views = {str(row[0]) for row in rows}
    for name in names:
        if not _VIEW_NAME.fullmatch(name):
            raise HistEtlError(f"unsafe view name {name}", exit_code=2)
        if name in live_views:
            warn(f"dropping view {name}: no funding month file of the manifest selects it")
            connection.execute(f"DROP VIEW {name}")


def _connect(database: Path) -> duckdb.DuckDBPyConnection:
    delay = 0.2
    last: BaseException | None = None
    for attempt in range(1, _LOCK_ATTEMPTS + 1):
        try:
            return duckdb.connect(str(database))
        except duckdb.Error as exc:
            if "lock" not in str(exc).lower():
                raise
            last = exc
            if attempt == _LOCK_ATTEMPTS:
                break
            time.sleep(delay)
            delay *= 2
    raise HistEtlError(
        "research.duckdb is locked by another process. Close that DuckDB session and retry. "
        f"Last error: {last}",
        exit_code=2,
    ) from last


def _binance_files(directory: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for path in sorted(directory.glob("*.parquet")):
        match = _BINANCE_FILE.fullmatch(path.name)
        sidecar = path.with_name(path.name + ".sources.json")
        if match is None or not sidecar.is_file():
            continue
        found.setdefault(match.group(1), []).append(path)
    return found


def _kraken_files(directory: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in sorted(directory.glob("*.parquet")):
        sidecar = path.with_name(path.name + ".sources.json")
        if _KRAKEN_FILE.fullmatch(path.name) is None or not sidecar.is_file():
            continue
        files.append(path)
    return tuple(files)


def _binance_names(market: str, symbol: str, slug: str, *, replace_legacy: bool) -> tuple[str, ...]:
    primary = f"hist_bn_{market}_{symbol.lower()}_{slug}"
    if replace_legacy and symbol == "BTCUSDT":
        return (primary, f"hist_bn_{market}_{slug}")
    return (primary,)


def _kraken_names(pair: str, interval: str, *, replace_legacy: bool) -> tuple[str, ...]:
    primary = f"hist_kr_ohlcvt_{pair.lower()}_{interval}"
    if replace_legacy:
        return (primary, f"hist_kr_{pair.lower()}_{interval}")
    return (primary,)


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise HistEtlError(
            f"parquet path is outside the archive root: {path}", exit_code=2
        ) from exc
    if "'" in relative or ".." in relative.split("/"):
        raise HistEtlError(f"unsafe parquet path {relative}", exit_code=2)
    return relative


def _view(name: str, relative_paths: tuple[str, ...]) -> str:
    if not _VIEW_NAME.fullmatch(name):
        raise HistEtlError(f"unsafe view name {name}", exit_code=2)
    if not relative_paths:
        raise HistEtlError(f"{name} has no parquet files", exit_code=2)
    listed = ",\n".join(f"    '__HIST__/{path}'" for path in relative_paths)
    return f"CREATE OR REPLACE VIEW {name} AS\nSELECT * FROM read_parquet([\n{listed}\n]);\n"


def _strip_views(sql: str, names: set[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group(1) in names:
            return ""
        return match.group(0)

    return _VIEW_STMT.sub(replace, sql)


def _statements(block: str) -> list[str]:
    statements: list[str] = []
    for chunk in block.split(";"):
        lines = [
            line
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        statement = "\n".join(lines).strip()
        if statement:
            statements.append(statement)
    return statements


def _sql_root(root: Path) -> str:
    rendered = root.resolve().as_posix()
    if "'" in rendered or "\n" in rendered:
        raise HistEtlError("archive root cannot contain a single quote", exit_code=2)
    return rendered
