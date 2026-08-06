from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from exitspec.evidence_pack_library import (
    EvidencePackHandoffState,
    EvidencePackLibraryItem,
    EvidencePackLibraryProjection,
)
from exitspec.models import VerdictStatus


NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _item(**changes) -> EvidencePackLibraryItem:
    payload = {
        "poc_id": "poc_evidence_library_demo",
        "display_name": "Inference latency POC",
        "customer_label": "Northstar",
        "contract_id": "northstar-inference-poc",
        "contract_version": "1",
        "contract_hash": "a" * 64,
        "run_id": "run_" + "b" * 32,
        "verdict": VerdictStatus.PASS,
        "evidence_pack_url": (
            "/artifacts/run_{0}/decision-packet.html".format("b" * 32)
        ),
        "evidence_pack_sha256": "c" * 64,
        "handoff_state": EvidencePackHandoffState.READY_FOR_HANDOFF,
        "updated_at": NOW,
    }
    payload.update(changes)
    return EvidencePackLibraryItem(**payload)


def test_projection_is_bounded_immutable_and_authority_free():
    item = _item()
    projection = EvidencePackLibraryProjection(packs=(item,))

    assert projection.schema_version == "exitspec.evidence-pack-library.v1"
    assert projection.authorization == (
        "Evidence is proof, not shipping authorization."
    )
    assert projection.packs == (item,)
    assert not hasattr(projection, "approve")
    assert not hasattr(projection, "freeze")
    assert not hasattr(projection, "verdict")
    with pytest.raises(ValidationError):
        projection.packs = ()


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://example.com/decision-packet.html",
        "/artifacts/../decision-packet.html",
        "/artifacts/run_x/decision-packet.html?download=1",
        "/artifacts/run_x/not-the-pack.html",
    ),
)
def test_item_rejects_nonlocal_or_ambiguous_pack_urls(unsafe_url):
    with pytest.raises(ValidationError, match="local decision packet"):
        _item(evidence_pack_url=unsafe_url)


def test_item_requires_timezone_aware_timestamp_and_exact_hashes():
    with pytest.raises(ValidationError, match="timezone-aware"):
        _item(updated_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        _item(evidence_pack_sha256="not-a-hash")


def test_projection_rejects_duplicate_pack_identities():
    item = _item()
    with pytest.raises(ValidationError, match="identities must be unique"):
        EvidencePackLibraryProjection(packs=(item, item))
