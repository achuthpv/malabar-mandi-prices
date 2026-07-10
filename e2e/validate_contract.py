#!/usr/bin/env python3
"""Validate site/openapi.json and contract-test the published API files.

1. The OpenAPI document itself must be valid (openapi-spec-validator).
2. Every generated site/api file must validate against the response schema
   its path maps to — so the spec and reality cannot drift.
3. No secrets: published JSON must not contain anything that looks like an
   API key.

Run after `python -m mandi publish`. Exits non-zero on any violation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import jsonschema
from openapi_spec_validator import validate as validate_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "site"
API = SITE / "api" / "v1"

# path template -> regex for generated files
TEMPLATES = {
    "/api/v1/index.json": r"^index\.json$",
    "/api/v1/meta.json": r"^meta\.json$",
    "/api/v1/commodities.json": r"^commodities\.json$",
    "/api/v1/markets.json": r"^markets\.json$",
    "/api/v1/prices/{slug}/latest.json": r"^prices/[a-z0-9-]+/latest\.json$",
    "/api/v1/prices/{slug}/daily/{year}.json": r"^prices/[a-z0-9-]+/daily/\d{4}\.json$",
    "/api/v1/prices/{slug}/monthly.json": r"^prices/[a-z0-9-]+/monthly\.json$",
    "/api/v1/analysis/{slug}/seasonality.json": r"^analysis/[a-z0-9-]+/seasonality\.json$",
    "/api/v1/analysis/{slug}/summary.json": r"^analysis/[a-z0-9-]+/summary\.json$",
    "/api/v1/news/{slug}.json": r"^news/[a-z0-9-]+\.json$",
}

# data.gov.in keys are 56+ hex chars; also catch obvious assignments
SECRET_PATTERNS = [
    re.compile(r"\b[0-9a-f]{56,}\b", re.IGNORECASE),
    re.compile(r"api[-_]?key\s*[=:]\s*['\"]?[0-9a-z]{20,}", re.IGNORECASE),
]


def main() -> int:
    with open(SITE / "openapi.json", encoding="utf-8") as f:
        spec = json.load(f)

    validate_spec(spec)
    print("openapi.json: valid OpenAPI 3.1")

    if not API.exists():
        print("ERROR: site/api/v1 missing — run `python -m mandi publish` first",
              file=sys.stderr)
        return 1

    failures = 0
    files = sorted(p for p in API.rglob("*.json"))
    if not files:
        print("ERROR: no generated API files found", file=sys.stderr)
        return 1

    for path in files:
        rel = str(path.relative_to(API)).replace("\\", "/")
        template = next(
            (t for t, rx in TEMPLATES.items() if re.match(rx, rel)), None)
        if template is None:
            print(f"FAIL {rel}: no matching path template in openapi.json")
            failures += 1
            continue

        schema = (spec["paths"][template]["get"]["responses"]["200"]
                  ["content"]["application/json"]["schema"])
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        doc = json.loads(raw)

        try:
            # inlining components at the root lets "#/components/..." refs
            # resolve within the same document — no external resolver needed
            jsonschema.validate(doc, {**schema, "components": spec["components"]})
        except jsonschema.ValidationError as e:
            print(f"FAIL {rel}: {e.message} (at {'/'.join(map(str, e.absolute_path))})")
            failures += 1
            continue

        for rx in SECRET_PATTERNS:
            if rx.search(raw):
                print(f"FAIL {rel}: contains something that looks like a secret")
                failures += 1
                break
        else:
            print(f"ok   {rel}")

    if failures:
        print(f"\n{failures} contract violation(s)", file=sys.stderr)
        return 1
    print(f"\nall {len(files)} API files match the spec; no secrets found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
