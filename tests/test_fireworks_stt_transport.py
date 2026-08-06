from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from exitspec.providers.fireworks_stt import (
    FIREWORKS_STT_DATA_POLICY_SHA256,
    FIREWORKS_STT_MODEL,
    FIREWORKS_STT_PROVIDER,
    FIREWORKS_STT_REGION,
    FireworksSTTTransport,
)
from exitspec.stt_boundary import (
    AudioDescriptor,
    MeetingConsentAttestation,
    STTConsentState,
    STTEgressIntent,
    STTPrivacyPolicy,
    STTRetentionMode,
    STTSpeakerMappingState,
)
from exitspec.stt_operation import (
    STTAudioPermitIssuer,
    STTOperationError,
    STTOperationExecutor,
    STTOperationFailureCode,
    STTTransportError,
)


NOW = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)
API_KEY = "fw_test_stt_credential_never_render"
AUDIO = b"\x1a\x45\xdf\xa3" + b"bounded webm test audio"
AUDIO_SHA256 = hashlib.sha256(AUDIO).hexdigest()
NOTICE_SHA256 = "a" * 64


class FakeResponse:
    def __init__(
        self,
        *,
        status: Any = 200,
        headers: Any = None,
        body: Any = None,
        read_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = (
            [
                ("Content-Type", "application/json"),
                ("X-Request-ID", "fw-stt-request-001"),
            ]
            if headers is None
            else headers
        )
        self.body = _success_body() if body is None else body
        self.read_error = read_error
        self.read_calls: list[int] = []
        self.close_calls = 0

    def getheaders(self):
        return self.headers

    def read(self, amount: int):
        self.read_calls.append(amount)
        if self.read_error is not None:
            raise self.read_error
        return self.body

    def close(self) -> None:
        self.close_calls += 1


class FakeConnection:
    def __init__(
        self,
        response: FakeResponse | None = None,
        *,
        request_error: Exception | None = None,
        getresponse_error: Exception | None = None,
    ) -> None:
        self.response = response or FakeResponse()
        self.request_error = request_error
        self.getresponse_error = getresponse_error
        self.requests: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.close_calls = 0

    def request(self, *args, **kwargs) -> None:
        self.requests.append((args, kwargs))
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self):
        if self.getresponse_error is not None:
            raise self.getresponse_error
        return self.response

    def close(self) -> None:
        self.close_calls += 1


class RecordingFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.connection


def _success_body(**updates: object) -> bytes:
    payload: dict[str, object] = {
        "duration": 1.0,
        "language": "en",
        "segments": [
            {
                "end": 0.4,
                "id": 0,
                "speaker_id": "speaker_0",
                "start": 0,
                "text": "P95 latency must stay below 500 ms.",
            },
            {
                "end": 0.9,
                "id": 1,
                "speaker_id": "speaker_1",
                "start": 0.4,
                "text": "Error rate must stay below one percent.",
            },
        ],
        "task": "transcribe",
        "text": (
            "P95 latency must stay below 500 ms. "
            "Error rate must stay below one percent."
        ),
    }
    payload.update(updates)
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _policy(**updates: object) -> STTPrivacyPolicy:
    values: dict[str, object] = {
        "policy_id": "stt_policy_fireworks_test_v1",
        "policy_version": "v1",
        "provider": FIREWORKS_STT_PROVIDER,
        "provider_model": FIREWORKS_STT_MODEL,
        "region": FIREWORKS_STT_REGION,
        "allowed_media_types": ("audio/webm",),
        "max_audio_bytes": 64 * 1024,
        "max_duration_ms": 8_000,
        "transport_timeout_seconds": 30.0,
        "provider_data_policy_sha256": FIREWORKS_STT_DATA_POLICY_SHA256,
        "consent_notice_sha256": NOTICE_SHA256,
        "deletion_policy_ref": "policy://fireworks-zero-retention-v1",
        "incident_response_policy_ref": "policy://stt-live-smoke-v1",
        "reviewed_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
    }
    values.update(updates)
    return STTPrivacyPolicy(**values)


def _intent(**updates: object) -> STTEgressIntent:
    consent = MeetingConsentAttestation(
        attestation_id="consent_fireworks_test_001",
        meeting_id="meeting_fireworks_test_001",
        participant_ids=("participant_operator_001",),
        consented_participant_ids=("participant_operator_001",),
        recording_notice_acknowledged=True,
        consent_notice_sha256=NOTICE_SHA256,
        state=STTConsentState.GRANTED,
        attested_by="synthetic:operator",
        attested_at=NOW - timedelta(seconds=20),
    )
    audio = AudioDescriptor(
        meeting_id="meeting_fireworks_test_001",
        audio_sha256=AUDIO_SHA256,
        byte_length=len(AUDIO),
        duration_ms=1_000,
        media_type="audio/webm",
        captured_at=NOW - timedelta(seconds=10),
    )
    values: dict[str, object] = {
        "request_id": "sttreq_fireworks_test_001",
        "poc_id": "poc_fireworks_stt_test",
        "meeting_id": "meeting_fireworks_test_001",
        "audio": audio,
        "consent": consent,
        "provider": FIREWORKS_STT_PROVIDER,
        "provider_model": FIREWORKS_STT_MODEL,
        "region": FIREWORKS_STT_REGION,
        "retention_mode": STTRetentionMode.ZERO_RETENTION,
        "requested_at": NOW - timedelta(seconds=5),
    }
    values.update(updates)
    return STTEgressIntent(**values)


def _execute(
    connection: FakeConnection,
    *,
    policy: STTPrivacyPolicy | None = None,
    transport_updates: dict[str, object] | None = None,
):
    selected_policy = policy or _policy()
    issuer = STTAudioPermitIssuer(selected_policy, clock=lambda: NOW)
    permit = issuer.issue(_intent(), AUDIO)
    factory = RecordingFactory(connection)
    configuration: dict[str, object] = {
        "api_key": API_KEY,
        "connection_factory": factory,
    }
    configuration.update(transport_updates or {})
    transport = FireworksSTTTransport(**configuration)
    result = STTOperationExecutor(
        transport,
        enabled=True,
        clock=lambda: NOW,
        monotonic=iter((10.0, 10.125)).__next__,
    ).execute(permit)
    return result, factory


def test_success_pins_origin_shape_and_returns_untrusted_segments():
    connection = FakeConnection()

    result, factory = _execute(connection)

    assert factory.calls == [
        (
            ("audio-prod.us-virginia-1.direct.fireworks.ai", 443),
            {"timeout": 30.0},
        )
    ]
    assert len(connection.requests) == 1
    args, kwargs = connection.requests[0]
    assert args == ("POST", "/v1/audio/transcriptions")
    body = kwargs["body"]
    headers = kwargs["headers"]
    assert type(body) is bytes
    assert AUDIO in body
    for expected in (
        b'name="model"',
        b"whisper-v3",
        b'name="response_format"',
        b"verbose_json",
        b'name="diarize"',
        b"true",
        b'name="file"; filename="capture.webm"',
    ):
        assert expected in body
    assert b'name="preprocessing"' not in body
    assert headers["Authorization"] == "Bearer " + API_KEY
    assert headers["Content-Length"] == str(len(body))
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert result.receipt.provider == FIREWORKS_STT_PROVIDER
    assert result.receipt.provider_model == FIREWORKS_STT_MODEL
    assert result.receipt.region == FIREWORKS_STT_REGION
    assert result.receipt.attempts == 1
    assert result.receipt.automatic_retries == 0
    assert result.transcript.speaker_mapping is (
        STTSpeakerMappingState.PROVIDER_ASSIGNED_UNVERIFIED
    )
    private_text = result.transcript.transient_redaction_input()
    assert "P95 latency" in private_text
    assert "Error rate" in private_text
    assert connection.response.close_calls == 1
    assert connection.close_calls == 1


def test_constructor_is_side_effect_free_and_never_renders_credential():
    connection = FakeConnection()
    factory = RecordingFactory(connection)

    transport = FireworksSTTTransport(
        api_key=API_KEY,
        connection_factory=factory,
    )

    assert factory.calls == []
    assert API_KEY not in repr(transport)
    assert "connection_factory=<redacted>" in repr(transport)


@pytest.mark.parametrize("api_key", (None, "", " has-space", "fw bad"))
def test_invalid_credential_fails_before_network(api_key):
    with pytest.raises(STTTransportError) as caught:
        FireworksSTTTransport(api_key=api_key)
    assert caught.value.failure_code is STTOperationFailureCode.TRANSPORT_CONFIGURATION
    assert API_KEY not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (400, STTOperationFailureCode.TRANSPORT_CONFIGURATION),
        (401, STTOperationFailureCode.AUTHENTICATION),
        (402, STTOperationFailureCode.ACCOUNT_UNAVAILABLE),
        (403, STTOperationFailureCode.AUTHENTICATION),
        (404, STTOperationFailureCode.TRANSPORT_CONFIGURATION),
        (408, STTOperationFailureCode.TIMEOUT),
        (412, STTOperationFailureCode.ACCOUNT_UNAVAILABLE),
        (413, STTOperationFailureCode.TRANSPORT_CONFIGURATION),
        (429, STTOperationFailureCode.RATE_LIMITED),
        (500, STTOperationFailureCode.SERVICE_UNAVAILABLE),
        (503, STTOperationFailureCode.SERVICE_UNAVAILABLE),
        (504, STTOperationFailureCode.TIMEOUT),
    ),
)
def test_http_failure_matrix_is_typed_and_single_attempt(status, expected):
    connection = FakeConnection(FakeResponse(status=status, body=b'{"error":"private"}'))

    with pytest.raises(STTOperationError) as caught:
        _execute(connection)

    assert caught.value.failure_code is expected
    assert caught.value.attempts == 1
    assert len(connection.requests) == 1
    rendered = str(caught.value) + repr(caught.value)
    assert API_KEY not in rendered
    assert "private" not in rendered


def test_redirect_is_never_followed():
    response = FakeResponse(
        status=302,
        headers=[
            ("Content-Type", "application/json"),
            ("Location", "https://attacker.invalid/audio"),
        ],
        body=b"{}",
    )
    connection = FakeConnection(response)

    with pytest.raises(STTOperationError) as caught:
        _execute(connection)

    assert caught.value.failure_code is STTOperationFailureCode.TRANSPORT
    assert len(connection.requests) == 1
    assert "attacker" not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize(
    "body",
    (
        b"not-json",
        b'{"language":"en","language":"fr","segments":[]}',
        b'{"language":"en","segments":[]}',
        _success_body(segments=[{"start": 0.8, "end": 0.2, "text": "bad"}]),
        _success_body(segments=[{"start": 0, "end": 1.5, "text": "too long"}]),
    ),
)
def test_malformed_or_unbound_provider_output_is_not_accepted(body):
    connection = FakeConnection(FakeResponse(body=body))

    with pytest.raises(STTOperationError) as caught:
        _execute(connection)

    assert caught.value.failure_code is STTOperationFailureCode.INVALID_RESPONSE
    assert caught.value.attempts == 1


def test_text_only_response_gets_one_bounded_unlabeled_segment():
    connection = FakeConnection(
        FakeResponse(body=_success_body(segments=None, text="One measurable requirement."))
    )

    result, _ = _execute(connection)

    assert result.receipt.segment_count == 1
    assert result.transcript.speaker_mapping is STTSpeakerMappingState.NOT_PROVIDED
    assert "One measurable requirement." in (
        result.transcript.transient_redaction_input()
    )


def test_mixed_speaker_labels_are_neutralized_as_unverified_mapping():
    body = _success_body(
        segments=[
            {"start": 0, "end": 0.4, "text": "First", "speaker_id": "speaker-a"},
            {"start": 0.4, "end": 0.9, "text": "Second"},
        ]
    )
    connection = FakeConnection(FakeResponse(body=body))

    result, _ = _execute(connection)

    assert result.transcript.speaker_mapping is (
        STTSpeakerMappingState.PROVIDER_ASSIGNED_UNVERIFIED
    )
    assert "Speaker 1: First" in result.transcript.transient_redaction_input()
    assert "Speaker 2: Second" in result.transcript.transient_redaction_input()


def test_timeout_and_transport_exceptions_are_content_free():
    timeout_connection = FakeConnection(request_error=TimeoutError("secret timeout"))
    with pytest.raises(STTOperationError) as timeout:
        _execute(timeout_connection)
    assert timeout.value.failure_code is STTOperationFailureCode.TIMEOUT

    transport_connection = FakeConnection(request_error=OSError("secret network"))
    with pytest.raises(STTOperationError) as transport:
        _execute(transport_connection)
    assert transport.value.failure_code is STTOperationFailureCode.TRANSPORT
    assert "secret" not in (
        str(timeout.value)
        + repr(timeout.value)
        + str(transport.value)
        + repr(transport.value)
    )


def test_wrong_frozen_provider_configuration_makes_zero_network_calls():
    connection = FakeConnection()
    policy = _policy(provider="provider.other")
    issuer = STTAudioPermitIssuer(policy, clock=lambda: NOW)
    permit = issuer.issue(
        _intent(provider="provider.other"),
        AUDIO,
    )
    factory = RecordingFactory(connection)
    executor = STTOperationExecutor(
        FireworksSTTTransport(api_key=API_KEY, connection_factory=factory),
        enabled=True,
        clock=lambda: NOW,
    )

    with pytest.raises(STTOperationError) as caught:
        executor.execute(permit)

    assert caught.value.failure_code is STTOperationFailureCode.TRANSPORT_CONFIGURATION
    assert factory.calls == []
    assert connection.requests == []
