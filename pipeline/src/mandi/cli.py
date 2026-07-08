"""Command line interface: python -m mandi {fetch,analyze,publish,backfill,discover}."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from .config import load_config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cmd_fetch(_args: argparse.Namespace) -> int:
    from .fetch import fetch_all
    from .normalize import normalize_records
    from .store import upsert_rows, write_quarantine

    cfg = load_config()
    fetched_at = _now_iso()
    raw = fetch_all(cfg)
    rows, quarantined = normalize_records(cfg, raw, source="ogd", fetched_at=fetched_at)
    changed = upsert_rows(rows)
    qpath = write_quarantine(quarantined, now=fetched_at)

    print(f"raw records: {len(raw)}")
    print(f"in-scope rows: {len(rows)}, quarantined: {len(quarantined)}")
    for partition, n in changed.items():
        print(f"changed {partition}: {n} rows")
    if not changed:
        print("no data changes")
    if qpath:
        print(f"quarantine appended: {qpath}")
    return 0


def cmd_discover(_args: argparse.Namespace) -> int:
    from .discover import discover

    print(discover(load_config()))
    return 0


def cmd_analyze(_args: argparse.Namespace) -> int:
    from .analyze import analyze_all, write_analysis

    cfg = load_config()
    results = analyze_all(cfg)
    path = write_analysis(results)
    print(f"analysis written: {path}")
    return 0


def cmd_publish(_args: argparse.Namespace) -> int:
    from .publish import publish_all

    cfg = load_config()
    written = publish_all(cfg, generated_at=_now_iso())
    for p in written:
        print(f"wrote {p}")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    from .backfill_ceda import backfill

    cfg = load_config()
    return backfill(cfg, years=args.years, dry_run=args.dry_run)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="mandi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="fetch current OGD data and upsert into data/").set_defaults(
        func=cmd_fetch
    )
    sub.add_parser("discover", help="list names the OGD feed reports").set_defaults(
        func=cmd_discover
    )
    sub.add_parser("analyze", help="compute seasonality/trends from data/").set_defaults(
        func=cmd_analyze
    )
    sub.add_parser("publish", help="generate site/api/v1 JSON").set_defaults(func=cmd_publish)

    p_back = sub.add_parser("backfill", help="one-time historical backfill (CEDA)")
    p_back.add_argument("--years", type=int, default=5)
    p_back.add_argument("--dry-run", action="store_true")
    p_back.set_defaults(func=cmd_backfill)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
