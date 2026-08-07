from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import pickle

import pytest
from pydantic import ValidationError

from exitspec.zoom_webhook_auth import (
    ZOOM_WEBHOOK_AUTHORITY,
    ZoomWebhookAuthenticationDenied,
    ZoomWebhookAuthenticationFailureCode,
    ZoomWebhookAuthenticationPolicy,
    ZoomWebhookAuthenticationStateError,
    ZoomWebhookAuthenticator,
    ZoomWebhookSecretBoundaryError,
    zoom_webhook_policy_sha256,
)


NOW = datetime(2026, 8, 6, 20, 0, tzinfo=timezone.utc)
SECRET = b"synthetic-zoom-webhook-secret-v1"
PRIVATE_MARKER = "synthetic-private-meeting-88421"
RAW_BODY = (
    b'{"event":"meeting.rtms_started","payload":{"object":{"uuid":"'
    + PRIVATE_MARKER.encode("ascii")
    + b'"}}}'
)


@dataclass
class MutableClock:
    value: object

    def __call__(self) -> object:
        return self.value


def _policy(**updates: object) -> ZoomWebhookAuthenticationPolicy:
    values: dict[str, object] = {
        "policy_id": "zoomwhpolicy_synthetic_v1",
        "policy_version": "v1",
        "max_body_bytes": 4_096,
        "max_past_age_seconds": 300,
        "max_future_skew_seconds": 30,
        "max_replay_records": 100,
        "reviewed_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
    }
    values.update(updates)
    return ZoomWebhookAuthenticationPolicy(**values)


def _timestamp(value: datetime = NOW) -> str:
    return str(int(value.timestamp()))


def _signature(
    body: bytes = RAW_BODY,
    timestamp: str | None = None,
    *,
    secret: bytes = SECRET,
) -> str:
    timestamp = _timestamp() if timestamp is None else timestamp
    message = b"v0:" + timestamp.encode("ascii") + b":" + body
    return "v0=" + hmac.new(secret, message, hashlib.sha256).hexdigest()


def _authenticator(
    *,
    clock: MutableClock | None = None,
    policy: ZoomWebhookAuthenticationPolicy | None = None,
) -> ZoomWebhookAuthenticator:
    return ZoomWebhookAuthenticator(
        policy or _policy(),
        SECRET,
        clock=clock or MutableClock(NOW),
    )


def _failure_code(
    exc_info: pytest.ExceptionInfo[ZoomWebhookAuthenticationDenied],
) -> str:
    return exc_info.value.failure_code.value


def test_exact_documented_v0_message_authenticates_one_opaque_delivery():
    policy = _policy()
    result = _authenticator(policy=policy).authenticate(
        raw_body=RAW_BODY,
        request_timestamp=_timestamp(),
        signature=_signature(),
    )

    assert result.first_observation is True
    assert result.exact_replay is False
    assert result.downstream_effect_permitted is False
    assert result.receipt.authentication_authority == ZOOM_WEBHOOK_AUTHORITY
    assert result.receipt.webhook_signature_verified is True
    assert result.receipt.webhook_event_sha256 == hashlib.sha256(RAW_BODY).hexdigest()
    assert result.receipt.body_size_bytes == len(RAW_BODY)
    assert result.receipt.policy_sha256 == zoom_webhook_policy_sha256(policy)
    assert result.receipt.raw_body_retained is False
    assert result.receipt.synthetic_only is True


def test_public_receipt_contains_no_raw_body_secret_or_zoom_identifiers():
    result = _authenticator().authenticate(
        raw_body=RAW_BODY,
        request_timestamp=_timestamp(),
        signature=_signature(),
    )

    public = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert PRIVATE_MARKER not in public
    assert RAW_BODY.decode("ascii") not in public
    assert SECRET.decode("ascii") not in public


def test_receipt_explicitly_has_zero_parse_transport_inbox_or_decision_authority():
    receipt = _authenticator().authenticate(
        raw_body=RAW_BODY,
        request_timestamp=_timestamp(),
        signature=_signature(),
    ).receipt

    assert receipt.may_parse_zoom_payload is False
    assert receipt.may_create_transport_binding is False
    assert receipt.may_append_meeting_inbox is False
    assert receipt.may_confirm_contract is False
    assert receipt.may_freeze_contract is False
    assert receipt.may_start_measurement is False
    assert receipt.may_assign_verdict is False


@pytest.mark.parametrize(
    "signature",
    (
        "",
        "v1=" + "0" * 64,
        "v0=" + "0" * 63,
        "v0=" + "A" * 64,
        "v0=" + "0" * 64,
        None,
    ),
)
def test_missing_malformed_or_wrong_signature_fails_closed(signature: object):
    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        _authenticator().authenticate(
            raw_body=RAW_BODY,
            request_timestamp=_timestamp(),
            signature=signature,  # type: ignore[arg-type]
        )

    assert _failure_code(exc_info) == "ZOOM_WEBHOOK_AUTHENTICATION_FAILED"
    assert PRIVATE_MARKER not in str(exc_info.value)


def test_signature_is_bound_to_exact_raw_bytes_not_parsed_json_equivalence():
    reformatted = json.dumps(
        json.loads(RAW_BODY),
        indent=2,
        sort_keys=True,
    ).encode("utf-8")

    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        _authenticator().authenticate(
            raw_body=reformatted,
            request_timestamp=_timestamp(),
            signature=_signature(RAW_BODY),
        )

    assert _failure_code(exc_info) == "ZOOM_WEBHOOK_AUTHENTICATION_FAILED"


def test_signature_is_bound_to_the_exact_timestamp_header():
    changed_timestamp = _timestamp(NOW + timedelta(seconds=1))

    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        _authenticator().authenticate(
            raw_body=RAW_BODY,
            request_timestamp=changed_timestamp,
            signature=_signature(timestamp=_timestamp()),
        )

    assert _failure_code(exc_info) == "ZOOM_WEBHOOK_AUTHENTICATION_FAILED"


@pytest.mark.parametrize("timestamp", ("01", "-1", "+1", "1.0", "abc", "", None))
def test_noncanonical_timestamp_is_an_authentication_failure(timestamp: object):
    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        _authenticator().authenticate(
            raw_body=RAW_BODY,
            request_timestamp=timestamp,  # type: ignore[arg-type]
            signature="v0=" + "0" * 64,
        )

    assert _failure_code(exc_info) == "ZOOM_WEBHOOK_AUTHENTICATION_FAILED"


@pytest.mark.parametrize(
    "request_time",
    (
        NOW - timedelta(seconds=301),
        NOW + timedelta(seconds=31),
    ),
)
def test_validly_signed_stale_or_future_request_is_rejected(request_time: datetime):
    timestamp = _timestamp(request_time)

    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        _authenticator().authenticate(
            raw_body=RAW_BODY,
            request_timestamp=timestamp,
            signature=_signature(timestamp=timestamp),
        )

    assert _failure_code(exc_info) == "ZOOM_WEBHOOK_TIMESTAMP_OUTSIDE_WINDOW"


def test_exact_freshness_boundaries_are_accepted():
    for request_time in (
        NOW - timedelta(seconds=300),
        NOW + timedelta(seconds=30),
    ):
        timestamp = _timestamp(request_time)
        result = _authenticator().authenticate(
            raw_body=RAW_BODY,
            request_timestamp=timestamp,
            signature=_signature(timestamp=timestamp),
        )
        assert result.first_observation is True


@pytest.mark.parametrize("body", (b"", bytearray(RAW_BODY), None, "{}"))
def test_empty_or_non_bytes_body_is_rejected(body: object):
    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        _authenticator().authenticate(
            raw_body=body,  # type: ignore[arg-type]
            request_timestamp=_timestamp(),
            signature=_signature(),
        )

    assert _failure_code(exc_info) == "ZOOM_WEBHOOK_REQUEST_MALFORMED"


def test_oversized_body_is_rejected_before_any_receipt_exists():
    body = b"x" * 4_097

    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        _authenticator().authenticate(
            raw_body=body,
            request_timestamp=_timestamp(),
            signature=_signature(body),
        )

    assert _failure_code(exc_info) == "ZOOM_WEBHOOK_BODY_LIMIT_EXCEEDED"


def test_exact_replay_returns_the_original_receipt_without_new_authority():
    authenticator = _authenticator()
    request = {
        "raw_body": RAW_BODY,
        "request_timestamp": _timestamp(),
        "signature": _signature(),
    }

    first = authenticator.authenticate(**request)
    replay = authenticator.authenticate(**request)

    assert replay.receipt is first.receipt
    assert replay.receipt.receipt_id == first.receipt.receipt_id
    assert replay.exact_replay is True
    assert replay.first_observation is False
    assert replay.downstream_effect_permitted is False


def test_concurrent_exact_replays_create_one_first_observation():
    authenticator = _authenticator()
    request = {
        "raw_body": RAW_BODY,
        "request_timestamp": _timestamp(),
        "signature": _signature(),
    }

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = tuple(
            pool.map(
                lambda _: authenticator.authenticate(**request),
                range(32),
            )
        )

    assert sum(result.first_observation for result in results) == 1
    assert sum(result.exact_replay for result in results) == 31
    assert len({result.receipt.receipt_id for result in results}) == 1


def test_replay_capacity_fails_closed_then_expired_state_is_pruned():
    clock = MutableClock(NOW)
    policy = _policy(max_replay_records=1)
    authenticator = _authenticator(clock=clock, policy=policy)
    authenticator.authenticate(
        raw_body=RAW_BODY,
        request_timestamp=_timestamp(),
        signature=_signature(),
    )
    second_body = b'{"event":"meeting.rtms_stopped"}'

    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        authenticator.authenticate(
            raw_body=second_body,
            request_timestamp=_timestamp(),
            signature=_signature(second_body),
        )
    assert _failure_code(exc_info) == "ZOOM_WEBHOOK_REPLAY_CAPACITY_EXCEEDED"

    clock.value = NOW + timedelta(seconds=301)
    current_timestamp = _timestamp(clock.value)
    accepted = authenticator.authenticate(
        raw_body=second_body,
        request_timestamp=current_timestamp,
        signature=_signature(second_body, current_timestamp),
    )
    assert accepted.first_observation is True


def test_policy_activation_and_expiry_fail_closed():
    not_active = _policy(reviewed_at=NOW + timedelta(seconds=1))
    expired = _policy(
        reviewed_at=NOW - timedelta(days=2),
        expires_at=NOW,
    )

    with pytest.raises(ZoomWebhookAuthenticationDenied) as not_active_exc:
        _authenticator(policy=not_active).authenticate(
            raw_body=RAW_BODY,
            request_timestamp=_timestamp(),
            signature=_signature(),
        )
    with pytest.raises(ZoomWebhookAuthenticationDenied) as expired_exc:
        _authenticator(policy=expired).authenticate(
            raw_body=RAW_BODY,
            request_timestamp=_timestamp(),
            signature=_signature(),
        )

    assert _failure_code(not_active_exc) == "ZOOM_WEBHOOK_POLICY_NOT_ACTIVE"
    assert _failure_code(expired_exc) == "ZOOM_WEBHOOK_POLICY_EXPIRED"


def test_invalid_clock_and_clock_rollback_are_state_errors():
    invalid_clock = MutableClock(NOW.replace(tzinfo=None))
    with pytest.raises(ZoomWebhookAuthenticationStateError):
        _authenticator(clock=invalid_clock).authenticate(
            raw_body=RAW_BODY,
            request_timestamp=_timestamp(),
            signature=_signature(),
        )

    clock = MutableClock(NOW)
    authenticator = _authenticator(clock=clock)
    authenticator.authenticate(
        raw_body=RAW_BODY,
        request_timestamp=_timestamp(),
        signature=_signature(),
    )
    clock.value = NOW - timedelta(seconds=1)
    with pytest.raises(ZoomWebhookAuthenticationStateError):
        authenticator.authenticate(
            raw_body=RAW_BODY,
            request_timestamp=_timestamp(),
            signature=_signature(),
        )


def test_close_and_secret_copy_or_serialization_attempts_fail_closed():
    authenticator = _authenticator()
    assert SECRET.decode("ascii") not in repr(authenticator)

    for operation in (
        lambda: copy.copy(authenticator),
        lambda: copy.deepcopy(authenticator),
        lambda: pickle.dumps(authenticator),
    ):
        with pytest.raises(ZoomWebhookSecretBoundaryError):
            operation()

    authenticator.close()
    with pytest.raises(ZoomWebhookAuthenticationStateError):
        authenticator.authenticate(
            raw_body=RAW_BODY,
            request_timestamp=_timestamp(),
            signature=_signature(),
        )


def test_policy_hash_binds_reviewed_limits_and_policy_is_immutable():
    policy = _policy()
    changed = _policy(max_past_age_seconds=299)

    assert zoom_webhook_policy_sha256(policy) != zoom_webhook_policy_sha256(changed)
    with pytest.raises(ValidationError):
        policy.max_past_age_seconds = 299  # type: ignore[misc]


def test_tampered_receipt_identity_is_rejected():
    receipt = _authenticator().authenticate(
        raw_body=RAW_BODY,
        request_timestamp=_timestamp(),
        signature=_signature(),
    ).receipt
    payload = receipt.model_dump(mode="python")
    payload["webhook_event_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="receipt_id"):
        type(receipt)(**payload)


@pytest.mark.parametrize(
    "updates",
    (
        {"max_body_bytes": 0},
        {"max_past_age_seconds": 901},
        {"max_future_skew_seconds": 301},
        {"max_replay_records": 0},
        {"synthetic_only": False},
        {"reviewed_at": NOW.replace(tzinfo=None)},
        {"expires_at": NOW - timedelta(days=2)},
    ),
)
def test_invalid_or_non_synthetic_policy_is_rejected(updates: dict[str, object]):
    with pytest.raises(ValidationError):
        _policy(**updates)


def test_secret_type_and_length_are_strictly_bounded():
    for secret in (b"short", bytearray(SECRET), b"x" * 513):
        with pytest.raises(ValueError, match="secret"):
            ZoomWebhookAuthenticator(_policy(), secret)  # type: ignore[arg-type]


def test_public_failure_messages_do_not_reflect_untrusted_request_material():
    forged_signature = "v0=" + "f" * 64
    with pytest.raises(ZoomWebhookAuthenticationDenied) as exc_info:
        _authenticator().authenticate(
            raw_body=RAW_BODY,
            request_timestamp=_timestamp(),
            signature=forged_signature,
        )

    rendered = str(exc_info.value)
    assert PRIVATE_MARKER not in rendered
    assert forged_signature not in rendered
    assert _timestamp() not in rendered
    assert exc_info.value.retryable is False
    assert (
        exc_info.value.failure_code
        is ZoomWebhookAuthenticationFailureCode.AUTHENTICATION_FAILED
    )
