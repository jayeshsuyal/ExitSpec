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
    ExactToolSelectionPolicy,
    ProposalBatch,
    ProposalClassification,
)
from .providers import (
    ProviderError,
    ProviderErrorCode,
    ProviderReceipt,
    StructuredJSONRequest,
    StructuredJSONResult,
)


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
