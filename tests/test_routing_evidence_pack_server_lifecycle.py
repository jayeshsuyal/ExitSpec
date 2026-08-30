from __future__ import annotations

from pathlib import Path

import pytest

from exitspec.routing_evidence_pack import (
    RoutingEvidencePackError,
    verify_routing_evidence_pack,
)
from exitspec.web import serve_demo


def test_serve_demo_reuses_verified_pack_after_restart(tmp_path: Path):
    first = serve_demo(port=0, output_root=tmp_path)
    try:
        first_publication = first.routing_evidence_pack
        assert first_publication is not None
        first_pack_id = first_publication.pack_id
    finally:
        first.server_close()

    second = serve_demo(port=0, output_root=tmp_path)
    try:
        second_publication = second.routing_evidence_pack
        assert second_publication == first_publication
        assert second_publication == verify_routing_evidence_pack(
            tmp_path, first_pack_id
        )
    finally:
        second.server_close()


def test_serve_demo_restart_rejects_tampered_existing_pack(tmp_path: Path):
    first = serve_demo(port=0, output_root=tmp_path)
    try:
        publication = first.routing_evidence_pack
        assert publication is not None
        decision_packet = tmp_path / publication.pack_id / "decision-packet.html"
    finally:
        first.server_close()

    original = decision_packet.read_bytes()
    decision_packet.write_bytes(original + b"\n<!-- tampered -->\n")

    with pytest.raises(RoutingEvidencePackError):
        serve_demo(port=0, output_root=tmp_path)
    assert decision_packet.read_bytes().endswith(b"<!-- tampered -->\n")
