import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections import Counter
from copy import deepcopy
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT
    / "examples/support-agent/email/wave-2-acceptance-v1.json"
)
FIXTURE_DIRECTORY = PROJECT_ROOT / "examples/support-agent/email"

MESSAGE_ID_PATTERN = re.compile(r"<([^<>\s]+)>")

EXPECTED_CANDIDATE_PROJECTIONS = {
    "thread-root": [
        {
            "criterion_type": "numeric_threshold",
            "metric": "tool_selection_accuracy",
            "operator": "gte",
            "threshold": "0.95",
            "unit": "ratio",
            "minimum_samples": 200,
        },
        {
            "criterion_type": "numeric_threshold",
            "metric": "end_to_end_latency_p95",
            "operator": "lt",
            "threshold": "2",
            "unit": "seconds",
            "minimum_samples": None,
        },
    ],
    "thread-follow-up": [
        {
            "criterion_type": "numeric_threshold",
            "metric": "total_model_and_tool_cost_per_resolved_case",
            "operator": "lte",
            "threshold": "0.04",
            "unit": "USD/case",
            "minimum_samples": None,
        }
    ],
    "allowed-text-attachment": [
        {
            "criterion_type": "numeric_threshold",
            "metric": "escalation_rate",
            "operator": "lt",
            "threshold": "0.03",
            "unit": "ratio",
            "minimum_samples": None,
        }
    ],
    "authority-attack": [
        {
            "criterion_type": "numeric_threshold",
            "metric": "tool_selection_accuracy",
            "operator": "gte",
            "threshold": "0.95",
            "unit": "ratio",
            "minimum_samples": None,
        }
    ],
}


def _load_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _fixture_cases(manifest):
    return {
        fixture["case_id"]: fixture
        for fixture in manifest["fixture_set"]["fixtures"]
    }


def _fixture_path(fixture):
    return PROJECT_ROOT / fixture["path"]


def _parse_fixture(fixture):
    return BytesParser(policy=policy.default).parsebytes(
        _fixture_path(fixture).read_bytes()
    )


def _canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalize_text(value):
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = unicodedata.normalize("NFC", normalized)
    normalized = "\n".join(
        line.rstrip(" \t") for line in normalized.split("\n")
    )
    return normalized.strip("\n") + "\n"


def _regex_flags(flag_names):
    supported = {
        "ASCII": re.ASCII,
        "IGNORECASE": re.IGNORECASE,
    }
    assert set(flag_names) <= set(supported)
    flags = 0
    for name in flag_names:
        flags |= supported[name]
    return flags


def _ordered_customer_terms(manifest, customer_terms):
    semantics = manifest["normalization_and_redaction"][
        "customer_term_semantics"
    ]
    normalized = [
        unicodedata.normalize("NFC", term)
        for term in customer_terms
    ]
    if not semantics["empty_terms_allowed"]:
        assert all(normalized)
    unique = {}
    for term in normalized:
        unique.setdefault(term.casefold(), term)
    assert semantics["duplicate_semantics"] == (
        "deduplicate by NFC casefolded value"
    )
    assert semantics["ordering"] == (
        "descending Unicode code-point length, then ascending casefolded "
        "lexical value"
    )
    return sorted(
        unique.values(),
        key=lambda term: (-len(term), term.casefold()),
    )


def _compiled_redaction_pattern(manifest, kind):
    specification = manifest["normalization_and_redaction"]["patterns"][kind]
    return re.compile(
        specification["pattern"],
        _regex_flags(specification["flags"]),
    )


def _redact(value, customer_terms, manifest):
    policy = manifest["normalization_and_redaction"]
    value = unicodedata.normalize("NFC", value)
    counts = {
        kind: 0
        for kind in policy["replacement_tokens"]
    }

    for kind in policy["redaction_order"]:
        replacement = policy["replacement_tokens"][kind]
        if kind == "customer_term":
            semantics = policy["customer_term_semantics"]
            flags = _regex_flags(semantics["flags"])
            for customer_term in _ordered_customer_terms(
                manifest,
                customer_terms,
            ):
                value, count = re.subn(
                    re.escape(customer_term),
                    replacement,
                    value,
                    flags=flags,
                )
                counts[kind] += count
            continue
        value, count = _compiled_redaction_pattern(
            manifest,
            kind,
        ).subn(replacement, value)
        counts[kind] += count
    return value, counts


def _merge_counts(total, additional):
    for key, count in additional.items():
        total[key] += count


def _redacted_header_projection(message, customer_terms, manifest):
    authored_at = parsedate_to_datetime(
        str(message["Date"])
    ).astimezone(timezone.utc)
    return {
        "authored_at": authored_at.isoformat().replace("+00:00", "Z"),
        "from": _redact(
            str(message.get("From", "")),
            customer_terms,
            manifest,
        )[0],
        "subject": _redact(
            str(message.get("Subject", "")),
            customer_terms,
            manifest,
        )[0],
        "to": _redact(
            str(message.get("To", "")),
            customer_terms,
            manifest,
        )[0],
    }


def _redacted_header_digest(message, customer_terms, manifest):
    versioning = manifest["source_contract"]["versioning"]
    projection = _redacted_header_projection(
        message,
        customer_terms,
        manifest,
    )
    assert set(projection) == set(
        versioning["redacted_header_projection"]["exact_fields"]
    )
    return hashlib.sha256(
        versioning["redacted_header_digest_domain"].encode("ascii")
        + b"\x00"
        + _canonical_json_bytes(projection)
    ).hexdigest()


def _normalized_redacted_parts(message, customer_terms, manifest):
    parts = []
    body_index = 0
    attachment_index = 0
    redaction_counts = {
        "customer_term": 0,
        "email": 0,
        "phone": 0,
        "secret": 0,
    }

    for header_name in ("From", "To", "Subject"):
        _, counts = _redact(
            str(message.get(header_name, "")),
            customer_terms,
            manifest,
        )
        _merge_counts(redaction_counts, counts)

    for part in message.walk():
        if part.is_multipart():
            continue
        media_type = part.get_content_type().lower()
        if media_type not in {"text/plain", "application/json"}:
            continue
        charset = (part.get_content_charset() or "utf-8").lower()
        decoded = (part.get_payload(decode=True) or b"").decode(
            charset,
            errors="strict",
        )
        normalized = _normalize_text(decoded)
        redacted, counts = _redact(
            normalized,
            customer_terms,
            manifest,
        )
        _merge_counts(redaction_counts, counts)
        if part.get_content_disposition() == "attachment":
            part_path = f"attachment:{media_type}:{attachment_index}"
            attachment_index += 1
            redacted_filename, filename_counts = _redact(
                part.get_filename() or "",
                customer_terms,
                manifest,
            )
            _merge_counts(redaction_counts, filename_counts)
            redacted_filename_sha256 = hashlib.sha256(
                redacted_filename.encode("utf-8")
            ).hexdigest()
        else:
            part_path = f"body:{media_type}:{body_index}"
            body_index += 1
            redacted_filename = None
            redacted_filename_sha256 = None
        parts.append(
            {
                "part_path": part_path,
                "redacted_text": redacted,
                "redacted_filename": redacted_filename,
                "redacted_filename_sha256": redacted_filename_sha256,
            }
        )

    return parts, redaction_counts


def _normalized_message_id(value):
    normalized = value.strip()
    if normalized.startswith("<") and normalized.endswith(">"):
        normalized = normalized[1:-1]
    return normalized.lower()


def _opaque_identity(prefix, raw_message_id, label):
    digest = hashlib.sha256(
        prefix.encode("ascii")
        + b"\x00"
        + _normalized_message_id(raw_message_id).encode("ascii")
    ).hexdigest()
    return f"{label}:{digest}"


def _thread_root_message_id(message):
    references = MESSAGE_ID_PATTERN.findall(str(message.get("References", "")))
    if references:
        return f"<{references[0]}>"
    replies = MESSAGE_ID_PATTERN.findall(str(message.get("In-Reply-To", "")))
    if len(replies) == 1:
        return f"<{replies[0]}>"
    return str(message["Message-ID"])


def _inline_plain_parts(message):
    return [
        part
        for part in message.walk()
        if not part.is_multipart()
        and part.get_content_type() == "text/plain"
        and part.get_content_disposition() != "attachment"
    ]


def _part_digest_projection(parts):
    return [
        {
            "part_path": part["part_path"],
            "redacted_filename_sha256": part[
                "redacted_filename_sha256"
            ],
            "redacted_text_sha256": hashlib.sha256(
                part["redacted_text"].encode("utf-8")
            ).hexdigest(),
        }
        for part in parts
    ]


def _version_input(manifest, fixture):
    cases = _fixture_cases(manifest)
    case_ids = [
        *fixture.get("precondition_case_ids", []),
        fixture["case_id"],
    ]
    messages = []
    for case_id in case_ids:
        version_fixture = cases[case_id]
        message = _parse_fixture(version_fixture)
        parts, _ = _normalized_redacted_parts(
            message,
            version_fixture["customer_terms"],
            manifest,
        )
        messages.append(
            {
                "message_key": version_fixture["expected_message_key"],
                "parts": _part_digest_projection(parts),
                "redacted_header_sha256": _redacted_header_digest(
                    message,
                    version_fixture["customer_terms"],
                    manifest,
                ),
            }
        )
    return {
        "messages": messages,
        "source_id": fixture["expected_source_id"],
        "source_version": fixture["expected_source_version"],
    }


def _expected_version_id(manifest, fixture):
    versioning = manifest["source_contract"]["versioning"]
    version_input = _version_input(manifest, fixture)
    assert set(version_input) == set(
        versioning["version_id_input_exact_fields"]
    )
    for message in version_input["messages"]:
        assert set(message) == set(
            versioning["version_message_exact_fields"]
        )
        for part in message["parts"]:
            assert set(part) == set(
                versioning["version_part_exact_fields"]
            )
    return "srcv:" + hashlib.sha256(
        versioning["version_id_domain"].encode("ascii")
        + b"\x00"
        + _canonical_json_bytes(version_input)
    ).hexdigest()


def _candidate_source_link_is_valid(candidate, fixture, parts):
    parts_by_path = {
        part["part_path"]: part["redacted_text"].encode("utf-8")
        for part in parts
    }
    if candidate["source_id"] != fixture["expected_source_id"]:
        return False
    if candidate["source_version"] != fixture["expected_source_version"]:
        return False
    if candidate["version_id"] != fixture["expected_version_id"]:
        return False
    if candidate["message_key"] != fixture["expected_message_key"]:
        return False
    source = parts_by_path.get(candidate["part_path"])
    if source is None:
        return False
    start = candidate["start_byte"]
    end = candidate["end_byte"]
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
        or end > len(source)
    ):
        return False
    return hashlib.sha256(source[start:end]).hexdigest() == candidate[
        "quote_sha256"
    ]


def _evaluate_fault_case(manifest, fault_case):
    operation = fault_case["operation"]
    operation_type = operation["type"]
    attachment_policy = manifest["attachment_policy"]
    content_limits = manifest["content_limits"]

    if operation_type == "synthetic_marker_check":
        marker = manifest["source_contract"]["synthetic_marker"]
        if (
            operation["present"] is not True
            or operation["value"] != marker["required_value"]
        ):
            return marker["missing_or_other_value_code"]
        return "accepted"

    if operation_type == "compare_fixture_digest":
        fixture = _fixture_cases(manifest)[
            fault_case["base_fixture_case_id"]
        ]
        if operation["observed_sha256"] != fixture["sha256"]:
            return manifest["source_contract"]["digests"][
                "digest_mismatch_code"
            ]
        return "accepted"

    if operation_type == "virtual_raw_message_bytes":
        if operation["value"] > content_limits["raw_message_bytes_max"]:
            return content_limits["required_failure_codes"][
                "raw_message_bytes_max"
            ]
        return "accepted"

    if operation_type == "virtual_header_count":
        if operation["value"] > content_limits["header_count_max"]:
            return content_limits["required_failure_codes"][
                "header_count_max"
            ]
        return "accepted"

    if operation_type == "virtual_unfolded_header_bytes":
        if (
            operation["value"]
            > content_limits["single_unfolded_header_bytes_max"]
        ):
            return content_limits["required_failure_codes"][
                "single_unfolded_header_bytes_max"
            ]
        return "accepted"

    if operation_type == "virtual_mime_shape":
        if operation["depth"] > content_limits["mime_depth_max"]:
            return content_limits["required_failure_codes"][
                "mime_depth_max"
            ]
        if (
            operation["part_count"]
            > content_limits["multipart_part_count_max"]
        ):
            return content_limits["required_failure_codes"][
                "multipart_part_count_max"
            ]
        return "accepted"

    if operation_type == "strict_body_transfer_decode":
        if operation["encoding"] != "base64":
            raise AssertionError("fault oracle only freezes base64 decoding")
        try:
            base64.b64decode(
                operation["payload_ascii"],
                validate=True,
            )
        except (binascii.Error, ValueError):
            return manifest["normalization_and_redaction"][
                "required_failure_codes"
            ]["malformed_transfer_encoding"]
        return "accepted"

    if operation_type == "strict_text_decode":
        if operation["charset"].lower() not in manifest[
            "normalization_and_redaction"
        ]["supported_text_charsets"]:
            return manifest["normalization_and_redaction"][
                "required_failure_codes"
            ]["unsupported_charset"]
        bytes.fromhex(operation["payload_hex"]).decode(
            operation["charset"],
            errors="strict",
        )
        return "accepted"

    if operation_type == "replace_attachment_filename":
        value = operation["value"]
        if PurePosixPath(value).name != value or "\\" in value:
            return "unsafe_attachment_filename"
        return "accepted"

    if operation_type == "virtual_decoded_attachment_sizes":
        sizes = operation["sizes"]
        if len(sizes) > attachment_policy["attachment_count_max"]:
            return "too_many_attachments"
        if any(
            size
            > attachment_policy["single_decoded_attachment_bytes_max"]
            for size in sizes
        ):
            return "attachment_too_large"
        if sum(sizes) > attachment_policy[
            "total_decoded_attachment_bytes_max"
        ]:
            return "attachment_total_too_large"
        return "accepted"

    if operation_type == "strict_base64_decode":
        try:
            base64.b64decode(
                operation["payload_ascii"],
                validate=True,
            )
        except (binascii.Error, ValueError):
            return "attachment_decode_failed"
        return "accepted"

    if operation_type == "inject_stage_failure":
        return {
            "attachment_redaction": "attachment_redaction_failed",
            "source_redaction": "redaction_failed",
            "assisted_authoring_provider": "assist_unavailable",
        }[operation["stage"]]

    if operation_type == "replace_thread_parent":
        cases = _fixture_cases(manifest)
        known_parent_ids = {
            _normalized_message_id(str(_parse_fixture(cases[case_id])["Message-ID"]))
            for case_id in fault_case["precondition_case_ids"]
        }
        replacement_ids = {
            _normalized_message_id(f"<{message_id}>")
            for field in ("references", "in_reply_to")
            for message_id in MESSAGE_ID_PATTERN.findall(operation[field])
        }
        if not replacement_ids <= known_parent_ids:
            return "thread_parent_not_found"
        return "accepted_new_version"

    if operation_type == "mutate_candidate_source_link":
        fixture = _fixture_cases(manifest)[
            fault_case["base_fixture_case_id"]
        ]
        message = _parse_fixture(fixture)
        parts, _ = _normalized_redacted_parts(
            message,
            fixture["customer_terms"],
            manifest,
        )
        candidate = deepcopy(
            fixture["expected_candidates"][operation["candidate_index"]]
        )
        candidate[operation["field"]] += operation["delta"]
        if not _candidate_source_link_is_valid(
            candidate,
            fixture,
            parts,
        ):
            return "source_link_violation"
        return "accepted"

    raise AssertionError(f"unknown deterministic operation: {operation_type}")


def _evaluate_physical_outcome(manifest, failure):
    cases = _fixture_cases(manifest)
    case_id = failure["fixture_case_id"]
    fixture = cases[case_id]
    message = _parse_fixture(fixture)

    if failure["case"] == "duplicate_import":
        return "duplicate_replay"
    if failure["case"] == "changed_thread_content":
        root = cases["thread-root"]
        if (
            root["expected_source_id"] == fixture["expected_source_id"]
            and root["expected_message_key"]
            != fixture["expected_message_key"]
        ):
            return "accepted_new_version"
    if failure["case"] == "changed_content_same_message_id":
        root = cases["thread-root"]
        root_message = _parse_fixture(root)
        if (
            str(root_message["Message-ID"]) == str(message["Message-ID"])
            and root["sha256"] != fixture["sha256"]
        ):
            return "source_identity_conflict"
    if failure["case"] == "sender_ambiguity":
        if len(message.get_all("From", [])) != 1:
            return "sender_ambiguous"
    if failure["case"] == "missing_body":
        if not _inline_plain_parts(message):
            return "missing_body"
    if failure["case"] == "oversized_content":
        decoded = _inline_plain_parts(message)[0].get_payload(decode=True)
        if len(decoded) > manifest["content_limits"][
            "normalized_inline_body_bytes_max"
        ]:
            return "body_too_large"
    if failure["case"] == "unsupported_attachment":
        attachment = list(message.iter_attachments())[0]
        allowed = {
            (entry["media_type"], extension)
            for entry in manifest["attachment_policy"]["allowlist"]
            for extension in entry["extensions"]
        }
        observed = (
            attachment.get_content_type(),
            Path(attachment.get_filename()).suffix,
        )
        if observed not in allowed:
            return "unsupported_attachment"
    if failure["case"] == "html_plain_text_disagreement":
        alternatives = {
            part.get_content_type(): part.get_content()
            for part in message.walk()
            if part.get_content_type() in {"text/plain", "text/html"}
        }
        if re.findall(r"\d+%", alternatives["text/plain"]) != re.findall(
            r"\d+%",
            alternatives["text/html"],
        ):
            return "alternative_disagreement"
    if failure["case"] == "missing_stable_identity":
        if not message.get_all("Message-ID", []):
            return "missing_message_id"
    raise AssertionError(f"physical outcome not executable: {failure['case']}")


def test_wave2_manifest_freezes_the_exact_synthetic_rfc822_suite():
    manifest = _load_manifest()
    fixture_set = manifest["fixture_set"]
    fixtures = fixture_set["fixtures"]
    fixture_paths = {_fixture_path(fixture) for fixture in fixtures}

    assert manifest["status"] == "FROZEN"
    assert manifest["manifest_version"] == "1.0.0"
    assert fixture_set["synthetic_only"] is True
    assert len(fixtures) == fixture_set["case_count"] == 11
    assert len({fixture["case_id"] for fixture in fixtures}) == len(fixtures)
    assert len({fixture["path"] for fixture in fixtures}) == len(fixtures)
    assert fixture_paths == set(FIXTURE_DIRECTORY.glob("*.eml"))

    for fixture in fixtures:
        path = _fixture_path(fixture)
        fixture_bytes = path.read_bytes()
        assert path.parent == FIXTURE_DIRECTORY
        assert path.suffix == ".eml"
        assert len(fixture_bytes) == fixture["raw_bytes"]
        assert hashlib.sha256(fixture_bytes).hexdigest() == fixture["sha256"]

    digest_projection = sorted(
        (
            {
                "case_id": fixture["case_id"],
                "path": fixture["path"],
                "sha256": fixture["sha256"],
            }
            for fixture in fixtures
        ),
        key=lambda item: item["case_id"],
    )
    fixture_set_digest = hashlib.sha256(
        b"exitspec-wave2-fixture-set-v1\x00"
        + _canonical_json_bytes(digest_projection)
    ).hexdigest()
    assert fixture_set["set_digest_projection"] == (
        "canonical JSON array containing only case_id, path, and sha256"
    )
    assert fixture_set["set_digest_order"] == "ascending case_id"
    assert fixture_set_digest == fixture_set["set_sha256"]

    actual_slice_counts = Counter(
        slice_name
        for fixture in fixtures
        for slice_name in fixture["slices"]
    )
    assert dict(sorted(actual_slice_counts.items())) == fixture_set[
        "case_slice_counts"
    ]
    accepted_codes = {"accepted", "accepted_new_version"}
    assert sum(
        fixture["expected_outcome_code"] in accepted_codes
        for fixture in fixtures
    ) == fixture_set["accepted_case_count"]
    assert sum(
        fixture["expected_outcome_code"] not in accepted_codes
        for fixture in fixtures
    ) == fixture_set["rejected_case_count"]
    assert sum(
        fixture.get("expected_candidate_count", 0)
        for fixture in fixtures
    ) == fixture_set["expected_candidate_count"] == 5


def test_every_fixture_is_parseable_explicitly_synthetic_and_reserved():
    manifest = _load_manifest()

    for fixture in manifest["fixture_set"]["fixtures"]:
        message = _parse_fixture(fixture)
        assert not message.defects
        assert message["X-ExitSpec-Synthetic"] == "true"

        address_headers = []
        for header_name in ("From", "To", "Cc", "Reply-To"):
            address_headers.extend(message.get_all(header_name, []))
        addresses = [
            address
            for _, address in getaddresses(address_headers)
            if address
        ]
        assert addresses
        assert all(
            address.rsplit("@", 1)[-1].endswith(".example")
            for address in addresses
        )

        identity_headers = " ".join(
            str(message.get(header_name, ""))
            for header_name in ("Message-ID", "In-Reply-To", "References")
        )
        assert all(
            identity.rsplit("@", 1)[-1].endswith(".example")
            for identity in MESSAGE_ID_PATTERN.findall(identity_headers)
        )

        raw_text = _fixture_path(fixture).read_text(
            encoding="utf-8",
            errors="strict",
        )
        assert not re.search(r"\bfw_[A-Za-z0-9]{16,}\b", raw_text)
        assert not re.search(r"\bgh[opsu]_[A-Za-z0-9]{16,}\b", raw_text)
        assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}\b", raw_text)
        assert "Bearer " not in raw_text


def test_identity_duplicate_and_follow_up_oracles_are_exact():
    manifest = _load_manifest()
    cases = _fixture_cases(manifest)

    for case_id in (
        "thread-root",
        "thread-follow-up",
        "allowed-text-attachment",
        "authority-attack",
    ):
        fixture = cases[case_id]
        message = _parse_fixture(fixture)
        assert len(message.get_all("Message-ID", [])) == 1
        message_key = _opaque_identity(
            "exitspec-rfc822-message-id-v1",
            str(message["Message-ID"]),
            "msg",
        )
        source_id = _opaque_identity(
            "exitspec-rfc822-thread-id-v1",
            _thread_root_message_id(message),
            "rfc822",
        )
        assert message_key == fixture["expected_message_key"]
        assert source_id == fixture["expected_source_id"]

    root = cases["thread-root"]
    follow_up = cases["thread-follow-up"]
    mutated = cases["thread-root-mutated"]
    root_message = _parse_fixture(root)
    mutated_message = _parse_fixture(mutated)

    assert root["expected_source_id"] == follow_up["expected_source_id"]
    assert root["expected_message_key"] != follow_up["expected_message_key"]
    assert root["expected_source_version"] == 1
    assert follow_up["expected_source_version"] == 2
    assert follow_up["precondition_case_ids"] == ["thread-root"]
    assert str(root_message["Message-ID"]) == str(mutated_message["Message-ID"])
    assert root["sha256"] != mutated["sha256"]
    assert mutated["expected_outcome_code"] == "source_identity_conflict"
    assert mutated["expected_persistence"] == "nothing_new"
    assert mutated["expected_candidate_effect"] == "no_new_candidates"

    rules = manifest["reimport_and_thread_rules"]
    assert rules["exact_duplicate_new_write_count"] == 0
    assert rules["exact_duplicate_new_candidate_count"] == 0
    assert rules["follow_up_version_increment"] == 1
    assert rules["same_message_id_changed_content_new_write_count"] == 0
    assert manifest["source_contract"]["versioning"][
        "ordering_basis"
    ].startswith("atomic accepted-ingestion")


def test_concurrent_duplicate_oracle_is_exact_for_both_commit_orders():
    manifest = _load_manifest()
    fixture = _fixture_cases(manifest)["thread-root"]
    oracle = manifest["reimport_and_thread_rules"][
        "concurrent_duplicate_oracle"
    ]

    assert oracle["fixture_case_id"] == fixture["case_id"]
    assert oracle["request_count"] == len(oracle["actors"]) == 2
    assert {
        tuple(order)
        for order in oracle["allowed_commit_orders"]
    } == {
        ("import-a", "import-b"),
        ("import-b", "import-a"),
    }
    assert oracle["expected_response_by_commit_position"] == [
        "accepted",
        "duplicate_replay",
    ]
    assert oracle["expected_response_multiset"] == [
        "accepted",
        "duplicate_replay",
    ]
    assert oracle["accepted_response_count"] == 1
    assert oracle["duplicate_replay_response_count"] == 1
    assert oracle["expected_source_id"] == fixture["expected_source_id"]
    assert oracle["expected_version_id"] == fixture["expected_version_id"]
    assert oracle["final_store"]["candidate_count"] == fixture[
        "expected_candidate_count"
    ]
    assert oracle["accepted_write_transaction_count"] == 1
    assert oracle["duplicate_new_write_count"] == 0
    assert oracle["duplicate_new_candidate_count"] == 0
    assert "before identity lookup" in oracle["atomic_boundary"]
    assert "both actors" in oracle["pre_transaction_barrier"]

    for commit_order in oracle["allowed_commit_orders"]:
        store = deepcopy(oracle["starting_store"])
        stored_source_ids = set()
        stored_version_ids = set()
        stored_candidates = []
        responses = []
        accepted_write_transactions = 0

        for actor in commit_order:
            assert actor in oracle["actors"]
            if store["idempotency_record_count"] == 0:
                responses.append("accepted")
                accepted_write_transactions += 1
                stored_source_ids.add(fixture["expected_source_id"])
                stored_version_ids.add(fixture["expected_version_id"])
                stored_candidates.extend(
                    deepcopy(fixture["expected_candidates"])
                )
                store = {
                    "thread_source_count": 1,
                    "source_version_count": 1,
                    "candidate_count": fixture[
                        "expected_candidate_count"
                    ],
                    "idempotency_record_count": 1,
                }
            else:
                before_duplicate = deepcopy(store)
                responses.append("duplicate_replay")
                assert store == before_duplicate

        assert responses == oracle[
            "expected_response_by_commit_position"
        ]
        assert Counter(responses) == Counter(
            oracle["expected_response_multiset"]
        )
        assert accepted_write_transactions == oracle[
            "accepted_write_transaction_count"
        ]
        assert store == oracle["final_store"]
        assert stored_source_ids == {fixture["expected_source_id"]}
        assert stored_version_ids == {fixture["expected_version_id"]}
        assert stored_candidates == fixture["expected_candidates"]


def test_accepted_outputs_have_exact_versions_and_non_vacuous_candidates():
    manifest = _load_manifest()
    cases = _fixture_cases(manifest)
    provenance = manifest["source_contract"]["provenance"]

    assert provenance["accepted_case_candidate_count_min"] == 1
    assert provenance["candidate_projection_policy"].startswith(
        "exactly the manifest expected_candidates"
    )

    for case_id, expected_projections in (
        EXPECTED_CANDIDATE_PROJECTIONS.items()
    ):
        fixture = cases[case_id]
        message = _parse_fixture(fixture)
        parts, _ = _normalized_redacted_parts(
            message,
            fixture["customer_terms"],
            manifest,
        )
        candidates = fixture["expected_candidates"]

        assert _redacted_header_digest(
            message,
            fixture["customer_terms"],
            manifest,
        ) == fixture["expected_redacted_header_sha256"]
        assert _expected_version_id(
            manifest,
            fixture,
        ) == fixture["expected_version_id"]
        assert fixture["expected_candidate_count"] > 0
        assert len(candidates) == fixture["expected_candidate_count"]
        assert [
            candidate["projection"] for candidate in candidates
        ] == expected_projections

        expected_span_links = {
            (
                part["part_path"],
                span["start_byte"],
                span["end_byte"],
                span["quote_sha256"],
            )
            for part in fixture["expected_parts"]
            for span in part["provenance_spans"]
        }
        actual_candidate_links = set()
        for candidate in candidates:
            assert set(candidate) == set(
                provenance["candidate_exact_fields"]
            )
            assert set(candidate["projection"]) == set(
                provenance["candidate_projection_exact_fields"]
            )
            assert candidate["candidate_type"] == "criterion"
            assert candidate["state"] == "NEEDS_REVIEW"
            assert candidate["source_id"] == fixture["expected_source_id"]
            assert candidate["source_version"] == fixture[
                "expected_source_version"
            ]
            assert candidate["version_id"] == fixture[
                "expected_version_id"
            ]
            assert candidate["message_key"] == fixture[
                "expected_message_key"
            ]
            assert _candidate_source_link_is_valid(
                candidate,
                fixture,
                parts,
            )
            actual_candidate_links.add(
                (
                    candidate["part_path"],
                    candidate["start_byte"],
                    candidate["end_byte"],
                    candidate["quote_sha256"],
                )
            )

        assert actual_candidate_links == expected_span_links
        assert len(actual_candidate_links) == len(candidates)


def test_manifest_owned_redaction_and_filename_oracles_are_reproducible():
    manifest = _load_manifest()
    cases = _fixture_cases(manifest)
    accepted_case_ids = (
        "thread-root",
        "thread-follow-up",
        "allowed-text-attachment",
        "authority-attack",
    )
    redaction_policy = manifest["normalization_and_redaction"]

    assert redaction_policy["redaction_order"] == [
        "secret",
        "customer_term",
        "email",
        "phone",
    ]
    assert set(redaction_policy["patterns"]) == {
        "secret",
        "email",
        "phone",
    }
    assert redaction_policy["patterns"]["secret"]["case_sensitive"] is True
    assert redaction_policy["patterns"]["email"]["case_sensitive"] is False
    assert redaction_policy["patterns"]["phone"]["boundary_semantics"] == (
        "ASCII digit negative lookbehind and negative lookahead"
    )
    assert redaction_policy["customer_term_semantics"][
        "case_sensitive"
    ] is False
    assert redaction_policy["customer_term_semantics"][
        "boundary_semantics"
    ] == "no implicit word boundary; match the exact literal substring"

    boundary_probe, _ = _redact(
        "EXAMPLECO (priya@customer.example) "
        "+1 202-555-0142 api_key=synthetic-value",
        ["ExampleCo"],
        manifest,
    )
    assert boundary_probe == (
        "[CUSTOMER_TERM] ([EMAIL]) [PHONE] [SECRET]"
    )
    case_probe, _ = _redact(
        "API_KEY=case-sensitive",
        [],
        manifest,
    )
    assert case_probe == "API_KEY=case-sensitive"

    for case_id in accepted_case_ids:
        fixture = cases[case_id]
        message = _parse_fixture(fixture)
        actual_parts, actual_redaction_counts = _normalized_redacted_parts(
            message,
            fixture["customer_terms"],
            manifest,
        )
        expected_parts = {
            part["part_path"]: part
            for part in fixture["expected_parts"]
        }

        assert actual_redaction_counts == fixture[
            "expected_redaction_counts"
        ]
        assert {
            part["part_path"] for part in actual_parts
        } == set(expected_parts)
        for part in actual_parts:
            part_path = part["part_path"]
            redacted_text = part["redacted_text"]
            redacted_bytes = redacted_text.encode("utf-8")
            expected_part = expected_parts[part_path]
            assert len(redacted_bytes) == expected_part["redacted_bytes"]
            assert hashlib.sha256(redacted_bytes).hexdigest() == expected_part[
                "redacted_text_sha256"
            ]
            assert part["redacted_filename_sha256"] == expected_part[
                "redacted_filename_sha256"
            ]
            for span in expected_part["provenance_spans"]:
                selected = redacted_bytes[
                    span["start_byte"] : span["end_byte"]
                ]
                assert selected.decode("utf-8") == span["quote"]
                assert hashlib.sha256(selected).hexdigest() == span[
                    "quote_sha256"
                ]

        projected_persisted_text = "\n".join(
            part["redacted_text"] for part in actual_parts
        )
        projected_redacted_headers = []
        for header_name in ("From", "To", "Subject"):
            redacted_header, _ = _redact(
                str(message.get(header_name, "")),
                fixture["customer_terms"],
                manifest,
            )
            projected_redacted_headers.append(redacted_header)
        projected_persisted_text += "\n".join(projected_redacted_headers)
        for customer_term in fixture["customer_terms"]:
            assert customer_term.casefold() not in (
                projected_persisted_text.casefold()
            )
        for kind in ("secret", "email", "phone"):
            assert not _compiled_redaction_pattern(
                manifest,
                kind,
            ).search(projected_persisted_text)
        assert fixture["expected_candidate_state"] == "NEEDS_REVIEW"

    attachment_fixture = cases["allowed-text-attachment"]
    attachment_message = _parse_fixture(attachment_fixture)
    attachment_parts, _ = _normalized_redacted_parts(
        attachment_message,
        attachment_fixture["customer_terms"],
        manifest,
    )
    attachment_part = next(
        part
        for part in attachment_parts
        if part["part_path"] == "attachment:text/plain:0"
    )
    filename_oracle = attachment_fixture["expected_attachments"][0]
    assert attachment_part["redacted_filename"] == filename_oracle[
        "redacted_filename"
    ]
    assert attachment_part[
        "redacted_filename_sha256"
    ] == filename_oracle["redacted_filename_sha256"]
    assert "ExampleCo" not in attachment_part["redacted_filename"]
    assert "priya@customer.example" not in attachment_part[
        "redacted_filename"
    ]


def test_attachment_body_and_alternative_policy_fixtures_hit_exact_edges():
    manifest = _load_manifest()
    cases = _fixture_cases(manifest)
    attachment_policy = manifest["attachment_policy"]
    content_limits = manifest["content_limits"]

    allowed = _parse_fixture(cases["allowed-text-attachment"])
    allowed_attachments = list(allowed.iter_attachments())
    assert len(allowed_attachments) == 1
    attachment = allowed_attachments[0]
    decoded_attachment = attachment.get_payload(decode=True)
    allowlist = {
        (entry["media_type"], extension)
        for entry in attachment_policy["allowlist"]
        for extension in entry["extensions"]
    }
    assert allowlist == {("text/plain", ".txt")}
    assert (
        attachment.get_content_type(),
        Path(attachment.get_filename()).suffix,
    ) in allowlist
    assert len(decoded_attachment) <= attachment_policy[
        "single_decoded_attachment_bytes_max"
    ]

    unsupported = _parse_fixture(cases["unsupported-attachment"])
    unsupported_attachment = list(unsupported.iter_attachments())[0]
    assert (
        unsupported_attachment.get_content_type(),
        Path(unsupported_attachment.get_filename()).suffix,
    ) not in allowlist
    assert cases["unsupported-attachment"]["expected_outcome_code"] == (
        "unsupported_attachment"
    )

    missing_body = _parse_fixture(cases["missing-body"])
    assert _inline_plain_parts(missing_body) == []

    oversized = _parse_fixture(cases["oversized-body"])
    oversized_body = _inline_plain_parts(oversized)[0]
    oversized_decoded = oversized_body.get_payload(decode=True)
    assert len(oversized_decoded) > content_limits[
        "normalized_inline_body_bytes_max"
    ]

    ambiguous = _parse_fixture(cases["sender-ambiguous"])
    assert len(ambiguous.get_all("From", [])) == 2

    missing_identity = _parse_fixture(cases["missing-message-id"])
    assert missing_identity.get_all("Message-ID", []) == []

    disagreement = _parse_fixture(cases["html-plain-disagreement"])
    plain = next(
        part
        for part in disagreement.walk()
        if part.get_content_type() == "text/plain"
    ).get_content()
    html = next(
        part
        for part in disagreement.walk()
        if part.get_content_type() == "text/html"
    ).get_content()
    assert re.findall(r"\d+%", plain) == ["95%"]
    assert re.findall(r"\d+%", html) == ["90%"]
    assert cases["html-plain-disagreement"]["expected_outcome_code"] == (
        "alternative_disagreement"
    )


def test_failure_matrix_is_complete_typed_and_fail_closed():
    manifest = _load_manifest()
    failures = {
        failure["case"]: failure
        for failure in manifest["required_failure_matrix"]
    }

    assert set(failures) == {
        "missing_synthetic_marker",
        "invalid_synthetic_marker",
        "fixture_digest_mismatch",
        "raw_message_size_limit",
        "header_count_limit",
        "unfolded_header_size_limit",
        "mime_depth_limit",
        "mime_part_count_limit",
        "malformed_transfer_encoding",
        "unsupported_charset",
        "duplicate_import",
        "changed_thread_content",
        "changed_content_same_message_id",
        "sender_ambiguity",
        "missing_body",
        "oversized_content",
        "unsupported_attachment",
        "unsafe_attachment_filename",
        "attachment_per_file_size_limit",
        "attachment_total_size_limit",
        "attachment_count_limit",
        "attachment_decode_failure",
        "attachment_redaction_failure",
        "html_plain_text_disagreement",
        "redaction_failure",
        "assisted_authoring_provider_failure",
        "unknown_thread_parent",
        "source_link_violation",
        "missing_stable_identity",
    }
    assert len(failures) == manifest["required_outcome_count"] == 29
    assert failures["missing_synthetic_marker"]["expected_code"] == (
        manifest["source_contract"]["synthetic_marker"][
            "missing_or_other_value_code"
        ]
    )
    assert failures["invalid_synthetic_marker"]["expected_code"] == (
        manifest["source_contract"]["synthetic_marker"][
            "missing_or_other_value_code"
        ]
    )
    assert failures["fixture_digest_mismatch"]["expected_code"] == (
        manifest["source_contract"]["digests"]["digest_mismatch_code"]
    )
    assert failures["duplicate_import"]["expected_code"] == "duplicate_replay"
    assert failures["duplicate_import"]["is_failure"] is False
    assert failures["changed_thread_content"]["expected_code"] == (
        "accepted_new_version"
    )
    assert failures["changed_thread_content"]["is_failure"] is False
    assert failures["redaction_failure"]["persistence"] == "nothing"
    assert failures["redaction_failure"]["candidate_effect"] == "no_candidates"
    assert failures["assisted_authoring_provider_failure"][
        "persistence"
    ] == "redacted_source_envelope_only"
    assert failures["assisted_authoring_provider_failure"][
        "candidate_effect"
    ] == "no_provider_candidates"

    for failure in failures.values():
        assert failure["expected_code"]
        assert failure["safe_next_action"].endswith(".")
        if failure["is_failure"]:
            assert failure["retry"] is False
            assert failure["persistence"] in {
                "nothing",
                "nothing_new",
                "redacted_source_envelope_only",
            }
            assert failure["candidate_effect"] in {
                "no_candidates",
                "no_new_candidates",
                "no_provider_candidates",
            }


def test_every_declared_outcome_has_an_executable_fixture_or_fault_oracle():
    manifest = _load_manifest()
    fault_cases = {
        fault_case["fault_case_id"]: fault_case
        for fault_case in manifest["deterministic_fault_cases"]
    }
    required_outcomes = manifest["required_failure_matrix"]

    assert len(fault_cases) == manifest[
        "deterministic_fault_case_count"
    ] == 20
    referenced_faults = {
        outcome["fault_case_id"]
        for outcome in required_outcomes
        if outcome["fault_case_id"] is not None
    }
    assert referenced_faults == set(fault_cases)

    for outcome in required_outcomes:
        fault_case_id = outcome["fault_case_id"]
        if fault_case_id is None:
            observed_code = _evaluate_physical_outcome(
                manifest,
                outcome,
            )
        else:
            fault_case = fault_cases[fault_case_id]
            assert fault_case["base_fixture_case_id"] == outcome[
                "fixture_case_id"
            ]
            observed_code = _evaluate_fault_case(
                manifest,
                fault_case,
            )
            assert observed_code == fault_case["expected_code"]
        assert observed_code == outcome["expected_code"]

    for fault_case_id, fault_case in fault_cases.items():
        assert fault_case["new_persistence_count"] == 0
        assert fault_case["new_candidate_count"] == 0
        outcome = next(
            item
            for item in required_outcomes
            if item["fault_case_id"] == fault_case_id
        )
        if fault_case_id == "provider-terminal-failure":
            assert outcome["persistence"] == "redacted_source_envelope_only"
            assert outcome["candidate_effect"] == "no_provider_candidates"
        else:
            assert outcome["persistence"] in {"nothing", "nothing_new"}
            assert outcome["candidate_effect"] in {
                "no_candidates",
                "no_new_candidates",
            }

    matrix_codes = {
        outcome["expected_code"]
        for outcome in required_outcomes
    }
    assert set(
        manifest["attachment_policy"]["required_failure_codes"]
    ) <= matrix_codes
    assert set(
        manifest["content_limits"]["required_failure_codes"].values()
    ) <= matrix_codes
    assert set(
        manifest["normalization_and_redaction"][
            "required_failure_codes"
        ].values()
    ) <= matrix_codes
    assert manifest["source_contract"]["synthetic_marker"][
        "missing_or_other_value_code"
    ] in matrix_codes
    assert manifest["source_contract"]["digests"][
        "digest_mismatch_code"
    ] in matrix_codes


def test_authority_privacy_secret_timing_and_receipt_gates_are_binary():
    manifest = _load_manifest()
    authority = manifest["source_contract"]["authority"]
    quality = manifest["quality_gates"]
    timing = manifest["timing_gate"]
    transport = manifest["transport_gates"]
    receipt = manifest["receipt_contract"]

    assert authority["email_role"] == "untrusted_source_only"
    assert authority["candidate_state"] == "NEEDS_REVIEW"
    for field, value in authority.items():
        if field.startswith("email_may_"):
            assert value is False

    for gate, minimum in quality.items():
        if gate.endswith("_rate_min"):
            assert minimum == 1.0
        if gate.endswith("_count_max"):
            assert minimum == 0

    assert timing["start"].startswith("immediately before dispatch")
    assert timing["end"].startswith("first animation frame")
    assert timing["measured_run_count"] == (
        timing["measured_runs_per_accepted_case"]
        * timing["measured_accepted_case_count"]
    )
    assert timing["each_successful_import_elapsed_ms_max"] == 60000
    assert timing["successful_import_latency_p95_ms_max"] == 10000
    assert timing["p95_method"].startswith("nearest-rank")
    assert timing["refusal_start"].startswith("immediately before dispatch")
    assert timing["refusal_end"].startswith("first animation frame")
    assert timing["typed_local_refusal_elapsed_ms_max"] == 5000
    assert timing["run_isolation"] == {
        "store": (
            "new empty in-memory source, candidate, and idempotency store "
            "for every warmup and measured run"
        ),
        "browser": (
            "reset source selection, candidate rendering, provenance panel, "
            "outcome banner, and timing recorder before setup"
        ),
        "server_process": (
            "may remain running, but no source, candidate, idempotency, or "
            "operation state may be reused"
        ),
        "fixture_verification": (
            "verify fixture_id and frozen SHA-256 before setup"
        ),
        "setup_is_measured": False,
        "teardown_is_measured": False,
    }
    assert timing["accepted_case_setup"] == {
        "thread-root": "empty isolated store",
        "thread-follow-up": (
            "seed thread-root to accepted source_version 1 outside the "
            "measured interval"
        ),
        "allowed-text-attachment": "empty isolated store",
        "authority-attack": "empty isolated store",
    }
    assert timing["follow_up_seed_assertions"] == {
        "seed_case_id": "thread-root",
        "seed_outside_measured_interval": True,
        "expected_source_version_before_start": 1,
        "expected_candidate_count_before_start": 2,
        "clear_seed_timing_samples_before_start": True,
    }
    duplicate_timing = timing["duplicate_timing"]
    assert duplicate_timing[
        "reported_separately_from_successful_import_latency"
    ] is True
    assert duplicate_timing["runs"] == 5
    assert duplicate_timing["each_elapsed_ms_max"] == 5000
    assert duplicate_timing["latency_p95_ms_max"] == 2000
    assert duplicate_timing["new_persistence_count"] == 0
    assert duplicate_timing["new_candidate_count"] == 0
    assert "outside the interval" in duplicate_timing["setup"]
    assert timing["teardown"] == {
        "when": "after the end timestamp and evidence capture",
        "destroy_store": True,
        "clear_browser_source_state": True,
        "discard_timing_recorder": True,
        "assert_no_pending_source_tasks": True,
    }
    assert timing["external_provider_required"] is False

    assert transport == {
        "streaming_events_used": False,
        "accepted_message_loss_count_max": 0,
        "duplicate_candidate_count_max": 0,
        "reconnect_applicable": False,
        "reconnect_recovery_seconds_max": None,
        "source_adapter_external_egress_count_max": 0,
    }

    assert receipt["required_for_every_terminal_outcome"] is True
    assert {
        "raw_rfc822",
        "raw_header",
        "raw_message_id",
        "sender_address",
        "recipient_address",
        "subject_text",
        "body_text",
        "attachment_filename",
        "attachment_content",
        "customer_term",
        "secret",
        "candidate_text",
        "provider_request",
        "provider_response",
    } == set(receipt["forbidden_content"])


def test_scope_excludes_real_mailbox_transport_and_customer_email():
    manifest = _load_manifest()
    non_goals = " ".join(manifest["non_goals"]).lower()

    for excluded in (
        "oauth",
        "live gmail",
        "outlook",
        "imap",
        "remote mailbox",
        "sending",
        "real customer email",
        "arbitrary email upload",
        "production identity",
    ):
        assert excluded in non_goals
    assert manifest["fixture_set"]["synthetic_only"] is True
    assert manifest["source_contract"]["synthetic_marker"] == {
        "header": "X-ExitSpec-Synthetic",
        "required_value": "true",
        "missing_or_other_value_code": "source_not_approved",
    }
    assert manifest["source_contract"]["import_authorization"] == {
        "employee_action_required": True,
        "server_manifest_fixture_id_required": True,
        "browser_supplied_path_or_rfc822_allowed": False,
        "background_prefetch_or_import_allowed": False,
        "one_action_imports_at_most_one_fixture": True,
    }
