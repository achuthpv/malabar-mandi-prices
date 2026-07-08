"""CSV partition store with keyed upsert.

Layout: data/prices/{commodity_slug}/{year}.csv
Natural key: (date, market, commodity_slug, variety, grade)

Conflict rules on upsert:
  - source "ogd" beats source "ceda" (official daily feed wins over archive)
  - same source: latest fetched_at wins

Files are written sorted by natural key, so re-running the pipeline on the
same inputs produces byte-identical files (idempotent; `git diff --quiet`
then makes the commit step a no-op).
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import PRICES_DIR, QUARANTINE_DIR
from .normalize import COLUMNS

Key = tuple[str, str, str, str, str]

SOURCE_RANK = {"ceda": 0, "ogd": 1}

QUARANTINE_COLUMNS = ["quarantined_at", "reason", "source", "record"]


def natural_key(row: dict[str, Any]) -> Key:
    return (
        str(row["date"]),
        str(row["market"]),
        str(row["commodity_slug"]),
        str(row["variety"]),
        str(row["grade"]),
    )


def _partition_path(slug: str, year: str, base: Path) -> Path:
    return base / slug / f"{year}.csv"


def read_partition(path: Path) -> dict[Key, dict[str, Any]]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {natural_key(row): dict(row) for row in csv.DictReader(f)}


def read_all_rows(base: Path | None = None) -> list[dict[str, Any]]:
    """Read every stored row across all partitions (for analysis/publish)."""
    base = base or PRICES_DIR
    rows: list[dict[str, Any]] = []
    for path in sorted(base.glob("*/*.csv")):
        rows.extend(read_partition(path).values())
    return rows


def _wins(new: dict[str, Any], old: dict[str, Any]) -> bool:
    new_rank = SOURCE_RANK.get(str(new.get("source")), -1)
    old_rank = SOURCE_RANK.get(str(old.get("source")), -1)
    if new_rank != old_rank:
        return new_rank > old_rank
    return str(new.get("fetched_at", "")) >= str(old.get("fetched_at", ""))


def upsert_rows(rows: Iterable[dict[str, Any]], base: Path | None = None) -> dict[str, int]:
    """Merge rows into their partitions. Returns {partition: changed_row_count}."""
    base = base or PRICES_DIR
    # Group incoming rows by partition
    by_partition: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        year = str(row["date"])[:4]
        by_partition.setdefault((str(row["commodity_slug"]), year), []).append(row)

    changed: dict[str, int] = {}
    for (slug, year), new_rows in sorted(by_partition.items()):
        path = _partition_path(slug, year, base)
        existing = read_partition(path)
        n_changed = 0
        for row in new_rows:
            key = natural_key(row)
            old = existing.get(key)
            if old is not None and not _wins(row, old):
                continue
            normalized = {c: str(row[c]) for c in COLUMNS}
            if old != normalized:
                existing[key] = normalized
                n_changed += 1
        if n_changed:
            _write_partition(path, existing)
            changed[str(path.relative_to(base))] = n_changed
    return changed


def _write_partition(path: Path, rows_by_key: dict[Key, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(rows_by_key):
            writer.writerow(rows_by_key[key])


def write_quarantine(
    quarantined: list[dict[str, Any]], base: Path | None = None, now: str | None = None
) -> Path | None:
    """Append rejected rows (with reasons) to a monthly quarantine file."""
    if not quarantined:
        return None
    base = base or QUARANTINE_DIR
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = base / f"{now[:7]}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=QUARANTINE_COLUMNS, lineterminator="\n")
        if is_new:
            writer.writeheader()
        for q in quarantined:
            q = dict(q)
            writer.writerow(
                {
                    "quarantined_at": now,
                    "reason": q.pop("reason", "unknown"),
                    "source": q.pop("source", "unknown"),
                    "record": repr(q),
                }
            )
    return path
