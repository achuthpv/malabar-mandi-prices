"""Dev tool: list distinct district/market/commodity names the OGD feed
actually reports for the configured state(s).

Run this BEFORE editing ogd_names in config/sources.yaml — the feed's
spellings are surprising ("Keralam", "Kozhikode(Calicut)", "Ungrabled").
"""

from __future__ import annotations

from collections import Counter

from .config import Config
from .fetch import fetch_all


def discover(cfg: Config) -> str:
    records = fetch_all(cfg)
    districts: Counter[str] = Counter()
    commodities: Counter[str] = Counter()
    markets: Counter[tuple[str, str]] = Counter()

    for r in records:
        d = str(r.get("district", "")).strip()
        c = str(r.get("commodity", "")).strip()
        districts[d] += 1
        commodities[c] += 1
        markets[(d, str(r.get("market", "")).strip())] += 1

    lines = [f"Total records for {', '.join(cfg.state_names)}: {len(records)}", ""]
    lines.append("Districts (record count today):")
    for name, n in districts.most_common():
        mark = " *" if name.lower() in cfg.district_by_ogd_name else ""
        lines.append(f"  {n:5d}  {name}{mark}")
    lines.append("")
    lines.append("Commodities (record count today, * = mapped in config):")
    for name, n in commodities.most_common():
        mark = " *" if name.lower() in cfg.commodity_by_ogd_name else ""
        lines.append(f"  {n:5d}  {name}{mark}")
    lines.append("")
    lines.append("Markets in configured districts:")
    for (d, m), n in sorted(markets.items()):
        if d.lower() in cfg.district_by_ogd_name:
            lines.append(f"  {n:5d}  {d} / {m}")
    return "\n".join(lines)
