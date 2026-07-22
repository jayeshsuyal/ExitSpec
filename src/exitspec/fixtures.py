"""Loading and deterministic expansion of synthetic workload fixture specifications."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List, Tuple

from pydantic import Field, field_validator

from .models import ExitSpecModel, ToolSelectionFixtureCase


class ToolSelectionFixtureSpec(ExitSpecModel):
    fixture_id: str = Field(min_length=1)
    synthetic: bool
    description: str = Field(min_length=1)
    case_count: int = Field(gt=0)
    prompt_template: str = Field(min_length=1)
    expected_tool_cycle: List[str] = Field(min_length=1)

    @field_validator("expected_tool_cycle")
    @classmethod
    def tools_must_be_distinct(cls, tools: List[str]) -> List[str]:
        if len(tools) != len(set(tools)):
            raise ValueError("expected_tool_cycle entries must be distinct.")
        return tools


def fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tool_selection_fixture(
    path: Path,
) -> Tuple[ToolSelectionFixtureSpec, List[ToolSelectionFixtureCase]]:
    """Load a compact fixture specification and expand its fixed synthetic cases."""

    spec = ToolSelectionFixtureSpec.model_validate(json.loads(path.read_text("utf-8")))
    cases: List[ToolSelectionFixtureCase] = []
    for index in range(spec.case_count):
        case_number = index + 1
        expected_tool = spec.expected_tool_cycle[index % len(spec.expected_tool_cycle)]
        cases.append(
            ToolSelectionFixtureCase(
                case_id="support-case-{0:03d}".format(case_number),
                prompt=spec.prompt_template.format(case_number=case_number),
                expected_tool=expected_tool,
            )
        )
    return spec, cases
