"""Deterministic, provider-free assisted-authoring adapter for the local demo.

This adapter deliberately consumes only the redacted JSON request assembled by
``assisted_authoring``. It is not a model, never makes network calls, and cannot
approve a draft, freeze a contract, or assign an evidence verdict.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .assisted_authoring import (
    ASSISTED_AUTHORING_MODEL,
    ASSISTED_AUTHORING_SCHEMA_VERSION,
    ExactToolSelectionPolicy,
    ProposalBatch,
    ProposalClassification,
    SourceNeutralProposalBatch,
)
from .providers import (
    ProviderError,
    ProviderErrorCode,
    ProviderReceipt,
    StructuredJSONRequest,
    StructuredJSONResult,
)
from .poc_sources import SourceKind


SYNTHETIC_ASSISTED_MODEL = "synthetic-assisted-authoring-v1"
SYNTHETIC_ASSISTED_ENDPOINT = "local://exitspec/synthetic-assisted-authoring"
SYNTHETIC_ASSISTED_ADAPTER = "synthetic_assisted_authoring"
SYNTHETIC_ASSISTED_ADAPTER_VERSION = "1"

SYNTHETIC_ASSISTED_POLICY = ExactToolSelectionPolicy(
    workload_slice="approved-support-cases-v2",
    adapter=DeterministicToolSelectionAdapter.name,
    adapter_version=DeterministicToolSelectionAdapter.version,
    owner="vendor_solutions_engineer",
    evidence_policy=(
        "Persist synthetic case IDs, expected/actual tool names, calculation "
        "inputs, and SHA-256 digests."
    ),
)

_PERCENT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%")
_SAMPLE_COUNT = re.compile(
    r"(?<!\w)(\d[\d,]*)\s+"
    r"(?:fixed\s+|approved\s+|valid\s+|total\s+|evaluation\s+|test\s+)*"
    r"(?:cases|samples|requests|examples)\b",
    re.IGNORECASE,
)
_EXACT_TOOL_SELECTION = re.compile(
    r"\b(?:exact\s+tool[- ]selection|select(?:s|ed|ing)?\s+the\s+exact\s+tool)\b",
    re.IGNORECASE,
)


def _provider_failure() -> ProviderError:
    """Return a stable error without retaining request or transcript content."""

    return ProviderError(
        ProviderErrorCode.MALFORMED_RESPONSE,
        "The synthetic assisted-authoring adapter could not read its redacted input.",
    )


def _redacted_lines(request: StructuredJSONRequest[ProposalBatch]) -> List[Mapping[str, Any]]:
    try:
        content = request.messages[-1].content
        _, separator, encoded = content.partition("\n")
        if not separator:
            raise ValueError
        payload = json.loads(encoded)
        lines = payload["lines"]
        if not isinstance(lines, list) or not lines:
            raise ValueError
        if any(
            not isinstance(line, Mapping)
            or not isinstance(line.get("line_number"), int)
            or not isinstance(line.get("speaker"), str)
            or not isinstance(line.get("quote"), str)
            for line in lines
        ):
            raise ValueError
        return lines
    except Exception:
        raise _provider_failure() from None


def _proposal_payload(request: StructuredJSONRequest[ProposalBatch]) -> Dict[str, Any]:
    parsed_lines = []
    for line in _redacted_lines(request):
        quote = str(line["quote"])
        percent_match = _PERCENT.search(quote)
        sample_match = _SAMPLE_COUNT.search(quote)
        parsed_lines.append(
            (
                line,
                quote,
                percent_match,
                sample_match,
                _EXACT_TOOL_SELECTION.search(quote) is not None,
            )
        )

    numeric_pairs = {
        (
            round(float(percent_match.group(1)) / 100, 6),
            int(sample_match.group(1).replace(",", "")),
        )
        for _, _, percent_match, sample_match, _ in parsed_lines
        if percent_match is not None and sample_match is not None
    }
    conflicting_numeric_rules = len(numeric_pairs) > 1

    proposals: List[Dict[str, Any]] = []
    for (
        line,
        quote,
        percent_match,
        sample_match,
        supported_metric,
    ) in parsed_lines:
        extracted_threshold = (
            round(float(percent_match.group(1)) / 100, 6)
            if percent_match is not None
            else None
        )
        minimum_samples = (
            int(sample_match.group(1).replace(",", ""))
            if sample_match is not None
            else None
        )
        complete_numeric_rule = (
            extracted_threshold is not None and minimum_samples is not None
        )
        measurable = (
            complete_numeric_rule
            and supported_metric
            and not conflicting_numeric_rules
        )
        threshold = (
            extracted_threshold
            if supported_metric or conflicting_numeric_rules
            else None
        )
        if measurable:
            title = "Exact support-tool selection"
            open_questions: List[str] = []
        elif complete_numeric_rule and conflicting_numeric_rules:
            title = "Conflicting measurable request"
            open_questions = [
                "Which threshold and minimum sample count governs the agreement?"
            ]
        elif complete_numeric_rule and not supported_metric:
            title = "Unsupported measurable request"
            open_questions = [
                "This demo supports exact tool selection only. "
                "Which supported metric should this request use?"
            ]
        else:
            title = "Unresolved customer request"
            open_questions = [
                "What measurable threshold and minimum sample count define acceptance?"
            ]

        proposals.append(
            {
                "line_number": line["line_number"],
                "speaker": line["speaker"],
                "quote": quote,
                "title": title,
                "normalized_claim": quote,
                "classification": (
                    ProposalClassification.MEASURABLE.value
                    if measurable
                    else ProposalClassification.VAGUE.value
                ),
                "threshold": threshold,
                "minimum_samples": minimum_samples,
                "open_questions": open_questions,
            }
        )
    return {"proposals": proposals}


class SyntheticAssistedAuthoringExecutor:
    """Local deterministic implementation of the provider-neutral executor."""

    provider_name = "synthetic"
    model = SYNTHETIC_ASSISTED_MODEL
    endpoint = SYNTHETIC_ASSISTED_ENDPOINT
    adapter_name = SYNTHETIC_ASSISTED_ADAPTER
    adapter_version = SYNTHETIC_ASSISTED_ADAPTER_VERSION

    def execute(
        self, request: StructuredJSONRequest[ProposalBatch]
    ) -> StructuredJSONResult[ProposalBatch]:
        """Generate source-bound proposal facts from already-redacted request JSON."""

        if request.model != self.model:
            raise _provider_failure()
        payload = _proposal_payload(request)
        try:
            request.validate_response_instance(payload)
            output = request.validate_output(payload)
        except Exception:
            raise _provider_failure() from None
        receipt = ProviderReceipt(
            provider=self.provider_name,
            model=request.model,
            endpoint=self.endpoint,
            attempts=1,
            latency_ms=0.0,
            input_tokens=request.estimated_input_tokens,
            output_tokens=None,
            total_tokens=None,
            provider_request_id=None,
            estimated_cost_usd=None,
            pricing_version=None,
        )
        return StructuredJSONResult(output=output, receipt=receipt)


def safe_receipt_facts(receipt: ProviderReceipt) -> Dict[str, Any]:
    """Project a receipt to JSON-safe, non-content execution facts."""

    return {
        "provider": receipt.provider,
        "model": receipt.model,
        "endpoint": receipt.endpoint,
        "attempts": receipt.attempts,
        "latency_ms": receipt.latency_ms,
        "input_tokens": receipt.input_tokens,
        "output_tokens": receipt.output_tokens,
        "total_tokens": receipt.total_tokens,
        "estimated_cost_usd": (
            None
            if receipt.estimated_cost_usd is None
            else str(receipt.estimated_cost_usd)
        ),
        "pricing_version": receipt.pricing_version,
    }


_SOURCE_NEUTRAL_SIGNAL = re.compile(
    r"(?i)(?:\bmust\b|\bshall\b|\bshould\b|\bneeds?\b|\brequires?\b|"
    r"\btarget\b|\bat\s+least\b|\bat\s+most\b|\bbelow\b|\babove\b|"
    r"\bwithin\b|\blatency\b|\bthroughput\b|\baccuracy\b|"
    r"\berror\s+rate\b|\bbudget\b|\bcost\b|\bthreshold\b)"
)
_SOURCE_NEUTRAL_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_SOURCE_NEUTRAL_PERCENT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%")
_SOURCE_NEUTRAL_SAMPLES = re.compile(
    r"(?<!\w)(\d[\d,]*)\s+"
    r"(?:fixed\s+|approved\s+|valid\s+|total\s+|evaluation\s+|test\s+)*"
    r"(?:cases|samples|requests|examples)\b",
    re.IGNORECASE,
)


def _source_neutral_input(
    request: StructuredJSONRequest[SourceNeutralProposalBatch],
) -> Mapping[str, Any]:
    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        content = request.messages[-1].content
        prefix, separator, encoded = content.partition("\n")
        if prefix != "Untrusted redacted source JSON follows:" or not separator:
            raise ValueError
        payload = json.loads(
            encoded,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {
                "source_kind",
                "source_content_sha256",
                "source_revision",
                "text",
            }
            or not isinstance(payload["source_kind"], str)
            or payload["source_kind"] not in {kind.value for kind in SourceKind}
            or not isinstance(payload["source_content_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", payload["source_content_sha256"])
            or type(payload["source_revision"]) is not int
            or payload["source_revision"] < 1
            or not isinstance(payload["text"], str)
            or not payload["text"].strip()
            or len(payload["text"]) > 64_000
        ):
            raise ValueError
        return payload
    except Exception:
        raise ProviderError(
            ProviderErrorCode.MALFORMED_RESPONSE,
            "The synthetic assisted-authoring adapter could not read its redacted input.",
        ) from None


class SyntheticSourceNeutralAssistedAuthoringExecutor:
    """Deterministic local seam for the A3 source-neutral authoring action."""

    provider_name = "synthetic"
    model = ASSISTED_AUTHORING_MODEL
    endpoint = "local://exitspec/source-neutral-assisted-authoring"
    adapter_name = "synthetic_source_neutral_assisted_authoring"
    adapter_version = "1"

    def execute(
        self,
        request: StructuredJSONRequest[SourceNeutralProposalBatch],
    ) -> StructuredJSONResult[SourceNeutralProposalBatch]:
        if request.model != self.model:
            raise ProviderError(
                ProviderErrorCode.PRECONDITION_FAILED,
                "The synthetic assisted-authoring model is not available.",
            )
        source = _source_neutral_input(request)
        fragments: list[str] = []
        for line in source["text"].splitlines():
            for fragment in _SOURCE_NEUTRAL_SENTENCE.split(line.strip()):
                candidate = fragment.strip()
                if (
                    candidate
                    and len(candidate) <= 4_000
                    and _SOURCE_NEUTRAL_SIGNAL.search(candidate) is not None
                    and candidate not in fragments
                ):
                    fragments.append(candidate)
                    if len(fragments) == 64:
                        break
            if len(fragments) == 64:
                break
        if not fragments:
            raise ProviderError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The synthetic assisted-authoring adapter produced no proposal material.",
            )

        proposals: list[dict[str, Any]] = []
        for ordinal, quote in enumerate(fragments, start=1):
            percentages = tuple(_SOURCE_NEUTRAL_PERCENT.finditer(quote))
            sample_counts = tuple(_SOURCE_NEUTRAL_SAMPLES.finditer(quote))
            numeric_facts = None
            if len(percentages) == 1 or len(sample_counts) == 1:
                numeric_facts = {
                    "threshold": (
                        round(float(percentages[0].group(1)) / 100.0, 12)
                        if len(percentages) == 1
                        else None
                    ),
                    "minimum_samples": (
                        int(sample_counts[0].group(1).replace(",", ""))
                        if len(sample_counts) == 1
                        else None
                    ),
                }
            proposals.append(
                {
                    "proposal_key": "proposal-{0:03d}".format(ordinal),
                    "source_quote": quote,
                    "normalized_claim": " ".join(quote.split()),
                    "numeric_facts": numeric_facts,
                }
            )
        payload = {
            "schema_version": ASSISTED_AUTHORING_SCHEMA_VERSION,
            "proposals": proposals,
        }
        try:
            request.validate_response_instance(payload)
            output = request.validate_output(payload)
        except Exception:
            raise ProviderError(
                ProviderErrorCode.INVALID_OUTPUT,
                "The synthetic assisted-authoring adapter produced invalid proposal material.",
            ) from None
        receipt = ProviderReceipt(
            provider=self.provider_name,
            model=request.model,
            endpoint=self.endpoint,
            attempts=1,
            latency_ms=0.0,
            input_tokens=request.estimated_input_tokens,
            output_tokens=None,
            total_tokens=None,
            provider_request_id=None,
            estimated_cost_usd=None,
            pricing_version=None,
        )
        return StructuredJSONResult(output=output, receipt=receipt)
