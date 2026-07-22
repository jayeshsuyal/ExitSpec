"""A deterministic local adapter used to prove ExitSpec's first evidence chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..models import ToolSelectionEvidence, ToolSelectionFixtureCase


@dataclass(frozen=True)
class AdapterExecution:
    sample_count: int
    success_count: int
    records: List[ToolSelectionEvidence]
    external_blocked_reason: Optional[str] = None
    internal_error: Optional[str] = None


class DeterministicToolSelectionAdapter:
    """Simulate deterministic success and failure profiles over a fixed fixture."""

    name = "deterministic_tool_selection"
    version = "1.0.0"

    _SCENARIOS: Dict[str, Dict[str, object]] = {
        "insufficient": {"sample_count": 100, "success_count": 100},
        "pass": {"sample_count": 200, "success_count": 197},
        "inconclusive": {"sample_count": 200, "success_count": 196},
        "fail": {"sample_count": 200, "success_count": 189},
        "blocked": {
            "sample_count": 0,
            "success_count": 0,
            "external_blocked_reason": "Target endpoint credentials were unavailable.",
        },
        "internal-error": {
            "sample_count": 0,
            "success_count": 0,
            "internal_error": "Simulated response parser crash.",
        },
    }

    @property
    def scenarios(self) -> List[str]:
        return list(self._SCENARIOS)

    def execute(
        self, fixture: List[ToolSelectionFixtureCase], scenario: str
    ) -> AdapterExecution:
        if scenario not in self._SCENARIOS:
            raise ValueError("Unknown deterministic scenario: {0}".format(scenario))
        profile = self._SCENARIOS[scenario]
        sample_count = int(profile["sample_count"])
        success_count = int(profile["success_count"])

        if sample_count > len(fixture):
            raise ValueError("Fixture does not contain enough cases for the scenario.")
        if success_count > sample_count:
            raise ValueError("Scenario success_count cannot exceed sample_count.")

        records: List[ToolSelectionEvidence] = []
        for index, case in enumerate(fixture[:sample_count]):
            is_exact_match = index < success_count
            records.append(
                ToolSelectionEvidence(
                    case_id=case.case_id,
                    expected_tool=case.expected_tool,
                    actual_tool=(
                        case.expected_tool
                        if is_exact_match
                        else self._different_tool(case.expected_tool)
                    ),
                    is_exact_match=is_exact_match,
                )
            )

        return AdapterExecution(
            sample_count=sample_count,
            success_count=success_count,
            records=records,
            external_blocked_reason=profile.get("external_blocked_reason"),
            internal_error=profile.get("internal_error"),
        )

    @staticmethod
    def _different_tool(expected_tool: str) -> str:
        alternatives = (
            "lookup_ticket",
            "update_ticket",
            "search_knowledge_base",
            "escalate_to_human",
        )
        for candidate in alternatives:
            if candidate != expected_tool:
                return candidate
        raise RuntimeError("No alternative tool is available.")
