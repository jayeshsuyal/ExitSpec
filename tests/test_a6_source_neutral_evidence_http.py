from contextlib import contextmanager
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

from exitspec.poc_creation import DraftPOCCreateRequest
from exitspec.poc_source_demo import SourceNeutralPOCDemoServer
from tests.test_a6_executable_orchestration import (
    _SECRET_CONFIRMATION_IDEMPOTENCY_KEY,
    _secret_confirmation_pack_service,
    _pack_service,
)


@contextmanager
def _running_source_server(tmp_path: Path):
    server = SourceNeutralPOCDemoServer(
        ("127.0.0.1", 0),
        evidence_artifact_root=(tmp_path / "artifacts").resolve(),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        assert not worker.is_alive()
        server.server_close()


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def test_source_neutral_artifact_route_verifies_captured_packet_before_serving(tmp_path):
    service = _pack_service(tmp_path)
    with _running_source_server(tmp_path) as (server, base_url):
        server.generic_evidence_service = service
        server.evidence_artifact_root = service._output_root
        attempt = service.start(
            "poc_a6_executable_test",
            acknowledgement=True,
            idempotency_key="a6-source-http-packet",
        ).attempt
        packet_url = f"{base_url}{attempt.evidence_pack_url}"

        status, body = _get(packet_url)
        assert status == 200
        assert b"ExitSpec Evidence Pack" in body

        assert _get(f"{base_url}/artifacts/{attempt.attempt_id}/evidence.json")[0] == 404
        assert _get(f"{base_url}/artifacts/{attempt.attempt_id}/../contract.json")[0] == 404

        packet = service._output_root / attempt.attempt_id / "decision-packet.html"
        original_verify = service.verify_evidence_pack_publication

        def verify_then_same_size_tamper(attempt_id):
            publication = original_verify(attempt_id)
            original = packet.read_bytes()
            replacement = (b"X" * len(original)) if original else b"X"
            packet.write_bytes(replacement)
            return publication

        service.verify_evidence_pack_publication = verify_then_same_size_tamper
        status, _ = _get(packet_url)
        assert status == 404


def test_source_neutral_artifact_route_redacts_confirmation_idempotency_key(tmp_path):
    service = _secret_confirmation_pack_service(tmp_path)
    with _running_source_server(tmp_path) as (server, base_url):
        server.generic_evidence_service = service
        server.evidence_artifact_root = service._output_root
        attempt = service.start(
            "poc_a6_executable_test",
            acknowledgement=True,
            idempotency_key="a6-source-http-secret-confirmation",
        ).attempt

        status, body = _get(f"{base_url}{attempt.evidence_pack_url}")

        assert status == 200
        assert b"idempotency_key" not in body
        assert _SECRET_CONFIRMATION_IDEMPOTENCY_KEY.encode() not in body
        assert attempt.confirmation_id.encode() in body
        assert b"contract_fingerprint" in body


def test_source_neutral_evidence_page_uses_exact_poc_identity(tmp_path):
    with _running_source_server(tmp_path) as (server, base_url):
        draft = server.draft_poc_service.create(
            DraftPOCCreateRequest(
                display_name="A6 route test",
                customer_label="A6 customer",
                use_case="Verify the route.",
                owner="a6.test",
                first_source_choice="DOCUMENT",
            ),
            idempotency_key="a6-source-page-draft",
        ).draft
        status, body = _get(f"{base_url}/app/pocs/{draft.poc_id}/evidence")
        assert status == 200
        assert b"Continue to evidence" not in body
        assert b"Start verified evidence" in body
        query_response = _get(
            f"{base_url}/app/pocs/{draft.poc_id}/evidence?extra=1"
        )
        assert query_response[0] == 400
        for invalid in (
            "poc_BAD_ID",
            "poc_x",
            "poc_valid/extra",
            "poc_valid%2Fextra",
        ):
            assert _get(f"{base_url}/app/pocs/{invalid}/evidence")[0] == 404
