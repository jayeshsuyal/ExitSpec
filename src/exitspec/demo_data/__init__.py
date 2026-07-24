"""Bundled, synthetic inputs for ExitSpec's deterministic demonstration.

Package resources are not guaranteed to be ordinary files. Callers therefore use
``support_agent_demo_paths`` as a context manager and keep all path consumers inside
that context. This works for normal wheels and for importers that extract resources
to a temporary directory.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class SupportAgentDemoPaths:
    """Filesystem paths materialized for one support-agent demo operation."""

    root: Path
    discovery_pack: Path
    review_plan: Path
    contract_seed: Path
    frozen_contract: Path
    fixture: Path

    @classmethod
    def from_root(cls, root: Path) -> "SupportAgentDemoPaths":
        """Build and validate the fixed resource map below ``root``."""

        resolved = cls(
            root=root,
            discovery_pack=root / "authoring" / "discovery-pack-v1.json",
            review_plan=root / "authoring" / "review-plan-v1.json",
            contract_seed=root / "authoring" / "contract-seed-v1.json",
            frozen_contract=root
            / "contracts"
            / "tool-selection-v1.frozen.yaml",
            fixture=root / "fixtures" / "tool-selection-200.json",
        )
        missing = [
            str(path.relative_to(root))
            for path in (
                resolved.discovery_pack,
                resolved.review_plan,
                resolved.contract_seed,
                resolved.frozen_contract,
                resolved.fixture,
            )
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(
                "ExitSpec's bundled support-agent demo is incomplete: {0}".format(
                    ", ".join(missing)
                )
            )
        return resolved


@contextmanager
def support_agent_demo_paths() -> Iterator[SupportAgentDemoPaths]:
    """Materialize every bundled support-agent input for the context lifetime."""

    resource_root = files(__package__).joinpath("support_agent")
    with as_file(resource_root) as materialized_root:
        yield SupportAgentDemoPaths.from_root(materialized_root)


__all__ = ["SupportAgentDemoPaths", "support_agent_demo_paths"]
