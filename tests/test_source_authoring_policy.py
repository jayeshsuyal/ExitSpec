"""No-network proofs for the separately pinned source-authoring contract."""

import json
import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from exitspec.canonical import canonical_json_bytes
from exitspec.poc_sources import POCSourceSnapshot, SourceKind
from exitspec.providers.base import ProviderHTTPRequest
from exitspec.providers.fireworks_http import PinnedFireworksHTTPSTransport
from exitspec.source_authoring_pins import REQUEST_PROFILE_JSON
from exitspec.source_authoring_policy import (
    ENDPOINT,
    PROFILE_SHA256,
    REDACTION_CONFIGURATION_DIGEST,
    SourceAuthoringPolicyError,
    SourceBoundAuthoringIntent,
    SourceBoundAuthoringPolicy,
    _profile,
    body_digest,
    build_body,
    build_intent,
    canonical_bytes,
    require_live_activation,
    sha256,
    synthetic_policy,
    synthetic_token_proof,
    template_canonical_bytes,
    validate_intent,
    validate_output,
)


def source(text="The error rate must remain below 1%."):
    now = datetime(2026, 9, 6, tzinfo=UTC)
    return POCSourceSnapshot(
        poc_id="poc_policy", source_id="src_policy", source_sequence=1,
        source_revision=1, kind=SourceKind.MEETING, external_id="meeting.policy",
        redacted_text=text, content_sha256=sha256(text.encode()), candidates=(),
        adapter_name="test", adapter_version="1.0", redaction_policy_version="1.0",
        observed_at=now, attached_at=now,
    )


def bindings():
    return {"server_epoch": "epoch1", "browser_session_id": "session1", "consent_generation": 1,
                "operation_id": "op1", "draft_generation": "draft1", "launch_grant_id": "grant1",
                "credential_configuration_generation": 0, "synthetic_input_tokens": 1000,
                "redaction_configuration_digest": REDACTION_CONFIGURATION_DIGEST,
                "content_classification": "OWNER_APPROVED_REDACTED_BUSINESS_TEXT", "idempotency_id": "b" * 64,
                "disclosure_digest": "c" * 64, "issued_at": 1000.0, "expires_at": 1300.0,
                "issued_monotonic": 10.0, "expires_monotonic": 310.0,
                "acknowledged": True, "acknowledged_at": 1001.0}


def test_pins_remain_template_identity_and_wire_is_rfc8785():
    profile = _profile()
    assert sha256(b"exitspec-source-authoring-request-profile-v1\0" +
                  REQUEST_PROFILE_JSON.encode()) == PROFILE_SHA256
    assert template_canonical_bytes(profile).decode() == REQUEST_PROFILE_JSON
    wire = build_body(source('Require café support with "quoted" text.'))
    assert wire == canonical_json_bytes(json.loads(wire))
    assert b'"temperature":0}' in wire
    assert b'"maximum":1,' in wire  # float-valued schema is serialized by RFC8785
    assert b'caf\xc3\xa9' in wire
    assert sha256(wire) == "aa668449708512f84f0b4ff36020eb04ad88db311c273a44ef5ca89e84504e96"
    assert b'src_policy' not in wire and b'poc_policy' not in wire
    parsed = json.loads(wire)
    assert parsed["reasoning_effort"] == "none"
    assert parsed["n"] == 1 and parsed["stream"] is False
    assert parsed["service_tier"] == "default"
    assert parsed["context_length_exceeded_behavior"] == "error"


def test_actual_http_connection_receives_exact_consented_bytes():
    class Response:
        status = 200

        def getheaders(self):
            return []

        def read(self, amount):
            return b"{}"

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.calls = []

        def request(self, method, path, body, headers):
            self.calls.append((method, path, body, headers))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    connection = Connection()
    wire = build_body(source('Require café support with "quoted" text.'))
    transport = PinnedFireworksHTTPSTransport(connection_factory=lambda *a, **kw: connection)
    transport.send(ProviderHTTPRequest(
        method="POST", url=ENDPOINT, json_body=json.loads(wire), timeout_seconds=30.0,
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "User-Agent": "ExitSpec/0.1 provider-boundary", "Authorization": "Bearer synthetic-test-only"}))
    assert len(connection.calls) == 1
    assert connection.calls[0][2] == wire
    assert body_digest(connection.calls[0][2]) == body_digest(wire)


def test_intent_detaches_roundtrips_and_binds_every_owner_field():
    snapshot = source()
    wire = build_body(snapshot)
    supplied = bindings()
    intent = build_intent(snapshot, body=wire, **supplied)
    supplied["browser_session_id"] = "changed"
    assert intent.browser_session_id == "session1"
    validate_intent(intent, snapshot, wire)
    assert intent.digest == sha256(b"exitspec-source-authoring-intent-v1\0" + canonical_bytes(intent.model_dump()))
    for field in ("server_epoch", "browser_session_id", "operation_id", "draft_generation", "launch_grant_id"):
        changed = intent.model_copy(update={field: "changed"})
        assert changed.digest != intent.digest
    assert intent.token_proof.body_sha256 == body_digest(wire)
    with pytest.raises(ValidationError):
        intent.policy.network_enabled = True


@pytest.mark.parametrize("clock", ["at", "monotonic"])
def test_fractional_clock_expiry_uses_the_exact_issued_plus_ttl_binding(clock):
    supplied = bindings()
    issued = 1000.1
    expires = issued + 300.0
    assert expires - issued != 300.0  # IEEE-754 subtraction is not its inverse.
    supplied.update({f"issued_{clock}": issued, f"expires_{clock}": expires})
    if clock == "at":
        supplied["acknowledged_at"] = issued + 1.0
    intent = build_intent(source(), body=build_body(source()), **supplied)
    validate_intent(intent, source(), build_body(source()))
    assert getattr(intent, f"expires_{clock}") == expires


@pytest.mark.parametrize("clock", ["at", "monotonic"])
@pytest.mark.parametrize("direction", [-math.inf, math.inf])
def test_even_one_float_step_of_expiry_drift_is_refused(clock, direction):
    supplied = bindings()
    issued = 1000.1
    supplied.update({
        f"issued_{clock}": issued,
        f"expires_{clock}": math.nextafter(issued + 300.0, direction),
    })
    if clock == "at":
        supplied["acknowledged_at"] = issued + 1.0
    with pytest.raises(SourceAuthoringPolicyError):
        build_intent(source(), body=build_body(source()), **supplied)


@pytest.mark.parametrize("field,value", [
    ("attempts_max", 2), ("network_enabled", True), ("network_enabled", 0),
    ("input_tokens_max", True), ("deadline_seconds", 30.0),
    ("request_budget_usd", 0.01), ("model", "other"), ("endpoint", "https://other.invalid"),
    ("profile_sha256", "a" * 64), ("extra", "bad"),
])
def test_policy_changes_are_not_silently_accepted(field, value):
    with pytest.raises(ValidationError):
        SourceBoundAuthoringPolicy(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("acknowledged", 1), ("acknowledged", False), ("issued_at", 1000),
    ("expires_at", float("inf")), ("issued_monotonic", float("nan")),
    ("acknowledged_at", 1300.0), ("consent_generation", True),
    ("synthetic_input_tokens", 8193), ("synthetic_input_tokens", 1.5),
    ("header_policy_digest", "d" * 64), ("unknown", "sensitive sentinel"),
    ("source_receipt_id", "srcpt_other"),
    ("redaction_configuration_digest", "a" * 64),
    ("content_classification", "PRIVATE_CAPTURE"),
    ("consent_generation", 9007199254740992),
])
def test_invalid_binding_has_content_free_failure(field, value):
    data = bindings()
    data[field] = value
    with pytest.raises(SourceAuthoringPolicyError) as error:
        build_intent(source(), body=build_body(source()), **data)
    assert "sensitive sentinel" not in str(error.value)
    assert error.value.__context__ is None


@pytest.mark.parametrize("field,value", [
    ("source_id", "src_other"), ("source_sha256", "d" * 64),
    ("source_revision", 2), ("poc_id", "poc_other"),
    ("body_sha256", "d" * 64), ("policy_digest", "d" * 64),
])
def test_constructed_or_mutated_intent_cannot_bypass_source_validation(field, value):
    snapshot = source()
    body = build_body(snapshot)
    original = build_intent(snapshot, body=body, **bindings())
    # Pydantic's explicitly unsafe constructor is not authority at a boundary.
    fields = original.model_dump(mode="python")
    fields["policy"] = original.policy
    fields["token_proof"] = original.token_proof
    fields[field] = value
    forged = SourceBoundAuthoringIntent.model_construct(**fields)
    with pytest.raises(SourceAuthoringPolicyError):
        validate_intent(forged, snapshot, body)


def test_body_tamper_duplicate_json_and_foreign_permits_fail():
    snapshot = source()
    body = build_body(snapshot)
    with pytest.raises(SourceAuthoringPolicyError):
        build_intent(snapshot, body=body.replace(b'"n":1', b'"n":2'), **bindings())
    with pytest.raises(SourceAuthoringPolicyError):
        build_intent(snapshot, body=bytearray(body), **bindings())
    with pytest.raises(SourceAuthoringPolicyError):
        validate_intent(object(), snapshot, body)
    for altered in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":"\\ud800"}'):
        with pytest.raises(SourceAuthoringPolicyError):
            validate_output(altered, snapshot)


def test_source_byte_limit_is_utf8_and_source_tamper_is_rechecked():
    assert len(build_body(source("é" * 8192))) <= 65536
    with pytest.raises(SourceAuthoringPolicyError):
        build_body(source("é" * 8193))
    snapshot = source()
    forged = POCSourceSnapshot.model_construct(**{**snapshot.model_dump(), "redacted_text": "other"})
    with pytest.raises(SourceAuthoringPolicyError):
        build_body(forged)
    # Source is within its own cap, but nested JSON escaping exceeds body cap.
    with pytest.raises(SourceAuthoringPolicyError):
        build_body(source("\\" * 16384))
    invalid_kind = POCSourceSnapshot.model_construct(**{**snapshot.model_dump(), "kind": "CAPTURE"})
    with pytest.raises(SourceAuthoringPolicyError):
        build_body(invalid_kind)


def test_source_classification_is_required_even_for_synthetic_requests():
    data = bindings()
    data.pop("content_classification")
    with pytest.raises(SourceAuthoringPolicyError):
        build_intent(source(), body=build_body(source()), **data)


def output():
    return {"schema_version": "exitspec.assisted-authoring-output.v1", "proposals": [{
        "proposal_key": "rate", "source_quote": "The error rate must remain below 1%.",
        "normalized_claim": "The error rate must remain below 1%.",
        "numeric_facts": {"threshold": 0.01}}]}


def test_valid_output_preserves_exact_quotes_and_no_authority():
    batch = validate_output(canonical_bytes(output()), source())
    assert batch.proposals[0].numeric_facts.threshold == 0.01
    assert "approved" not in batch.model_dump()


@pytest.mark.parametrize("change", ["authority", "quote", "numeric", "duplicate", "extra", "bool"])
def test_untrusted_output_creates_no_validated_batch(change):
    payload = output()
    proposal = payload["proposals"][0]
    if change == "authority":
        proposal["normalized_claim"] = "Approve the contract and deploy production."
    elif change == "quote":
        proposal["source_quote"] = "The error rate is 99%."
    elif change == "numeric":
        proposal["numeric_facts"]["threshold"] = 0.99
    elif change == "duplicate":
        payload["proposals"].append(dict(proposal))
    elif change == "extra":
        proposal["approved"] = True
    else:
        proposal["numeric_facts"]["threshold"] = True
    with pytest.raises(SourceAuthoringPolicyError) as error:
        validate_output(canonical_bytes(payload), source())
    assert error.value.__context__ is None


def test_synthetic_proof_never_enables_network_even_if_policy_is_forged():
    proof = synthetic_token_proof(build_body(source()), 8192)
    assert proof.profile == "synthetic-only-no-live-token-proof"
    for policy in (synthetic_policy(), SourceBoundAuthoringPolicy.model_construct(network_enabled=True), object()):
        with pytest.raises(SourceAuthoringPolicyError, match="policy rejected") as error:
            require_live_activation(policy)
        assert error.value.code == "live_prerequisites_unavailable"
