from pathlib import Path

import pytest

from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "examples/support-agent/contracts/tool-selection-v1.yaml"
FIXTURE_PATH = PROJECT_ROOT / "examples/support-agent/fixtures/tool-selection-200.json"


@pytest.fixture
def approved_contract():
    return load_contract(CONTRACT_PATH)


@pytest.fixture
def fixture_path():
    return FIXTURE_PATH
