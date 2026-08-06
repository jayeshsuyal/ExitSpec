"""Typed read-only projection for verified POC Evidence Packs.

The library is navigation, not a second artifact registry or verdict engine.
Callers must supply bindings resolved by the existing terminal-evidence
authority. This module only validates and presents those immutable identities.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Tuple

from pydantic import Field, field_validator, model_validator

from .models import FrozenExitSpecModel, SHA256_PATTERN, VerdictStatus
from .workspace_closure import POC_ID_PATTERN


MAX_LIBRARY_PACKS = 2_048


class EvidencePackHandoffState(StrEnum):
    READY_FOR_HANDOFF = "READY_FOR_HANDOFF"
    HANDOFF_COMPLETED = "HANDOFF_COMPLETED"
    POC_STOPPED = "POC_STOPPED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    HISTORICAL = "HISTORICAL"


class EvidencePackLibraryItem(FrozenExitSpecModel):
    """One authoritative, immutable run-scoped Evidence Pack identity."""

    poc_id: str = Field(pattern=POC_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=160)
    customer_label: str = Field(min_length=1, max_length=160)
    contract_id: str = Field(min_length=1, max_length=160)
    contract_version: str = Field(min_length=1, max_length=100)
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    verdict: VerdictStatus
    evidence_pack_url: str = Field(min_length=1, max_length=500)
    evidence_pack_sha256: str = Field(pattern=SHA256_PATTERN)
    handoff_state: EvidencePackHandoffState
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def require_timezone_aware_updated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evidence Pack timestamps must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def require_local_decision_packet(self) -> "EvidencePackLibraryItem":
        url = self.evidence_pack_url
        if (
            not url.startswith("/artifacts/")
            or not url.endswith("/decision-packet.html")
            or "\\" in url
            or "?" in url
            or "#" in url
            or any(part in {"", ".", ".."} for part in url.split("/")[2:])
        ):
            raise ValueError(
                "Evidence Pack URL must identify one local decision packet."
            )
        return self


class EvidencePackLibraryProjection(FrozenExitSpecModel):
    """Bounded product surface with no artifact or decision mutation methods."""

    schema_version: Literal["exitspec.evidence-pack-library.v1"] = (
        "exitspec.evidence-pack-library.v1"
    )
    packs: Tuple[EvidencePackLibraryItem, ...] = Field(
        default_factory=tuple,
        max_length=MAX_LIBRARY_PACKS,
    )
    authorization: Literal[
        "Evidence is proof, not shipping authorization."
    ] = "Evidence is proof, not shipping authorization."

    @model_validator(mode="after")
    def require_unique_pack_identities(
        self,
    ) -> "EvidencePackLibraryProjection":
        identities = tuple(
            (item.poc_id, item.run_id, item.evidence_pack_url)
            for item in self.packs
        )
        if len(identities) != len(set(identities)):
            raise ValueError("Evidence Pack library identities must be unique.")
        return self


__all__ = [
    "EvidencePackHandoffState",
    "EvidencePackLibraryItem",
    "EvidencePackLibraryProjection",
    "MAX_LIBRARY_PACKS",
]
