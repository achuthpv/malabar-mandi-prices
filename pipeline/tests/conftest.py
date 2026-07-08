import json
from pathlib import Path

import pytest

from mandi.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture()
def ogd_records():
    with open(FIXTURES / "ogd_records.json", encoding="utf-8") as f:
        return json.load(f)
