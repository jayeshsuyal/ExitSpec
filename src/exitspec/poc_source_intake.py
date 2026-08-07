"""Bounded, deterministic source intake for process-local draft POCs.

The runtime in this module converts four explicit local-demo inputs into
redacted :class:`~exitspec.poc_sources.PreparedPOCSource` objects. It never
retains raw input and never transfers approval, confirmation, freeze,
execution, evidence, or verdict authority. Every extracted requirement remains
source-anchored ``NEEDS_REVIEW`` input for a later human-review boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from threading import RLock
from typing import Any, Callable, Final, Literal, Tuple
import unicodedata

from pydantic import Field, ValidationError

from .adapters.rfc822 import (
    Rfc822PreparationError,
    prepare_support_agent_email_fixture,
)
from .contracts import verify_contract_digest
from .demo_data import (
    SupportAgentEmailResourceError,
    support_agent_email_paths,
)
from .intake import (
    TranscriptIntakeError,
    redact_and_parse_pasted_transcript,
)
from .models import ContractStatus, FrozenExitSpecModel, POCContract
from .poc_creation import (
    DraftPOCArchiveState,
    DraftPOCSnapshot,
    POC_ID_PATTERN,
)
from .poc_proposal_review import (
    ProposalReviewProposalUnavailable,
    SourceBoundProposal,
    derive_proposal_id,
)
from .poc_sources import (
    POCSourceAttachmentResult,
    POCSourceRevisionRequired,
    PreparedPOCSource,
    PreparedRequirementCandidate,
    ProcessLocalPOCSourceService,
    SourceKind,
)
from .redaction import (
    POLICY_VERSION,
    RedactionBoundaryError,
    assert_redaction_egress,
    redact_transcript,
)


EMAIL_INPUT_LIMIT: Final = 64
EMAIL_TEXT_INPUT_LIMIT: Final = 20_000
MEETING_INPUT_LIMIT: Final = 20_000
DOCUMENT_INPUT_LIMIT: Final = 20_000
CONTRACT_INPUT_LIMIT: Final = 40_000
MAX_PROPOSALS: Final = 64
MAX_CANDIDATE_QUOTE: Final = 4_000
MAX_NORMALIZED_CLAIM: Final = 2_000
MAX_IDEMPOTENCY_RECORDS: Final = 16_384

_ALLOWED_EMAIL_FIXTURES: Final = frozenset(
    {"thread-root", "authority-attack"}
)
_RECEIPT_ID_PATTERN: Final = r"^srcpt_[a-z0-9][a-z0-9_-]{7,95}$"
_STT_OPERATION_ID = re.compile(r"^sttop_[a-f0-9]{64}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_REDACTION_VERSION: Final = "exitspec-transcript-redaction-1.0"
_ADAPTER_VERSION: Final = "1.0.0"
_REQUIREMENT_SIGNAL = re.compile(
    r"(?i)(?:"
    r"\bmust\b|\bshall\b|\bshould\b|\bneeds?\b|\brequires?\b|"
    r"\brequirement\b|\btarget\b|\bat\s+least\b|\bat\s+most\b|"
    r"\bno\s+more\s+than\b|\bless\s+than\b|\bgreater\s+than\b|"
    r"\bbelow\b|\babove\b|\bwithin\b|\bp(?:90|95|99)\b|"
    r"\blatency\b|\bthroughput\b|\baccuracy\b|\berror\s+rate\b|"
    r"\bbudget\b|\bcost\b"
    r")"
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_SPEAKER_MESSAGE_LINE = re.compile(r"^[^:\n]{1,80}:\s+\S")
_MALFORMED_SPEAKER_LINE = re.compile(
    r"(?:^\s*:|^[^:\n]{1,80}:\s*$)"
)


class POCSourceIntakeError(RuntimeError):
    """Base class for content-free source-intake failures."""


class POCSourceIntakeInvalid(POCSourceIntakeError):
    """The supplied source did not pass its bounded intake contract."""


class POCSourceFixtureUnavailable(POCSourceIntakeError):
    """The requested synthetic email fixture is not approved."""


class POCSourceIntakeRevisionRequired(POCSourceIntakeError):
    """A stable source identity changed and needs an explicit later revision."""


class POCSourceIntakeCapacityExceeded(POCSourceIntakeError):
    """The process-local intake bookkeeping reached its fixed capacity."""


class POCSourceReceipt(FrozenExitSpecModel):
    """The complete safe UI projection of one attached source."""

    poc_id: str = Field(pattern=POC_ID_PATTERN)
    source_kind: SourceKind
    source_receipt_id: str = Field(pattern=_RECEIPT_ID_PATTERN)
    proposal_count: int = Field(ge=0, le=MAX_PROPOSALS)
    status: Literal["NEEDS_REVIEW"] = "NEEDS_REVIEW"
    idempotent_replay: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: str) -> str:
    return unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    ).strip()


def _require_bounded_text(
    value: object,
    *,
    maximum: int,
) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise POCSourceIntakeInvalid(
            "The source input is outside its supported bounds."
        )
    normalized = _normalize_text(value)
    if not normalized:
        raise POCSourceIntakeInvalid(
            "The source input is outside its supported bounds."
        )
    return normalized


def _redact_for_storage(value: str) -> tuple[str, str]:
    invalid = False
    redacted_text = ""
    try:
        result = redact_transcript(value)
        redacted_text = assert_redaction_egress(result)
    except (TypeError, ValueError, RedactionBoundaryError):
        invalid = True
    if invalid:
        raise POCSourceIntakeInvalid(
            "The source input was blocked by the current redaction policy."
        )
    normalized = _normalize_text(redacted_text)
    if not normalized:
        raise POCSourceIntakeInvalid(
            "The source input is outside its supported bounds."
        )
    return normalized, POLICY_VERSION


def _content_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _external_identity(prefix: str, *values: str) -> str:
    identity_material = "\x00".join(values).encode("utf-8")
    suffix = hashlib.sha256(identity_material).hexdigest()[:32]
    return "{0}.{1}".format(prefix, suffix)


def _candidate(
    *,
    adapter_label: str,
    ordinal: int,
    quote: str,
    normalized_claim: str,
) -> PreparedRequirementCandidate:
    return PreparedRequirementCandidate(
        candidate_id="cand_{0}_{1:03d}".format(adapter_label, ordinal),
        source_quote=quote,
        normalized_claim=normalized_claim,
    )


def _likely_requirement_fragments(
    redacted_text: str,
) -> Tuple[str, ...]:
    fragments: list[str] = []
    seen: set[str] = set()
    for paragraph in redacted_text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for sentence in _SENTENCE_BOUNDARY.split(paragraph):
            candidate = sentence.strip()
            if (
                not candidate
                or len(candidate) > MAX_CANDIDATE_QUOTE
                or len(" ".join(candidate.split())) > MAX_NORMALIZED_CLAIM
                or _REQUIREMENT_SIGNAL.search(candidate) is None
                or candidate in seen
            ):
                continue
            fragments.append(candidate)
            seen.add(candidate)
            if len(fragments) == MAX_PROPOSALS:
                return tuple(fragments)
    return tuple(fragments)


def _is_single_speaker_natural_text(value: str) -> bool:
    """Allow unlabelled prose without reinterpreting malformed dialogue."""

    lines = tuple(line.strip() for line in value.splitlines() if line.strip())
    return bool(lines) and not any(
        _MALFORMED_SPEAKER_LINE.match(line)
        or _SPEAKER_MESSAGE_LINE.match(line)
        for line in lines
    )


def _receipt_id(source_id: str) -> str:
    if not source_id.startswith("src_"):
        raise POCSourceIntakeError(
            "The attached source could not be projected safely."
        )
    return "srcpt_{0}".format(source_id.removeprefix("src_"))


def _receipt(
    result: POCSourceAttachmentResult,
) -> POCSourceReceipt:
    return POCSourceReceipt(
        poc_id=result.source.poc_id,
        source_kind=result.source.kind,
        source_receipt_id=_receipt_id(result.source.source_id),
        proposal_count=len(result.source.candidates),
        status="NEEDS_REVIEW",
        idempotent_replay=result.replayed,
    )


def _strict_contract(contract_json: str) -> POCContract:
    duplicate_or_nonfinite = False

    def reject_duplicate_pairs(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def reject_nonfinite(_: str) -> Any:
        raise ValueError

    parsed: Any = None
    try:
        parsed = json.loads(
            contract_json,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_nonfinite,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        duplicate_or_nonfinite = True
    if duplicate_or_nonfinite or type(parsed) is not dict:
        raise POCSourceIntakeInvalid(
            "The existing contract JSON was not accepted."
        )

    invalid_contract = False
    contract: POCContract | None = None
    try:
        normalized_json = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        contract = POCContract.model_validate_json(
            normalized_json,
            strict=True,
        )
        if (
            contract.status == ContractStatus.FROZEN
            and contract.canonical_hash is None
        ):
            invalid_contract = True
        elif (
            contract.canonical_hash is not None
            and not verify_contract_digest(contract)
        ):
            invalid_contract = True
    except (TypeError, ValueError, ValidationError):
        invalid_contract = True
    if invalid_contract or contract is None:
        raise POCSourceIntakeInvalid(
            "The existing contract JSON was not accepted."
        )
    if len(contract.criteria) > MAX_PROPOSALS:
        raise POCSourceIntakeInvalid(
            "The existing contract exceeds the supported proposal count."
        )
    return contract


class ProcessLocalPOCSourceIntake:
    """Compose deterministic adapters with the process-local source store."""

    __slots__ = (
        "_clock",
        "_draft_lookup",
        "_idempotency_lock",
        "_observed_at_by_key",
        "_source_service",
    )

    def __init__(
        self,
        *,
        draft_lookup: Callable[[str], DraftPOCSnapshot],
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not callable(draft_lookup):
            raise TypeError("draft_lookup must be callable.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._draft_lookup = draft_lookup
        self._clock = clock
        self._source_service = ProcessLocalPOCSourceService(
            draft_lookup=draft_lookup,
        )
        self._observed_at_by_key: dict[str, datetime] = {}
        self._idempotency_lock = RLock()

    def _observed_at(self, idempotency_key: object) -> datetime:
        if (
            type(idempotency_key) is not str
            or not idempotency_key.strip()
            or len(idempotency_key) > 200
        ):
            raise POCSourceIntakeInvalid(
                "The source request key is outside its supported bounds."
            )
        key_digest = hashlib.sha256(
            b"exitspec-poc-source-intake-observed-v1\x00"
            + idempotency_key.encode("utf-8")
        ).hexdigest()
        with self._idempotency_lock:
            observed_at = self._observed_at_by_key.get(key_digest)
            if observed_at is not None:
                return observed_at
            if len(self._observed_at_by_key) >= MAX_IDEMPOTENCY_RECORDS:
                raise POCSourceIntakeCapacityExceeded(
                    "The process-local source intake is at capacity."
                )
            observed_at = self._clock()
            if (
                not isinstance(observed_at, datetime)
                or observed_at.tzinfo is None
                or observed_at.utcoffset() is None
            ):
                raise POCSourceIntakeError(
                    "The source intake clock is unavailable."
                )
            self._observed_at_by_key[key_digest] = observed_at
            return observed_at

    def _attach(
        self,
        *,
        poc_id: str,
        prepared_source: PreparedPOCSource,
        idempotency_key: str,
    ) -> POCSourceReceipt:
        revision_required = False
        result: POCSourceAttachmentResult | None = None
        try:
            result = self._source_service.attach(
                poc_id,
                prepared_source,
                idempotency_key,
            )
        except POCSourceRevisionRequired:
            revision_required = True
        if revision_required:
            raise POCSourceIntakeRevisionRequired(
                "The source identity changed; explicit revision is required."
            )
        if result is None:
            raise POCSourceIntakeError(
                "The source could not be attached safely."
            )
        return _receipt(result)

    def list_receipts(
        self,
        poc_id: str,
    ) -> Tuple[POCSourceReceipt, ...]:
        """Return source metadata only; redacted content remains store-local."""

        unavailable = False
        try:
            draft = self._draft_lookup(poc_id)
            unavailable = (
                not isinstance(draft, DraftPOCSnapshot)
                or draft.poc_id != poc_id
                or draft.archive_state != DraftPOCArchiveState.ACTIVE
            )
        except Exception:
            unavailable = True
        if unavailable:
            raise POCSourceIntakeInvalid(
                "The draft POC is unavailable in this process."
            )
        return tuple(
            POCSourceReceipt(
                poc_id=source.poc_id,
                source_kind=source.kind,
                source_receipt_id=_receipt_id(source.source_id),
                proposal_count=len(source.candidates),
                status="NEEDS_REVIEW",
                idempotent_replay=False,
            )
            for source in self._source_service.snapshots(poc_id)
        )

    def proposal_inputs(
        self,
        poc_id: str,
    ) -> Tuple[SourceBoundProposal, ...]:
        """Project only redacted source-bound candidates for human triage."""

        try:
            self.list_receipts(poc_id)
        except POCSourceIntakeInvalid as error:
            raise ProposalReviewProposalUnavailable(
                "The draft POC is unavailable in this process."
            ) from error
        proposals = []
        for source in self._source_service.snapshots(poc_id):
            source_receipt_id = _receipt_id(source.source_id)
            for candidate in source.candidates:
                proposals.append(
                    SourceBoundProposal(
                        poc_id=source.poc_id,
                        proposal_id=derive_proposal_id(
                            source.poc_id,
                            source_receipt_id,
                            candidate.candidate_id,
                        ),
                        source_receipt_id=source_receipt_id,
                        source_kind=source.kind,
                        source_quote=candidate.source_quote,
                        normalized_claim=candidate.normalized_claim,
                        state="NEEDS_REVIEW",
                    )
                )
        return tuple(proposals)

    def capture_email(
        self,
        *,
        poc_id: str,
        fixture_case_id: str,
        idempotency_key: str,
    ) -> POCSourceReceipt:
        """Attach one of the two manifest-pinned synthetic email fixtures."""

        if (
            type(fixture_case_id) is not str
            or len(fixture_case_id) > EMAIL_INPUT_LIMIT
            or fixture_case_id not in _ALLOWED_EMAIL_FIXTURES
        ):
            raise POCSourceFixtureUnavailable(
                "The synthetic email fixture is not approved."
            )
        observed_at = self._observed_at(idempotency_key)
        prepared_import: Any = None
        fixture_failed = False
        try:
            with support_agent_email_paths() as resources:
                prepared_import = prepare_support_agent_email_fixture(
                    resources,
                    fixture_case_id,
                    observed_at=observed_at,
                )
        except (Rfc822PreparationError, SupportAgentEmailResourceError):
            fixture_failed = True
        if fixture_failed or prepared_import is None:
            raise POCSourceFixtureUnavailable(
                "The synthetic email fixture is unavailable."
            )

        envelope = prepared_import.prepared_envelope
        headers = envelope.message.redacted_headers.model_dump(
            mode="python",
            by_alias=True,
        )
        redacted_parts = tuple(
            part.redacted_text for part in envelope.message.parts
        )
        flattened = "\n".join(
            (
                "From: {0}".format(headers["from"]),
                "To: {0}".format(headers["to"]),
                "Subject: {0}".format(headers["subject"]),
                "",
                *redacted_parts,
            )
        )
        redacted_text, _ = _redact_for_storage(flattened)
        candidates: list[PreparedRequirementCandidate] = []
        invalid_fixture_projection = False
        try:
            for ordinal, draft in enumerate(
                envelope.candidate_drafts,
                start=1,
            ):
                part = next(
                    item
                    for item in envelope.message.parts
                    if item.part_path == draft.part_path
                )
                quote = part.redacted_text.encode("utf-8")[
                    draft.start_byte : draft.end_byte
                ].decode("utf-8")
                safe_quote, _ = _redact_for_storage(quote)
                if safe_quote not in redacted_text:
                    invalid_fixture_projection = True
                    break
                candidates.append(
                    _candidate(
                        adapter_label="email",
                        ordinal=ordinal,
                        quote=safe_quote,
                        normalized_claim=" ".join(safe_quote.split()),
                    )
                )
        except (StopIteration, UnicodeDecodeError, ValueError):
            invalid_fixture_projection = True
        if invalid_fixture_projection:
            raise POCSourceFixtureUnavailable(
                "The synthetic email fixture is unavailable."
            )

        prepared = PreparedPOCSource(
            kind=SourceKind.EMAIL,
            external_id="email.fixture.{0}".format(fixture_case_id),
            redacted_text=redacted_text,
            content_sha256=_content_sha256(redacted_text),
            candidates=tuple(candidates),
            adapter_name="synthetic_rfc822",
            adapter_version=_ADAPTER_VERSION,
            redaction_policy_version=_REDACTION_VERSION,
            observed_at=observed_at,
        )
        return self._attach(
            poc_id=poc_id,
            prepared_source=prepared,
            idempotency_key=idempotency_key,
        )

    def capture_email_text(
        self,
        *,
        poc_id: str,
        email_text: str,
        idempotency_key: str,
    ) -> POCSourceReceipt:
        """Redact and attach one bounded pasted customer email."""

        bounded = _require_bounded_text(
            email_text,
            maximum=EMAIL_TEXT_INPUT_LIMIT,
        )
        redacted_text, _ = _redact_for_storage(bounded)
        fragments = _likely_requirement_fragments(redacted_text)
        candidates = tuple(
            _candidate(
                adapter_label="email",
                ordinal=ordinal,
                quote=quote,
                normalized_claim=" ".join(quote.split()),
            )
            for ordinal, quote in enumerate(fragments, start=1)
        )
        observed_at = self._observed_at(idempotency_key)
        prepared = PreparedPOCSource(
            kind=SourceKind.EMAIL,
            external_id=_external_identity("email.paste", redacted_text),
            redacted_text=redacted_text,
            content_sha256=_content_sha256(redacted_text),
            candidates=candidates,
            adapter_name="pasted_email",
            adapter_version=_ADAPTER_VERSION,
            redaction_policy_version=_REDACTION_VERSION,
            observed_at=observed_at,
        )
        return self._attach(
            poc_id=poc_id,
            prepared_source=prepared,
            idempotency_key=idempotency_key,
        )

    def capture_meeting(
        self,
        *,
        poc_id: str,
        transcript_text: str,
        idempotency_key: str,
    ) -> POCSourceReceipt:
        """Attach labelled dialogue or one unlabelled single-speaker paste."""

        return self._capture_meeting_text(
            poc_id=poc_id,
            transcript_text=transcript_text,
            idempotency_key=idempotency_key,
            adapter_name="pasted_meeting",
        )

    def capture_stt_transcript(
        self,
        *,
        poc_id: str,
        redacted_transcript_text: str,
        expected_content_sha256: str,
        operation_id: str,
        idempotency_key: str,
    ) -> POCSourceReceipt:
        """Recheck and attach one operation-bound redacted STT transcript."""

        if (
            type(expected_content_sha256) is not str
            or _SHA256.fullmatch(expected_content_sha256) is None
            or type(operation_id) is not str
            or _STT_OPERATION_ID.fullmatch(operation_id) is None
        ):
            raise POCSourceIntakeInvalid(
                "The STT source binding is outside its supported contract."
            )
        return self._capture_meeting_text(
            poc_id=poc_id,
            transcript_text=redacted_transcript_text,
            idempotency_key=idempotency_key,
            adapter_name="synthetic_stt",
            external_id=_external_identity("meeting.stt", operation_id),
            expected_content_sha256=expected_content_sha256,
        )

    def capture_meeting_connector_transcript(
        self,
        *,
        poc_id: str,
        redacted_transcript_text: str,
        expected_content_sha256: str,
        stream_identity_sha256: str,
        idempotency_key: str,
    ) -> POCSourceReceipt:
        """Recheck and attach one sealed connector transcript projection."""

        if (
            type(expected_content_sha256) is not str
            or _SHA256.fullmatch(expected_content_sha256) is None
            or type(stream_identity_sha256) is not str
            or _SHA256.fullmatch(stream_identity_sha256) is None
        ):
            raise POCSourceIntakeInvalid(
                "The meeting connector source binding is outside its "
                "supported contract."
            )
        return self._capture_meeting_text(
            poc_id=poc_id,
            transcript_text=redacted_transcript_text,
            idempotency_key=idempotency_key,
            adapter_name="synthetic_meeting_connector",
            external_id=_external_identity(
                "meeting.connector",
                stream_identity_sha256,
            ),
            expected_content_sha256=expected_content_sha256,
        )

    def _capture_meeting_text(
        self,
        *,
        poc_id: str,
        transcript_text: str,
        idempotency_key: str,
        adapter_name: str,
        external_id: str | None = None,
        expected_content_sha256: str | None = None,
    ) -> POCSourceReceipt:
        """Normalize one meeting source and attach only its redacted form."""

        bounded = _require_bounded_text(
            transcript_text,
            maximum=MEETING_INPUT_LIMIT,
        )
        intake: Any = None
        try:
            intake = redact_and_parse_pasted_transcript(
                bounded,
                transcript_id="source-meeting",
                title="Pasted meeting transcript",
            )
        except TranscriptIntakeError:
            intake = None

        if intake is not None:
            redacted_text = "\n".join(
                "{0}: {1}".format(line.speaker, line.text)
                for line in intake.transcript.lines
            )
            fragments = tuple(
                fragment
                for line in intake.transcript.lines
                for fragment in _likely_requirement_fragments(line.text)
            )[:MAX_PROPOSALS]
        elif _is_single_speaker_natural_text(bounded):
            # Keep the redacted source wording intact. In particular, do not
            # invent a speaker label merely to satisfy the dialogue parser.
            redacted_text, _ = _redact_for_storage(bounded)
            fragments = _likely_requirement_fragments(redacted_text)
        else:
            raise POCSourceIntakeInvalid(
                "The meeting transcript was not accepted."
            )

        candidates = tuple(
            _candidate(
                adapter_label="meeting",
                ordinal=ordinal,
                quote=quote,
                normalized_claim=" ".join(quote.split()),
            )
            for ordinal, quote in enumerate(fragments, start=1)
        )
        content_sha256 = _content_sha256(redacted_text)
        if (
            expected_content_sha256 is not None
            and content_sha256 != expected_content_sha256
        ):
            raise POCSourceIntakeInvalid(
                "The meeting source does not match its redacted content "
                "binding."
            )
        observed_at = self._observed_at(idempotency_key)
        prepared = PreparedPOCSource(
            kind=SourceKind.MEETING,
            external_id=(
                _external_identity("meeting.paste", redacted_text)
                if external_id is None
                else external_id
            ),
            redacted_text=redacted_text,
            content_sha256=content_sha256,
            candidates=candidates,
            adapter_name=adapter_name,
            adapter_version=_ADAPTER_VERSION,
            redaction_policy_version=_REDACTION_VERSION,
            observed_at=observed_at,
        )
        return self._attach(
            poc_id=poc_id,
            prepared_source=prepared,
            idempotency_key=idempotency_key,
        )

    def capture_document(
        self,
        *,
        poc_id: str,
        document_text: str,
        idempotency_key: str,
    ) -> POCSourceReceipt:
        """Redact and attach one bounded pasted document."""

        bounded = _require_bounded_text(
            document_text,
            maximum=DOCUMENT_INPUT_LIMIT,
        )
        redacted_text, _ = _redact_for_storage(bounded)
        fragments = _likely_requirement_fragments(redacted_text)
        candidates = tuple(
            _candidate(
                adapter_label="document",
                ordinal=ordinal,
                quote=quote,
                normalized_claim=" ".join(quote.split()),
            )
            for ordinal, quote in enumerate(fragments, start=1)
        )
        observed_at = self._observed_at(idempotency_key)
        prepared = PreparedPOCSource(
            kind=SourceKind.DOCUMENT,
            external_id=_external_identity("document.paste", redacted_text),
            redacted_text=redacted_text,
            content_sha256=_content_sha256(redacted_text),
            candidates=candidates,
            adapter_name="pasted_document",
            adapter_version=_ADAPTER_VERSION,
            redaction_policy_version=_REDACTION_VERSION,
            observed_at=observed_at,
        )
        return self._attach(
            poc_id=poc_id,
            prepared_source=prepared,
            idempotency_key=idempotency_key,
        )

    def capture_contract(
        self,
        *,
        poc_id: str,
        contract_json: str,
        idempotency_key: str,
    ) -> POCSourceReceipt:
        """Import strict ExitSpec contract criteria without lifecycle authority."""

        bounded = _require_bounded_text(
            contract_json,
            maximum=CONTRACT_INPUT_LIMIT,
        )
        contract = _strict_contract(bounded)

        raw_lines = tuple(
            "Criterion {0}: {1}".format(
                criterion.id,
                criterion.normalized_claim,
            )
            for criterion in contract.criteria
        )
        redacted_text, _ = _redact_for_storage("\n".join(raw_lines))
        candidates: list[PreparedRequirementCandidate] = []
        invalid_projection = False
        for ordinal, (criterion, raw_line) in enumerate(
            zip(contract.criteria, raw_lines, strict=True),
            start=1,
        ):
            safe_line, _ = _redact_for_storage(raw_line)
            safe_claim, _ = _redact_for_storage(
                criterion.normalized_claim
            )
            if (
                safe_line not in redacted_text
                or len(safe_line) > MAX_CANDIDATE_QUOTE
                or len(safe_claim) > MAX_NORMALIZED_CLAIM
            ):
                invalid_projection = True
                break
            candidates.append(
                _candidate(
                    adapter_label="contract",
                    ordinal=ordinal,
                    quote=safe_line,
                    normalized_claim=safe_claim,
                )
            )
        if invalid_projection:
            raise POCSourceIntakeInvalid(
                "The existing contract criteria could not be projected safely."
            )

        observed_at = self._observed_at(idempotency_key)
        prepared = PreparedPOCSource(
            kind=SourceKind.EXISTING_CONTRACT,
            external_id=_external_identity(
                "contract.version",
                contract.id,
                contract.version,
            ),
            redacted_text=redacted_text,
            content_sha256=_content_sha256(redacted_text),
            candidates=tuple(candidates),
            adapter_name="strict_contract_json",
            adapter_version=_ADAPTER_VERSION,
            redaction_policy_version=_REDACTION_VERSION,
            observed_at=observed_at,
        )
        return self._attach(
            poc_id=poc_id,
            prepared_source=prepared,
            idempotency_key=idempotency_key,
        )


__all__ = [
    "CONTRACT_INPUT_LIMIT",
    "DOCUMENT_INPUT_LIMIT",
    "EMAIL_INPUT_LIMIT",
    "EMAIL_TEXT_INPUT_LIMIT",
    "MEETING_INPUT_LIMIT",
    "POCSourceFixtureUnavailable",
    "POCSourceIntakeCapacityExceeded",
    "POCSourceIntakeError",
    "POCSourceIntakeInvalid",
    "POCSourceIntakeRevisionRequired",
    "POCSourceReceipt",
    "ProcessLocalPOCSourceIntake",
]
