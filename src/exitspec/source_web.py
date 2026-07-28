"""Frozen Wave-2 guided source transport and projection boundary.

This module deliberately keeps the source HTTP policy, synthetic fixture
selection, immutable source store, and safe review projection separate from the
generic demo router.  Raw RFC822 data and private source identities never leave
the adapter/store boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from .adapters.deterministic_tool_selection import (
    DeterministicToolSelectionAdapter,
)
from .adapters.rfc822 import (
    Rfc822PreparationError,
    prepare_support_agent_email_fixture,
)
from .demo_data import (
    SupportAgentEmailResourceError,
    SupportAgentSourceWebContractError,
    support_agent_email_paths,
    support_agent_source_web_contract,
)
from .models import (
    Criterion,
    CriterionDraft,
    DiscoveryPack,
    DiscoveryTranscript,
    DraftStatus,
    Metric,
    ProportionRule,
    TranscriptLine,
    TranscriptSpan,
)
from .source_models import (
    SOURCE_TYPE,
    PreparedCandidateDraft,
    PreparedSourceEnvelope,
)
from .source_store import (
    SourceImportOutcome,
    SourceImportReceipt,
    SourceStore,
    SourceStoreCounts,
)


SOURCE_CONTRACT_VERSION = "wave2-source-web-v1"
SOURCE_SCOPE_PATH = "/api/source"
SOURCE_CATALOG_PATH = "/api/source/fixtures"
SOURCE_IMPORT_PATH = "/api/source/import"
SOURCE_REQUEST_LIMIT_BYTES = 65_536
SOURCE_OBSERVED_READ_LIMIT_BYTES = SOURCE_REQUEST_LIMIT_BYTES + 1
SOURCE_SPEAKER = "synthetic_email_source"
SOURCE_AUTHORITY_PUBLIC = "UNTRUSTED_SOURCE_ONLY"
GUIDED_SOURCE_CASE_IDS = ("thread-root", "authority-attack")
_ACCEPTED_CONTENT_TYPES = (
    "application/json",
    "application/json; charset=utf-8",
)
_CANONICAL_CONTENT_LENGTH = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_JSON_VALUE_MARKER = object()
_JSON_NUMBER_MARKER = object()


@dataclass(frozen=True, slots=True)
class _RefusalDefinition:
    status: HTTPStatus
    message: str
    next_action: str


_REFUSALS: Mapping[str, _RefusalDefinition] = {
    "invalid_local_host": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The source endpoint requires the exact local server authority.",
        "use_exact_local_server_authority",
    ),
    "forbidden_origin": _RefusalDefinition(
        HTTPStatus.FORBIDDEN,
        "The request origin is not the exact local application origin.",
        "use_exact_local_application_origin",
    ),
    "unknown_source_route": _RefusalDefinition(
        HTTPStatus.NOT_FOUND,
        "The requested guided source route does not exist.",
        "use_frozen_source_endpoint",
    ),
    "method_not_allowed": _RefusalDefinition(
        HTTPStatus.METHOD_NOT_ALLOWED,
        "The guided source endpoint does not allow this method.",
        "use_frozen_endpoint_method",
    ),
    "route_parameters_not_allowed": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "Guided source routes do not accept URL parameters.",
        "remove_route_parameters",
    ),
    "get_body_not_allowed": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The guided fixture catalog request cannot include a body.",
        "send_catalog_get_without_body",
    ),
    "forbidden_fetch_site": _RefusalDefinition(
        HTTPStatus.FORBIDDEN,
        "The request fetch context is not allowed for this local source route.",
        "use_same_origin_local_application",
    ),
    "unsupported_media_type": _RefusalDefinition(
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        "The source import requires one exact supported JSON media type.",
        "send_exact_application_json",
    ),
    "request_length_required": _RefusalDefinition(
        HTTPStatus.LENGTH_REQUIRED,
        "The source import requires one bounded Content-Length.",
        "send_bounded_content_length",
    ),
    "invalid_content_length": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The source import Content-Length is not canonical.",
        "send_one_canonical_decimal_content_length",
    ),
    "source_request_too_large": _RefusalDefinition(
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        "The source import exceeds the frozen request-size limit.",
        "reduce_request_below_or_equal_to_65536_bytes",
    ),
    "content_length_mismatch": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The observed source request body does not match Content-Length.",
        "send_body_matching_content_length",
    ),
    "empty_json_body": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The source import requires one JSON object.",
        "send_exact_json_object",
    ),
    "malformed_json": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The source import body is not a complete UTF-8 JSON document.",
        "send_valid_utf8_json",
    ),
    "duplicate_json_member": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The source import JSON contains a duplicate object member.",
        "send_unique_json_object_member_names",
    ),
    "json_object_required": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The source import JSON top level must be an object.",
        "send_json_object",
    ),
    "invalid_source_request": _RefusalDefinition(
        HTTPStatus.BAD_REQUEST,
        "The source import request does not match the exact frozen shape.",
        "send_only_fixture_case_id",
    ),
    "source_not_approved": _RefusalDefinition(
        HTTPStatus.NOT_FOUND,
        "The selected synthetic source is not approved for this guided path.",
        "choose_guided_fixture",
    ),
    "source_import_refused": _RefusalDefinition(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "The approved synthetic source could not cross the safe source boundary.",
        "inspect_safe_source_outcome",
    ),
    "source_change_requires_reset": _RefusalDefinition(
        HTTPStatus.CONFLICT,
        "Reset the workflow before selecting a different guided source.",
        "reset_workflow_before_changing_source",
    ),
    "source_import_locked": _RefusalDefinition(
        HTTPStatus.CONFLICT,
        "Reset the workflow before importing after downstream agreement work.",
        "reset_workflow_before_import",
    ),
}


class SourceWebRefusal(RuntimeError):
    """Content-free typed refusal used at the source/session boundary."""

    def __init__(self, code: str) -> None:
        if code not in _REFUSALS:
            code = "source_import_refused"
        self.code = code
        super().__init__(code)


class SourceWebRuntimeError(RuntimeError):
    """Safe terminal failure for parser, resource, projection, or store faults."""

    code = "source_import_refused"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class SourceWebResponse:
    status: HTTPStatus
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SourceWebRequest:
    """Transport-only request facts supplied by the local HTTP handler."""

    method: str
    target: str
    server_port: int
    header_values: Callable[[str], Sequence[str]]
    read_body: Callable[[int, int], bytes]


@dataclass(frozen=True, slots=True)
class SourceReviewControl:
    draft_id: str
    allowed_actions: tuple[str, ...]
    can_edit_rule: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "allowed_actions": list(self.allowed_actions),
            "can_edit_rule": self.can_edit_rule,
        }


@dataclass(frozen=True, slots=True)
class SourceIntakeRecord:
    """Only the safe web-local facts needed to render source intake state."""

    fixture_case_id: str
    label: str
    source_version: int
    message_count: int
    controls: tuple[SourceReviewControl, ...]

    @property
    def proposal_count(self) -> int:
        return len(self.controls)

    def can_edit_rule(self, draft_id: str) -> bool:
        return any(
            control.draft_id == draft_id and control.can_edit_rule
            for control in self.controls
        )

    def public_payload(
        self,
        drafts: Sequence[CriterionDraft],
    ) -> dict[str, Any]:
        expected_ids = tuple(control.draft_id for control in self.controls)
        actual_ids = tuple(draft.id for draft in drafts)
        if actual_ids != expected_ids:
            raise SourceWebRuntimeError()
        pending_count = sum(
            draft.status is DraftStatus.NEEDS_REVIEW for draft in drafts
        )
        return {
            "status": "NEEDS_REVIEW" if pending_count else "REVIEWED",
            "source_type": SOURCE_TYPE,
            "fixture_case_id": self.fixture_case_id,
            "label": self.label,
            "source_version": self.source_version,
            "message_count": self.message_count,
            "proposal_count": self.proposal_count,
            "pending_count": pending_count,
            "redaction_applied": True,
            "authority": SOURCE_AUTHORITY_PUBLIC,
            "review_controls": [
                control.to_dict() for control in self.controls
            ],
        }


@dataclass(frozen=True, slots=True)
class SourcePublication:
    receipt: SourceImportReceipt
    discovery_pack: DiscoveryPack
    intake: SourceIntakeRecord


@dataclass(slots=True)
class _JsonFrame:
    """One iterative JSON container frame.

    Only the root object's direct members are materialized because gate 10
    needs exactly those values. Nested values remain opaque after their syntax
    and duplicate names have been validated.
    """

    kind: str
    state: str
    seen_names: set[str] | None = None
    current_name: str | None = None
    root_members: dict[str, object] | None = None


def is_source_pipeline_target(target: str) -> bool:
    """Return whether the parsed request path belongs to the frozen pipeline."""

    try:
        path = urlparse(target).path
    except (TypeError, ValueError):
        return False
    return path == SOURCE_SCOPE_PATH or path.startswith(
        SOURCE_SCOPE_PATH + "/"
    )


def source_refusal_response(code: str) -> SourceWebResponse:
    definition = _REFUSALS.get(
        code,
        _REFUSALS["source_import_refused"],
    )
    resolved_code = code if code in _REFUSALS else "source_import_refused"
    return SourceWebResponse(
        status=definition.status,
        payload={
            "contract_version": SOURCE_CONTRACT_VERSION,
            "error": {
                "code": resolved_code,
                "message": definition.message,
                "retryable": False,
                "next_action": definition.next_action,
            },
            "state_unchanged": True,
        },
    )


def _header_values(
    request: SourceWebRequest,
    name: str,
) -> tuple[str, ...]:
    try:
        values = request.header_values(name)
    except Exception:
        return ()
    return tuple(value for value in values if isinstance(value, str))


def _validated_authority(
    request: SourceWebRequest,
) -> str | None:
    if not isinstance(request.target, str):
        return None
    try:
        target = urlparse(request.target)
    except (TypeError, ValueError):
        return None
    if (
        target.scheme
        or target.netloc
        or not request.target.startswith("/")
    ):
        return None
    hosts = _header_values(request, "Host")
    if len(hosts) != 1:
        return None
    host = hosts[0]
    allowed = {
        "127.0.0.1:{0}".format(request.server_port),
        "localhost:{0}".format(request.server_port),
        "[::1]:{0}".format(request.server_port),
    }
    return host if host in allowed else None


def _origin_is_exact(
    request: SourceWebRequest,
    authority: str,
    *,
    allow_absent: bool,
) -> bool:
    origins = _header_values(request, "Origin")
    if not origins:
        return allow_absent
    return len(origins) == 1 and origins[0] == "http://{0}".format(
        authority
    )


def _fetch_site_allowed(
    request: SourceWebRequest,
    *,
    catalog: bool,
) -> bool:
    values = _header_values(request, "Sec-Fetch-Site")
    if not values:
        return True
    allowed = {"none", "same-origin"} if catalog else {"same-origin"}
    return len(values) == 1 and values[0] in allowed


def _has_route_parameters(target: str, parsed: Any) -> bool:
    return bool(
        parsed.params
        or parsed.query
        or parsed.fragment
        or "?" in target
        or "#" in target
    )


def _parse_json_document(body: bytes) -> object:
    if not body or all(byte in b" \t\r\n\f\v" for byte in body):
        raise SourceWebRefusal("empty_json_body")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SourceWebRefusal("malformed_json") from None

    def retain_number_without_runtime_conversion(_: str) -> object:
        return _JSON_NUMBER_MARKER

    decoder = json.JSONDecoder(
        parse_int=retain_number_without_runtime_conversion,
        parse_float=retain_number_without_runtime_conversion,
    )
    stack: list[_JsonFrame] = []
    index = 0
    root_set = False
    root_value: object = None
    duplicate_member = False

    def malformed() -> None:
        raise SourceWebRefusal("malformed_json")

    def accept_value(value: object) -> None:
        nonlocal root_set, root_value
        if not stack:
            if root_set:
                malformed()
            root_set = True
            root_value = value
            return

        frame = stack[-1]
        if frame.kind == "array":
            if frame.state not in {"value_or_end", "value"}:
                malformed()
            frame.state = "comma_or_end"
            return

        if frame.state != "value" or frame.current_name is None:
            malformed()
        if frame.root_members is not None:
            frame.root_members[frame.current_name] = value
        frame.current_name = None
        frame.state = "comma_or_end"

    while True:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        if index == len(text):
            break

        character = text[index]
        if character == "{":
            is_root = not stack and not root_set
            members: dict[str, object] | None = {} if is_root else None
            accept_value(
                members if members is not None else _JSON_VALUE_MARKER
            )
            stack.append(
                _JsonFrame(
                    kind="object",
                    state="key_or_end",
                    seen_names=set(),
                    root_members=members,
                )
            )
            index += 1
            continue

        if character == "[":
            accept_value(_JSON_VALUE_MARKER)
            stack.append(_JsonFrame(kind="array", state="value_or_end"))
            index += 1
            continue

        if character == "}":
            if (
                not stack
                or stack[-1].kind != "object"
                or stack[-1].state
                not in {"key_or_end", "comma_or_end"}
            ):
                malformed()
            stack.pop()
            index += 1
            continue

        if character == "]":
            if (
                not stack
                or stack[-1].kind != "array"
                or stack[-1].state
                not in {"value_or_end", "comma_or_end"}
            ):
                malformed()
            stack.pop()
            index += 1
            continue

        if character == ",":
            if not stack or stack[-1].state != "comma_or_end":
                malformed()
            frame = stack[-1]
            frame.state = "key" if frame.kind == "object" else "value"
            index += 1
            continue

        if character == ":":
            if (
                not stack
                or stack[-1].kind != "object"
                or stack[-1].state != "colon"
            ):
                malformed()
            stack[-1].state = "value"
            index += 1
            continue

        if character == '"':
            try:
                value, end = decoder.raw_decode(text, index)
            except (json.JSONDecodeError, ValueError):
                malformed()
            if not isinstance(value, str):
                malformed()
            if (
                stack
                and stack[-1].kind == "object"
                and stack[-1].state in {"key_or_end", "key"}
            ):
                frame = stack[-1]
                if frame.seen_names is None:
                    malformed()
                if value in frame.seen_names:
                    duplicate_member = True
                else:
                    frame.seen_names.add(value)
                frame.current_name = value
                frame.state = "colon"
            else:
                accept_value(value)
            index = end
            continue

        if character == "-" or character.isdigit():
            try:
                value, end = decoder.raw_decode(text, index)
            except (json.JSONDecodeError, ValueError):
                malformed()
            if value is not _JSON_NUMBER_MARKER:
                malformed()
            accept_value(value)
            index = end
            continue

        literal = next(
            (
                (token, value)
                for token, value in (
                    ("true", True),
                    ("false", False),
                    ("null", None),
                )
                if text.startswith(token, index)
            ),
            None,
        )
        if literal is None:
            malformed()
        token, value = literal
        accept_value(value)
        index += len(token)

    if stack or not root_set:
        malformed()
    if duplicate_member:
        raise SourceWebRefusal("duplicate_json_member")
    return root_value


def _exact_content_length(
    request: SourceWebRequest,
) -> int:
    transfer_encodings = _header_values(request, "Transfer-Encoding")
    lengths = _header_values(request, "Content-Length")
    if transfer_encodings or not lengths:
        raise SourceWebRefusal("request_length_required")
    if len(lengths) != 1 or _CANONICAL_CONTENT_LENGTH.fullmatch(
        lengths[0]
    ) is None:
        raise SourceWebRefusal("invalid_content_length")
    value = lengths[0]
    if len(value) > 5 or (
        len(value) == 5 and value > str(SOURCE_REQUEST_LIMIT_BYTES)
    ):
        raise SourceWebRefusal("source_request_too_large")
    resolved = int(value)
    if resolved > SOURCE_REQUEST_LIMIT_BYTES:
        raise SourceWebRefusal("source_request_too_large")
    return resolved


def handle_source_web_request(
    request: SourceWebRequest,
    *,
    catalog_payload: Callable[[], dict[str, Any]],
    import_fixture: Callable[[str], dict[str, Any]],
) -> SourceWebResponse | None:
    """Execute the frozen twelve gates and invoke stateful work only at gate 12."""

    try:
        parsed = urlparse(request.target)
    except (TypeError, ValueError):
        return None
    path = parsed.path
    if not (
        path == SOURCE_SCOPE_PATH
        or path.startswith(SOURCE_SCOPE_PATH + "/")
    ):
        return None

    # Gate 1: exact local authority, then catalog Origin when present.
    authority = _validated_authority(request)
    if authority is None:
        return source_refusal_response("invalid_local_host")
    if path == SOURCE_CATALOG_PATH and not _origin_is_exact(
        request,
        authority,
        allow_absent=True,
    ):
        return source_refusal_response("forbidden_origin")

    # Gate 2: path lookup precedes method lookup.
    expected_method = {
        SOURCE_CATALOG_PATH: "GET",
        SOURCE_IMPORT_PATH: "POST",
    }.get(path)
    if expected_method is None:
        return source_refusal_response("unknown_source_route")
    if request.method != expected_method:
        return source_refusal_response("method_not_allowed")

    # Gate 3: no query, semicolon params, or fragment.
    if _has_route_parameters(request.target, parsed):
        return source_refusal_response("route_parameters_not_allowed")

    if path == SOURCE_CATALOG_PATH:
        # Gate 4: a catalog GET has no body.
        transfer_encodings = _header_values(request, "Transfer-Encoding")
        lengths = _header_values(request, "Content-Length")
        if (
            transfer_encodings
            or len(lengths) > 1
            or (lengths and lengths[0] != "0")
        ):
            return source_refusal_response("get_body_not_allowed")
        try:
            if request.read_body(0, 1):
                return source_refusal_response("get_body_not_allowed")
        except Exception:
            return source_refusal_response("get_body_not_allowed")

        # Gate 6: normal same-origin navigation may omit fetch metadata.
        if not _fetch_site_allowed(request, catalog=True):
            return source_refusal_response("forbidden_fetch_site")
        try:
            payload = catalog_payload()
        except Exception:
            return source_refusal_response("source_import_refused")
        return SourceWebResponse(HTTPStatus.OK, payload)

    # Gate 5: import requires one exact local Origin.
    if not _origin_is_exact(
        request,
        authority,
        allow_absent=False,
    ):
        return source_refusal_response("forbidden_origin")

    # Gate 6: import accepts absent or exact same-origin fetch metadata.
    if not _fetch_site_allowed(request, catalog=False):
        return source_refusal_response("forbidden_fetch_site")

    # Gate 7: media type is exact, including spacing and charset spelling.
    content_types = _header_values(request, "Content-Type")
    if (
        len(content_types) != 1
        or content_types[0] not in _ACCEPTED_CONTENT_TYPES
    ):
        return source_refusal_response("unsupported_media_type")

    try:
        # Gate 8: canonical bounded length, then observed-byte equality.
        declared_length = _exact_content_length(request)
        body = request.read_body(
            declared_length,
            SOURCE_OBSERVED_READ_LIMIT_BYTES,
        )
        if not isinstance(body, bytes):
            raise SourceWebRefusal("content_length_mismatch")
        if len(body) > SOURCE_REQUEST_LIMIT_BYTES:
            raise SourceWebRefusal("source_request_too_large")
        if len(body) != declared_length:
            raise SourceWebRefusal("content_length_mismatch")

        # Gate 9: complete syntax precedes recursive duplicate scanning.
        payload = _parse_json_document(body)
        if not isinstance(payload, dict):
            raise SourceWebRefusal("json_object_required")

        # Gate 10: exactly one non-empty string member.
        if set(payload) != {"fixture_case_id"}:
            raise SourceWebRefusal("invalid_source_request")
        fixture_case_id = payload["fixture_case_id"]
        if not isinstance(fixture_case_id, str) or not fixture_case_id:
            raise SourceWebRefusal("invalid_source_request")

        # Gate 11: unknown fixture lookup precedes every workflow-state check.
        if fixture_case_id not in GUIDED_SOURCE_CASE_IDS:
            raise SourceWebRefusal("source_not_approved")

        # Gate 12 is serialized inside the session callback.
        return SourceWebResponse(
            HTTPStatus.OK,
            import_fixture(fixture_case_id),
        )
    except SourceWebRefusal as error:
        return source_refusal_response(error.code)
    except Exception:
        return source_refusal_response("source_import_refused")


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _contract_payload() -> dict[str, Any]:
    try:
        with support_agent_source_web_contract() as resource:
            payload = _thaw(resource.contract)
    except SupportAgentSourceWebContractError:
        raise SourceWebRuntimeError() from None
    if not isinstance(payload, dict):
        raise SourceWebRuntimeError()
    return payload


def _catalog_payload() -> dict[str, Any]:
    contract = _contract_payload()
    endpoints = contract.get("endpoints")
    if not isinstance(endpoints, list):
        raise SourceWebRuntimeError()
    for endpoint in endpoints:
        if (
            isinstance(endpoint, dict)
            and endpoint.get("method") == "GET"
            and endpoint.get("path") == SOURCE_CATALOG_PATH
        ):
            example = endpoint.get("exact_success_example")
            if isinstance(example, dict):
                return json.loads(
                    json.dumps(
                        example,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                )
    raise SourceWebRuntimeError()


def _guided_label(fixture_case_id: str) -> str:
    catalog = _catalog_payload()
    fixtures = catalog.get("fixtures")
    if not isinstance(fixtures, list):
        raise SourceWebRuntimeError()
    for fixture in fixtures:
        if (
            isinstance(fixture, dict)
            and fixture.get("fixture_case_id") == fixture_case_id
            and isinstance(fixture.get("label"), str)
        ):
            return fixture["label"]
    raise SourceWebRuntimeError()


def _quote_for_candidate(
    prepared: PreparedSourceEnvelope,
    candidate: PreparedCandidateDraft,
) -> str:
    matches = tuple(
        part
        for part in prepared.message.parts
        if part.part_path == candidate.part_path
    )
    if len(matches) != 1:
        raise SourceWebRuntimeError()
    encoded = matches[0].redacted_text.encode("utf-8")
    quote_bytes = encoded[candidate.start_byte : candidate.end_byte]
    if (
        not quote_bytes
        or hashlib.sha256(quote_bytes).hexdigest()
        != candidate.quote_sha256
    ):
        raise SourceWebRuntimeError()
    try:
        return quote_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SourceWebRuntimeError() from None


def _supported_accuracy_draft(
    *,
    draft_id: str,
    transcript_id: str,
    line_number: int,
    quote: str,
) -> tuple[CriterionDraft, SourceReviewControl]:
    normalized_claim = (
        "Exact tool selection passes when exact expected support-tool selection "
        "reaches at least 95% across at least 200 fixed cases in the "
        "support-tool-selection-v1 workload, and the 95% Wilson lower bound "
        "meets the same threshold."
    )
    span = TranscriptSpan(
        transcript_id=transcript_id,
        start_line=line_number,
        end_line=line_number,
        speaker=SOURCE_SPEAKER,
        quote=quote,
    )
    criterion = Criterion(
        id="RULE-{0}".format(draft_id),
        title="Exact tool selection",
        must_have=True,
        source=span.to_source_reference(),
        human_added=False,
        normalized_claim=normalized_claim,
        metric=Metric.EXACT_TOOL_SELECTION_RATE,
        unit="proportion",
        aggregation="exact-match proportion",
        rule=ProportionRule(
            threshold=0.95,
            minimum_samples=200,
        ),
        workload_slice="support-tool-selection-v1",
        adapter=DeterministicToolSelectionAdapter.name,
        adapter_version=DeterministicToolSelectionAdapter.version,
        owner="vendor_solutions_engineer",
        evidence_policy=(
            "Persist synthetic case IDs, expected/actual tool names, "
            "calculation inputs, and SHA-256 digests."
        ),
        approved=False,
    )
    return (
        CriterionDraft(
            id=draft_id,
            source_span=span,
            normalized_claim=normalized_claim,
            proposed_criterion=criterion,
            open_questions=[],
        ),
        SourceReviewControl(
            draft_id=draft_id,
            allowed_actions=("APPROVE", "REJECT"),
            can_edit_rule=True,
        ),
    )


def _latency_context_draft(
    *,
    draft_id: str,
    transcript_id: str,
    line_number: int,
    quote: str,
) -> tuple[CriterionDraft, SourceReviewControl]:
    return (
        CriterionDraft(
            id=draft_id,
            source_span=TranscriptSpan(
                transcript_id=transcript_id,
                start_line=line_number,
                end_line=line_number,
                speaker=SOURCE_SPEAKER,
                quote=quote,
            ),
            normalized_claim=(
                "P95 end-to-end latency must remain below 2 seconds."
            ),
            proposed_criterion=None,
            open_questions=[
                "No deterministic latency adapter is available in this demo; "
                "keep this proposal as context or add a compatible adapter in "
                "a future contract."
            ],
        ),
        SourceReviewControl(
            draft_id=draft_id,
            allowed_actions=("REJECT",),
            can_edit_rule=False,
        ),
    )


def _unresolved_accuracy_draft(
    *,
    draft_id: str,
    transcript_id: str,
    line_number: int,
    quote: str,
) -> tuple[CriterionDraft, SourceReviewControl]:
    return (
        CriterionDraft(
            id=draft_id,
            source_span=TranscriptSpan(
                transcript_id=transcript_id,
                start_line=line_number,
                end_line=line_number,
                speaker=SOURCE_SPEAKER,
                quote=quote,
            ),
            normalized_claim=(
                "Tool-selection accuracy should be at least 95%; a human must "
                "define the fixed sample count before approval."
            ),
            proposed_criterion=None,
            open_questions=[
                "What minimum fixed sample count should define this acceptance rule?"
            ],
        ),
        SourceReviewControl(
            draft_id=draft_id,
            allowed_actions=("REJECT",),
            can_edit_rule=True,
        ),
    )


def _project_candidate(
    candidate: PreparedCandidateDraft,
    *,
    draft_id: str,
    transcript_id: str,
    line_number: int,
    quote: str,
) -> tuple[CriterionDraft, SourceReviewControl]:
    projection = candidate.projection
    signature = (
        projection.criterion_type,
        projection.metric,
        projection.operator,
        projection.threshold,
        projection.unit,
        projection.minimum_samples,
    )
    if signature == (
        "numeric_threshold",
        "tool_selection_accuracy",
        "gte",
        "0.95",
        "ratio",
        200,
    ):
        return _supported_accuracy_draft(
            draft_id=draft_id,
            transcript_id=transcript_id,
            line_number=line_number,
            quote=quote,
        )
    if signature == (
        "numeric_threshold",
        "end_to_end_latency_p95",
        "lt",
        "2",
        "seconds",
        None,
    ):
        return _latency_context_draft(
            draft_id=draft_id,
            transcript_id=transcript_id,
            line_number=line_number,
            quote=quote,
        )
    if signature == (
        "numeric_threshold",
        "tool_selection_accuracy",
        "gte",
        "0.95",
        "ratio",
        None,
    ):
        return _unresolved_accuracy_draft(
            draft_id=draft_id,
            transcript_id=transcript_id,
            line_number=line_number,
            quote=quote,
        )
    raise SourceWebRuntimeError()


def _project_prepared_source(
    fixture_case_id: str,
    prepared: PreparedSourceEnvelope,
    *,
    source_version: int,
) -> tuple[DiscoveryPack, SourceIntakeRecord]:
    expected_counts = {"thread-root": 2, "authority-attack": 1}
    if len(prepared.candidate_drafts) != expected_counts.get(
        fixture_case_id
    ):
        raise SourceWebRuntimeError()
    transcript_id = "email-{0}-v{1}".format(
        fixture_case_id,
        source_version,
    )
    label = _guided_label(fixture_case_id)
    lines: list[TranscriptLine] = []
    drafts: list[CriterionDraft] = []
    controls: list[SourceReviewControl] = []
    for ordinal, candidate in enumerate(
        prepared.candidate_drafts,
        start=1,
    ):
        quote = _quote_for_candidate(prepared, candidate)
        draft_id = "EMAIL-REQ-{0:02d}".format(ordinal)
        lines.append(
            TranscriptLine(
                line_number=ordinal,
                speaker=SOURCE_SPEAKER,
                text=quote,
            )
        )
        draft, control = _project_candidate(
            candidate,
            draft_id=draft_id,
            transcript_id=transcript_id,
            line_number=ordinal,
            quote=quote,
        )
        drafts.append(draft)
        controls.append(control)
    pack = DiscoveryPack(
        transcript=DiscoveryTranscript(
            id=transcript_id,
            title=label,
            synthetic=True,
            lines=lines,
        ),
        drafts=drafts,
    )
    return (
        pack,
        SourceIntakeRecord(
            fixture_case_id=fixture_case_id,
            label=label,
            source_version=source_version,
            message_count=1,
            controls=tuple(controls),
        ),
    )


class SourceWebRuntime:
    """Request-local parser plus process-local atomic source store."""

    __slots__ = ("_observed_clock", "_store", "_store_factory")

    def __init__(
        self,
        *,
        observed_clock: Callable[[], datetime] | None = None,
        store_factory: Callable[[], SourceStore] = SourceStore,
    ) -> None:
        self._observed_clock = (
            (lambda: datetime.now(timezone.utc))
            if observed_clock is None
            else observed_clock
        )
        self._store_factory = store_factory
        self._store = store_factory()

    def catalog_payload(self) -> dict[str, Any]:
        return _catalog_payload()

    def counts(self) -> SourceStoreCounts:
        return self._store.counts()

    def reset(self) -> None:
        self._store = self._store_factory()

    def _prepare(self, fixture_case_id: str) -> Any:
        try:
            observed_at = self._observed_clock()
            if (
                not isinstance(observed_at, datetime)
                or observed_at.tzinfo is None
                or observed_at.utcoffset() is None
            ):
                raise SourceWebRuntimeError()
            with support_agent_email_paths() as resources:
                return prepare_support_agent_email_fixture(
                    resources,
                    fixture_case_id,
                    observed_at=observed_at,
                )
        except SourceWebRuntimeError:
            raise
        except (
            Rfc822PreparationError,
            SupportAgentEmailResourceError,
            OSError,
            TypeError,
            ValueError,
        ):
            raise SourceWebRuntimeError() from None
        except Exception:
            raise SourceWebRuntimeError() from None

    def import_new(self, fixture_case_id: str) -> SourcePublication:
        """Prepare and pre-project before publishing one first source version."""

        prepared_import = self._prepare(fixture_case_id)
        try:
            pack, intake = _project_prepared_source(
                fixture_case_id,
                prepared_import.prepared_envelope,
                source_version=1,
            )
            result = self._store.import_prepared(prepared_import)
            if (
                result.receipt.outcome_code
                != SourceImportOutcome.ACCEPTED.value
                or result.receipt.source_version != 1
                or result.envelope is None
                or result.envelope.source_version != 1
                or len(result.envelope.candidates) != len(pack.drafts)
            ):
                raise SourceWebRuntimeError()
            return SourcePublication(
                receipt=result.receipt,
                discovery_pack=pack,
                intake=intake,
            )
        except SourceWebRuntimeError:
            raise
        except Exception:
            raise SourceWebRuntimeError() from None
        finally:
            prepared_import = None

    def replay(self, fixture_case_id: str) -> SourceImportReceipt:
        """Re-parse the approved source and prove private store idempotency."""

        prepared_import = self._prepare(fixture_case_id)
        try:
            result = self._store.import_prepared(prepared_import)
            if (
                result.receipt.outcome_code
                != SourceImportOutcome.DUPLICATE_REPLAY.value
                or result.receipt.source_version != 1
                or result.receipt.candidate_count != 0
                or result.envelope is None
            ):
                raise SourceWebRuntimeError()
            return result.receipt
        except SourceWebRuntimeError:
            raise
        except Exception:
            raise SourceWebRuntimeError() from None
        finally:
            prepared_import = None


def source_import_success_payload(
    *,
    receipt: SourceImportReceipt,
    intake: SourceIntakeRecord,
    drafts: Sequence[CriterionDraft],
) -> dict[str, Any]:
    """Return only the exact frozen public import response projection."""

    return {
        "contract_version": SOURCE_CONTRACT_VERSION,
        "receipt": receipt.to_dict(),
        "state": {
            "source_intake": intake.public_payload(drafts),
            "drafts": [
                draft.model_dump(mode="json") for draft in drafts
            ],
        },
    }


__all__ = [
    "GUIDED_SOURCE_CASE_IDS",
    "SOURCE_CATALOG_PATH",
    "SOURCE_CONTRACT_VERSION",
    "SOURCE_IMPORT_PATH",
    "SOURCE_OBSERVED_READ_LIMIT_BYTES",
    "SOURCE_REQUEST_LIMIT_BYTES",
    "SourceIntakeRecord",
    "SourceWebRefusal",
    "SourceWebRequest",
    "SourceWebResponse",
    "SourceWebRuntime",
    "SourceWebRuntimeError",
    "handle_source_web_request",
    "is_source_pipeline_target",
    "source_import_success_payload",
    "source_refusal_response",
]
