"""Price and arbitrage alerts, evaluated after each daily fetch.

Rules live in config/alerts.yaml. Alerts are EDGE-TRIGGERED: a rule fires
when its condition flips from false to true and stays silent while the
condition persists; when it clears, the rule re-arms. Firing state is kept
in data/alerts_state.json (committed by the cron, so state survives runs).

`python -m mandi alerts` prints triggered alert lines to stdout and writes
them to build/alerts.txt; the daily workflow opens/updates a GitHub issue
with that text when the file is non-empty. Exit code is always 0 — alerts
are information, not failures.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import yaml

from .analyze import BUILD_DIR, analyze_all
from .config import DATA_DIR, REPO_ROOT, Config
from .publish import _spread  # shared comparability logic
from .store import read_all_rows

log = logging.getLogger(__name__)

ALERTS_CONFIG = REPO_ROOT / "config" / "alerts.yaml"
STATE_PATH = DATA_DIR / "alerts_state.json"
OUTPUT_PATH = BUILD_DIR / "alerts.txt"


def _load_rules() -> list[dict[str, Any]]:
    if not ALERTS_CONFIG.exists():
        return []
    with open(ALERTS_CONFIG, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return list(raw.get("rules") or [])


def _load_state() -> dict[str, bool]:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return {str(k): bool(v) for k, v in json.load(f).items()}
    return {}


def _save_state(state: dict[str, bool]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
        f.write("\n")


def _rule_key(rule: dict[str, Any]) -> str:
    parts = [str(rule.get(k, "")) for k in
             ("type", "commodity", "district", "threshold", "pct")]
    return ":".join(parts)


def _view(analysis: dict[str, Any], commodity: str,
          district: str | None) -> dict[str, Any] | None:
    c = analysis.get("commodities", {}).get(commodity)
    if not c:
        return None
    if district:
        return (c.get("districts") or {}).get(district)
    return c.get("region")


def evaluate(cfg: Config, analysis: dict[str, Any],
             rows: list[dict[str, Any]],
             rules: list[dict[str, Any]]) -> list[tuple[str, bool, str]]:
    """Return [(rule_key, condition_true, message)] for every rule."""
    # spreads per commodity from the freshest rows (same logic as the API)
    spreads: dict[str, dict[str, Any] | None] = {}
    for c in cfg.commodities:
        crows = [r for r in rows if r["commodity_slug"] == c.slug]
        latest_by_market: dict[tuple, dict[str, Any]] = {}
        for r in sorted(crows, key=lambda r: r["date"]):
            latest_by_market[(r["district"], r["market"], r["variety"])] = r
        spreads[c.slug] = _spread(latest_by_market)

    results: list[tuple[str, bool, str]] = []
    for rule in rules:
        rtype = str(rule.get("type", ""))
        slug = str(rule.get("commodity", ""))
        district = rule.get("district")
        key = _rule_key(rule)
        try:
            display = cfg.commodity(slug).display
        except KeyError:
            log.warning("alert rule for unknown commodity %r skipped", slug)
            continue
        where = district or "region"
        view = _view(analysis, slug, district)

        fired, msg = False, ""
        if rtype in ("price_above", "price_below"):
            threshold = int(rule.get("threshold", 0))
            ma30 = ((view or {}).get("trend") or {}).get("ma30")
            if ma30 is not None:
                if rtype == "price_above" and ma30 > threshold:
                    fired, msg = True, (f"{display} ({where}): 30-day median "
                                        f"Rs{ma30:,} is ABOVE Rs{threshold:,}")
                if rtype == "price_below" and ma30 < threshold:
                    fired, msg = True, (f"{display} ({where}): 30-day median "
                                        f"Rs{ma30:,} is BELOW Rs{threshold:,}")
        elif rtype == "spread_above":
            pct = float(rule.get("pct", 0))
            s = spreads.get(slug)
            if s and s["spread_pct"] > pct:
                fired, msg = True, (
                    f"{display}: cross-market gap {s['spread_pct']}% "
                    f"(> {pct}%) — {s['high']['market']} Rs{s['high']['modal_price']:,} "
                    f"vs {s['low']['market']} Rs{s['low']['modal_price']:,}")
        elif rtype in ("vs_typical_above", "vs_typical_below"):
            pct = float(rule.get("pct", 0))
            signal = (view or {}).get("signal") or {}
            dev = signal.get("vs_typical_pct")
            if dev is not None:
                if rtype == "vs_typical_above" and dev > pct:
                    fired, msg = True, (f"{display} ({where}): {dev}% ABOVE "
                                        f"the seasonal norm for {signal.get('month')}")
                if rtype == "vs_typical_below" and dev < -pct:
                    fired, msg = True, (f"{display} ({where}): {abs(dev)}% BELOW "
                                        f"the seasonal norm for {signal.get('month')}")
        else:
            log.warning("unknown alert rule type %r skipped", rtype)
            continue

        results.append((key, fired, msg))
    return results


def run(cfg: Config, today: date | None = None) -> int:
    rules = _load_rules()
    if not rules:
        print("alerts: no rules configured")
        return 0

    rows = read_all_rows()
    analysis = analyze_all(cfg, today=today)
    state = _load_state()

    results = evaluate(cfg, analysis, rows, rules)
    new_alerts: list[str] = []
    new_state: dict[str, bool] = {}
    for key, fired, msg in results:
        new_state[key] = fired
        if fired and not state.get(key, False):  # edge: false -> true
            new_alerts.append(msg)

    _save_state(new_state)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(new_alerts) + ("\n" if new_alerts else ""))

    active = sum(1 for _, fired, _ in results if fired)
    print(f"alerts: {len(new_alerts)} new, {active} active, "
          f"{len(results)} rules evaluated")
    for msg in new_alerts:
        print("ALERT:", msg)
    return 0
