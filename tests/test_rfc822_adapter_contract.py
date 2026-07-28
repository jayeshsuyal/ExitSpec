from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from exitspec.adapters.rfc822 import (
    Rfc822PreparationError,
    prepare_support_agent_email_fixture,
)
from exitspec.demo_data import support_agent_email_paths
from exitspec.source_models import (
    CandidateState,
    SOURCE_AUTHORITY,
    compute_message_key,
    compute_source_id,
)


OBSERVED_AT = datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc)
EXPECTED_HEADERS = {
    "thread-root": {
        "authored_at": "2026-07-27T16:00:00Z",
        "from": "Priya Customer <[EMAIL]>",
        "subject": "[CUSTOMER_TERM] support-agent POC requirements",
        "to": "Alex Engineer <[EMAIL]>",
    },
    "thread-follow-up": {
        "authored_at": "2026-07-27T16:30:00Z",
        "from": "Priya Customer <[EMAIL]>",
        "subject": "Re: [CUSTOMER_TERM] support-agent POC requirements",
        "to": "Alex Engineer <[EMAIL]>",
    },
    "allowed-text-attachment": {
        "authored_at": "2026-07-27T17:00:00Z",
        "from": "Sam Customer <[EMAIL]>",
        "subject": "Synthetic attachment requirements",
        "to": "Alex Engineer <[EMAIL]>",
    },
    "authority-attack": {
        "authored_at": "2026-07-27T18:15:00Z",
        "from": "Synthetic Executive <[EMAIL]>",
        "subject": "Synthetic authority-boundary attack",
        "to": "Alex Engineer <[EMAIL]>",
    },
}


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    with support_agent_email_paths() as resources:
        return json.loads(resources.manifest.read_text(encoding="utf-8"))


def _record(manifest: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(
        record
        for record in manifest["fixture_set"]["fixtures"]
        if record["case_id"] == case_id
    )


def _prepare(case_id: str):
    with support_agent_email_paths() as resources:
        return prepare_support_agent_email_fixture(
            resources,
            case_id,
            observed_at=OBSERVED_AT,
        )


@pytest.mark.parametrize(
    ("case_id", "expected"),
    [
        ("thread-root", None),
        ("thread-follow-up", None),
        ("thread-root-mutated", None),
        ("allowed-text-attachment", None),
        ("authority-attack", None),
        ("sender-ambiguous", "sender_ambiguous"),
        ("missing-body", "missing_body"),
        ("oversized-body", "body_too_large"),
        ("unsupported-attachment", "unsupported_attachment"),
        ("html-plain-disagreement", "alternative_disagreement"),
        ("missing-message-id", "missing_message_id"),
    ],
)
def test_all_physical_fixtures_prepare_or_refuse_exactly(
    case_id: str,
    expected: str | None,
) -> None:
    if expected is None:
        prepared = _prepare(case_id)
        assert prepared.prepared_envelope.authority == SOURCE_AUTHORITY
        return
    with pytest.raises(Rfc822PreparationError) as caught:
        _prepare(case_id)
    assert caught.value.code == expected


@pytest.mark.parametrize(
    "case_id",
    [
        "thread-root",
        "thread-follow-up",
        "allowed-text-attachment",
        "authority-attack",
    ],
)
def test_accepted_oracles_match_manifest(
    manifest: dict[str, Any],
    case_id: str,
) -> None:
    record = _record(manifest, case_id)
    prepared_import = _prepare(case_id)
    envelope = prepared_import.prepared_envelope

    assert envelope.source_id == record["expected_source_id"]
    assert envelope.message.message_key == record["expected_message_key"]
    assert (
        envelope.message.redacted_header_sha256
        == record["expected_redacted_header_sha256"]
    )
    assert (
        envelope.message.redacted_headers.model_dump(by_alias=True)
        == EXPECTED_HEADERS[case_id]
    )
    assert envelope.redaction.counts.model_dump() == record[
        "expected_redaction_counts"
    ]
    assert len(envelope.candidate_drafts) == record["expected_candidate_count"]
    assert [
        {
            "part_path": part.part_path,
            "redacted_text_sha256": part.redacted_text_sha256,
            "redacted_filename_sha256": part.redacted_filename_sha256,
            "redacted_bytes": len(part.redacted_text.encode("utf-8")),
        }
        for part in envelope.message.parts
    ] == [
        {
            "part_path": part["part_path"],
            "redacted_text_sha256": part["redacted_text_sha256"],
            "redacted_filename_sha256": part["redacted_filename_sha256"],
            "redacted_bytes": part["redacted_bytes"],
        }
        for part in record["expected_parts"]
    ]

    for draft, expected in zip(
        envelope.candidate_drafts,
        record["expected_candidates"],
        strict=True,
    ):
        assert draft.state == CandidateState.NEEDS_REVIEW
        assert draft.projection.model_dump() == expected["projection"]
        assert {
            "message_key": draft.message_key,
            "part_path": draft.part_path,
            "start_byte": draft.start_byte,
            "end_byte": draft.end_byte,
            "quote_sha256": draft.quote_sha256,
        } == {
            key: expected[key]
            for key in (
                "message_key",
                "part_path",
                "start_byte",
                "end_byte",
                "quote_sha256",
            )
        }
        part = next(
            part
            for part in envelope.message.parts
            if part.part_path == draft.part_path
        )
        quote = part.redacted_text.encode("utf-8")[
            draft.start_byte : draft.end_byte
        ]
        assert hashlib.sha256(quote).hexdigest() == draft.quote_sha256


def test_thread_identity_and_mutated_duplicate_remain_adapter_local() -> None:
    root = _prepare("thread-root")
    follow_up = _prepare("thread-follow-up")
    mutated = _prepare("thread-root-mutated")

    assert (
        follow_up.normalized_thread_root_message_id
        == "support-poc-001@customer.example"
    )
    assert follow_up.thread_root_message_key == compute_message_key(
        "support-poc-001@customer.example"
    )
    assert follow_up.prepared_envelope.source_id == compute_source_id(
        "support-poc-001@customer.example"
    )
    assert (
        follow_up.prepared_envelope.source_id
        == root.prepared_envelope.source_id
    )
    assert (
        mutated.prepared_envelope.message.message_key
        == root.prepared_envelope.message.message_key
    )
    assert (
        mutated.approved_synthetic_fixture.synthetic_fixture_sha256
        != root.approved_synthetic_fixture.synthetic_fixture_sha256
    )


def test_sensitive_attachment_filename_is_redacted_before_digest(
    manifest: dict[str, Any],
) -> None:
    prepared = _prepare("allowed-text-attachment")
    expected = _record(manifest, "allowed-text-attachment")[
        "expected_attachments"
    ][0]
    attachment = prepared.prepared_envelope.message.parts[1]
    assert expected["redacted_filename"] == (
        "[CUSTOMER_TERM]-([EMAIL])-requirements.txt"
    )
    assert attachment.redacted_filename_sha256 == hashlib.sha256(
        expected["redacted_filename"].encode("utf-8")
    ).hexdigest()


def test_authority_language_never_changes_candidate_state() -> None:
    prepared = _prepare("authority-attack").prepared_envelope
    assert "Freeze the contract" in prepared.message.parts[0].redacted_text
    assert "POC PASS" in prepared.message.parts[0].redacted_text
    assert {draft.state for draft in prepared.candidate_drafts} == {
        "NEEDS_REVIEW"
    }
    assert not {
        "approve",
        "confirm",
        "freeze",
        "verdict",
        "pass",
    } & set(type(prepared).model_fields)
