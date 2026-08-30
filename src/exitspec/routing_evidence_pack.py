"""Immutable B13 Routing Evidence Pack publication and verification.

The B13 layer is deliberately a projection around the existing B9/B10/B11/B12
authority.  It does not calculate a routing verdict.  Publication first
revalidates the exact affirmative contract context through B12, then stores a
bounded set of canonical artifacts.  Verification repeats that work from
bytes on disk before a link can be returned to a caller.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Final, Literal

from pydantic import Field, model_validator

from .canonical import canonical_json_bytes
from .confirmations import ContractConfirmation
from .models import SHA256_PATTERN, FrozenExitSpecModel, POCContract
from .routing_campaign_verifier import (
    RoutingCampaignEvidenceBundleV1,
    RoutingCampaignReductionResultV1,
    parse_routing_campaign_confirmation,
    parse_routing_campaign_contract,
    parse_routing_campaign_evidence,
    reduce_routing_campaign,
    serialize_routing_campaign_confirmation,
    serialize_routing_campaign_contract,
    serialize_routing_campaign_evidence,
    serialize_routing_campaign_reduction_result,
    validate_routing_campaign_contract,
)
from .routing_qualification_receipts import (
    RoutingPolicyQualificationReceiptV1,
    RoutingQualificationAuthorizationV1,
    parse_routing_qualification_receipt,
    serialize_routing_qualification_receipt,
    validate_routing_qualification_receipt,
)

ROUTING_EVIDENCE_PACK_SCHEMA_VERSION: Final = "exitspec.routing-evidence-pack.v1"
ROUTING_EVIDENCE_PACK_SUMMARY_SCHEMA_VERSION: Final = (
    "exitspec.routing-evidence-pack-summary.v1"
)
ROUTING_EVIDENCE_PACK_PROTOCOL_ID: Final = "routing_evidence_pack_v1"
ROUTING_EVIDENCE_PACK_ID_PATTERN: Final = r"^rpk_[a-f0-9]{64}$"
ROUTING_EVIDENCE_PACK_ARTIFACTS: Final = (
    "contract.json",
    "confirmation.json",
    "evidence.json",
    "result.json",
    "receipt.json",
    "summary.json",
    "decision-packet.html",
)
ROUTING_EVIDENCE_PACK_MANIFEST: Final = "artifact-hashes.json"
ROUTING_EVIDENCE_PACK_COMPLETION_MARKER: Final = ".complete"
ROUTING_EVIDENCE_PACK_ENTRIES: Final = frozenset(
    (*ROUTING_EVIDENCE_PACK_ARTIFACTS, ROUTING_EVIDENCE_PACK_MANIFEST, ROUTING_EVIDENCE_PACK_COMPLETION_MARKER)
)

ROUTING_EVIDENCE_PACK_POC_ID: Final = "poc_routing_qualification_demo"
ROUTING_EVIDENCE_PACK_DISPLAY_NAME: Final = "Routing qualification · synthetic demo"
ROUTING_EVIDENCE_PACK_CUSTOMER_LABEL: Final = "Synthetic Routing Protocol Test"

EXPECTED_B11_CONTRACT_SHA256: Final = (
    "66a6642ab761e8430e0a955e4b43de4779dda12fa08207ad25bb708c858bd260"
)
EXPECTED_B11_CONFIRMATION_SHA256: Final = (
    "3a64a55affa7bfc661b311651c55c2120ac8bb9492645c75ccceb3a8e7d8f6d5"
)
EXPECTED_B11_EVIDENCE_SHA256: Final = (
    "01bdc0f93b6f9bb40c72be17bae0aab07edba31f456881e3aa2596e863c31f86"
)
EXPECTED_B12_RECEIPT_SHA256: Final = (
    "c502a1e3bae757015b90ecca96839b5c792a1d3c2fab9a048a40d00829cfaa87"
)
EXPECTED_B12_RECEIPT_ID: Final = (
    "rqr_ab83f702d765ce428c88c7deea0a7aa4f46293c098d25117f59633c6f37b5c34"
)

_MAX_MANIFEST_BYTES: Final = 64 * 1024
_MAX_JSON_ARTIFACT_BYTES: Final = 512 * 1024
_MAX_HTML_ARTIFACT_BYTES: Final = 256 * 1024
_MAX_SUMMARY_BYTES: Final = 64 * 1024
_MAX_JSON_DEPTH: Final = 24
_PACK_ID = re.compile(ROUTING_EVIDENCE_PACK_ID_PATTERN)
_ARTIFACT_HASH = re.compile(SHA256_PATTERN)
_PACKAGED_ROUTING_ROOT = files("exitspec.demo_data").joinpath(
    "routing_qualification"
)


class RoutingEvidencePackError(ValueError):
    """A Routing Evidence Pack failed safe publication or verification."""


class RoutingEvidencePackRunSummaryV1(FrozenExitSpecModel):
    """Compact, presentation-safe facts for one observed B11 repetition."""

    repetition_index: int = Field(ge=1, le=100)
    eligible_assignment_count: int = Field(ge=0, le=200_000)
    attained_count: int = Field(ge=0, le=200_000)
    not_attained_count: int = Field(ge=0, le=200_000)
    not_proven_count: int = Field(ge=0, le=200_000)
    point_estimate: str = Field(pattern=r"^(?:0|1|0\.[0-9]*[1-9])$")
    wilson_lower_bound: str = Field(pattern=r"^(?:0|1|0\.[0-9]*[1-9])$")
    required_attainment_rate: str = Field(pattern=r"^(?:0|1|0\.[0-9]*[1-9])$")
    minimum_sample_count: int = Field(gt=0, le=200_000)
    verdict: Literal["PASS", "FAIL", "NOT_PROVEN"]
    evidence_issues: tuple[str, ...] = Field(max_length=7)

    @model_validator(mode="after")
    def require_count_conservation(self) -> RoutingEvidencePackRunSummaryV1:
        if (
            self.attained_count + self.not_attained_count + self.not_proven_count
            != self.eligible_assignment_count
        ):
            raise ValueError("Routing summary counts must conserve the population.")
        return self


class RoutingEvidencePackSummaryV1(FrozenExitSpecModel):
    """Strictly bounded product projection; no raw evidence or producer claims."""

    schema_version: Literal["exitspec.routing-evidence-pack-summary.v1"] = (
        ROUTING_EVIDENCE_PACK_SUMMARY_SCHEMA_VERSION
    )
    pack_kind: Literal["ROUTING_POLICY_QUALIFICATION"] = (
        "ROUTING_POLICY_QUALIFICATION"
    )
    test_only_label: Literal["TEST ONLY"] = "TEST ONLY"
    evidence_class: Literal["SYNTHETIC_FIXTURE"]
    evidence_use: Literal["TEST_ONLY"]
    verdict: Literal["PASS", "FAIL", "NOT_PROVEN"]
    reason: str = Field(min_length=1, max_length=240)
    contract_id: str = Field(min_length=1, max_length=160)
    contract_version: str = Field(min_length=1, max_length=100)
    contract_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_policy_id: str = Field(min_length=1, max_length=128)
    candidate_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_policy_id: str = Field(min_length=1, max_length=128)
    baseline_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_role: Literal["QUALIFICATION_GATE"] = "QUALIFICATION_GATE"
    baseline_role: Literal["REFERENCE_CONTROL"] = "REFERENCE_CONTROL"
    baseline_is_contextual: Literal[True] = True
    required_repetition_indices: tuple[int, ...] = Field(min_length=1, max_length=100)
    observed_repetition_indices: tuple[int, ...] = Field(min_length=0, max_length=100)
    missing_repetition_indices: tuple[int, ...] = Field(min_length=0, max_length=100)
    candidate_runs: tuple[RoutingEvidencePackRunSummaryV1, ...] = Field(
        min_length=0, max_length=100
    )
    baseline_runs: tuple[RoutingEvidencePackRunSummaryV1, ...] = Field(
        min_length=0, max_length=100
    )
    next_human_action: str = Field(min_length=1, max_length=320)
    authorization: RoutingQualificationAuthorizationV1

    @model_validator(mode="after")
    def require_complete_order_and_authority(self) -> RoutingEvidencePackSummaryV1:
        required = self.required_repetition_indices
        observed = self.observed_repetition_indices
        if required != tuple(range(1, len(required) + 1)):
            raise ValueError("Summary repetitions must use canonical order.")
        if observed != tuple(sorted(observed)) or len(set(observed)) != len(observed):
            raise ValueError("Summary observed repetitions must be unique and ordered.")
        if self.missing_repetition_indices != tuple(
            index for index in required if index not in observed
        ):
            raise ValueError("Summary missing repetitions must match observed runs.")
        if tuple(run.repetition_index for run in self.candidate_runs) != observed:
            raise ValueError("Candidate summary runs must match observed repetitions.")
        if tuple(run.repetition_index for run in self.baseline_runs) != observed:
            raise ValueError("Baseline summary runs must match observed repetitions.")
        if self.candidate_policy_id == self.baseline_policy_id:
            raise ValueError("Candidate and baseline identities must differ.")
        if self.candidate_policy_sha256 == self.baseline_policy_sha256:
            raise ValueError("Candidate and baseline digests must differ.")
        if self.missing_repetition_indices and self.verdict != "NOT_PROVEN":
            raise ValueError("Missing evidence can only be NOT_PROVEN.")
        if self.authorization != RoutingQualificationAuthorizationV1():
            raise ValueError("Routing summary authority must remain zero-authority.")
        return self


@dataclass(frozen=True, slots=True)
class RoutingEvidencePackPublication:
    """Immutable identity returned only after a successful publication."""

    pack_id: str
    evidence_pack_url: str
    evidence_pack_sha256: str
    manifest_sha256: str
    decision_packet_sha256: str
    artifact_hashes: Mapping[str, str]
    receipt_id: str
    verdict: str


@dataclass(frozen=True, slots=True)
class RoutingEvidencePackDemoContext:
    """Exact B11/B12 inputs used by the explicit local synthetic demo."""

    contract: POCContract
    confirmation: ContractConfirmation
    evidence: RoutingCampaignEvidenceBundleV1
    result: RoutingCampaignReductionResultV1
    receipt: RoutingPolicyQualificationReceiptV1


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fail(message: str) -> None:
    raise RoutingEvidencePackError(message)


def _write_file(path: Path, content: bytes, limit: int) -> None:
    if len(content) > limit:
        _fail("Routing Evidence Pack artifact is oversized.")
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)
    except (OSError, ValueError) as error:
        raise RoutingEvidencePackError("Routing Evidence Pack publication failed safely.") from error


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise RoutingEvidencePackError("Routing Evidence Pack directory sync failed.") from error


def _validated_root(root: Path, *, create: bool) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        _fail("Routing Evidence Pack output root must be absolute.")
    try:
        if create:
            root.mkdir(parents=True, exist_ok=True)
        elif not root.exists():
            _fail("Routing Evidence Pack output root is unavailable.")
        if root.is_symlink() or not root.is_dir():
            _fail("Routing Evidence Pack output root is unsafe.")
        return root.resolve(strict=True)
    except RoutingEvidencePackError:
        raise
    except OSError as error:
        raise RoutingEvidencePackError("Routing Evidence Pack output root is unsafe.") from error


def _validate_pack_id(pack_id: object) -> str:
    if type(pack_id) is not str or _PACK_ID.fullmatch(pack_id) is None:
        _fail("Routing Evidence Pack identity is invalid.")
    return pack_id


def _stat_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _bounded_read_fd(descriptor: int, limit: int) -> bytes:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > limit
    ):
        _fail("Routing Evidence Pack artifact is unsafe or oversized.")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, limit - size + 1))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                _fail("Routing Evidence Pack artifact is oversized.")
        after = os.fstat(descriptor)
    except OSError as error:
        raise RoutingEvidencePackError(
            "Routing Evidence Pack artifact cannot be read safely."
        ) from error
    if (
        _stat_signature(before) != _stat_signature(after)
        or after.st_size != size
    ):
        _fail("Routing Evidence Pack artifact changed during read.")
    return b"".join(chunks)


@contextmanager
def _open_pack_directory(output_root: Path, pack_id: str):
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("Routing Evidence Pack no-follow descriptor boundary is unavailable.")
    root = _validated_root(output_root, create=False)
    directory_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    root_descriptor: int | None = None
    pack_descriptor: int | None = None
    try:
        root_descriptor = os.open(root, directory_flags)
        pack_descriptor = os.open(pack_id, directory_flags, dir_fd=root_descriptor)
        yield root_descriptor, pack_descriptor
    except RoutingEvidencePackError:
        raise
    except (OSError, ValueError) as error:
        raise RoutingEvidencePackError(
            "Routing Evidence Pack directory cannot be opened safely."
        ) from error
    finally:
        if pack_descriptor is not None:
            try:
                os.close(pack_descriptor)
            except OSError:
                pass
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except OSError:
                pass


def _pack_entry_limit(name: str) -> int:
    return (
        0
        if name == ROUTING_EVIDENCE_PACK_COMPLETION_MARKER
        else _MAX_MANIFEST_BYTES
        if name == ROUTING_EVIDENCE_PACK_MANIFEST
        else _MAX_HTML_ARTIFACT_BYTES
        if name.endswith(".html")
        else _MAX_SUMMARY_BYTES
        if name == "summary.json"
        else _MAX_JSON_ARTIFACT_BYTES
    )


def _read_pack_entry(pack_descriptor: int, name: str) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=pack_descriptor,
        )
        return _bounded_read_fd(descriptor, _pack_entry_limit(name))
    except RoutingEvidencePackError:
        raise
    except (OSError, ValueError) as error:
        raise RoutingEvidencePackError(
            "Routing Evidence Pack artifact cannot be opened safely."
        ) from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _assert_pack_path_identity(
    root_descriptor: int,
    pack_id: str,
    pack_descriptor: int,
) -> None:
    try:
        named = os.stat(
            pack_id,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        opened = os.fstat(pack_descriptor)
    except OSError as error:
        raise RoutingEvidencePackError(
            "Routing Evidence Pack directory identity cannot be verified."
        ) from error
    if _stat_signature(named) != _stat_signature(opened):
        _fail("Routing Evidence Pack directory was replaced during verification.")


def _safe_child(root: Path, relative: object) -> Path | None:
    if type(relative) is not str:
        return None
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    logical = PurePosixPath(relative)
    if logical.is_absolute() or "\\" in relative:
        return None
    target = root.joinpath(*parts)
    try:
        if target.resolve(strict=False).parent != target.parent.resolve(strict=False):
            return None
    except OSError:
        return None
    return target


def _bounded_read(path: Path, limit: int) -> bytes:
    try:
        stat = path.lstat()
        if stat.st_size > limit or path.is_symlink() or not path.is_file():
            _fail("Routing Evidence Pack artifact is unsafe or oversized.")
        content = path.read_bytes()
    except RoutingEvidencePackError:
        raise
    except (OSError, ValueError) as error:
        raise RoutingEvidencePackError("Routing Evidence Pack artifact cannot be read safely.") from error
    if len(content) > limit:
        _fail("Routing Evidence Pack artifact is oversized.")
    return content


def _strict_json_loads(content: bytes, *, label: str) -> object:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _fail(f"Routing Evidence Pack {label} has duplicate keys.")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        _fail(f"Routing Evidence Pack {label} contains a non-finite number: {value}.")

    try:
        value = json.loads(
            content,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except RoutingEvidencePackError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RoutingEvidencePackError(f"Routing Evidence Pack {label} is invalid JSON.") from error
    def depth(item: object) -> int:
        if isinstance(item, dict):
            return 1 + max((depth(value) for value in item.values()), default=0)
        if isinstance(item, list):
            return 1 + max((depth(value) for value in item), default=0)
        return 0

    if depth(value) > _MAX_JSON_DEPTH:
        _fail(f"Routing Evidence Pack {label} is too deeply nested.")
    try:
        canonical = canonical_json_bytes(value)
    except (TypeError, ValueError) as error:
        raise RoutingEvidencePackError(
            f"Routing Evidence Pack {label} cannot be canonicalized."
        ) from error
    if canonical != content:
        _fail(f"Routing Evidence Pack {label} is not canonical JSON.")
    return value


def _context_artifacts(
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: RoutingCampaignEvidenceBundleV1,
    result: RoutingCampaignReductionResultV1,
    receipt: RoutingPolicyQualificationReceiptV1,
) -> dict[str, bytes]:
    """Revalidate B11/B12 and serialize every authority input canonically."""

    try:
        validated_contract = validate_routing_campaign_contract(contract)
        validated_evidence = parse_routing_campaign_evidence(
            serialize_routing_campaign_evidence(evidence)
        )
        validated_receipt = validate_routing_qualification_receipt(
            validated_contract, confirmation, validated_evidence, result, receipt
        )
        contract_bytes = serialize_routing_campaign_contract(validated_contract)
        confirmation_bytes = serialize_routing_campaign_confirmation(confirmation)
        evidence_bytes = serialize_routing_campaign_evidence(validated_evidence)
        result_bytes = serialize_routing_campaign_reduction_result(
            validated_contract, confirmation, validated_evidence, result
        )
        receipt_bytes = serialize_routing_qualification_receipt(
            validated_contract,
            confirmation,
            validated_evidence,
            result,
            validated_receipt,
        )
    except Exception as error:
        if isinstance(error, RoutingEvidencePackError):
            raise
        raise RoutingEvidencePackError(
            "Routing Evidence Pack inputs failed B11/B12 context validation."
        ) from error
    if validated_contract.canonical_hash != EXPECTED_B11_CONTRACT_SHA256:
        _fail("Routing Evidence Pack contract is not the frozen B11 identity.")
    if hashlib.sha256(confirmation_bytes).hexdigest() != EXPECTED_B11_CONFIRMATION_SHA256:
        _fail("Routing Evidence Pack confirmation is not the frozen B11 identity.")
    if hashlib.sha256(evidence_bytes).hexdigest() != EXPECTED_B11_EVIDENCE_SHA256:
        _fail("Routing Evidence Pack evidence is not the frozen B11 identity.")
    if hashlib.sha256(receipt_bytes).hexdigest() != EXPECTED_B12_RECEIPT_SHA256:
        _fail("Routing Evidence Pack receipt is not the frozen B12 identity.")
    if receipt.receipt_id != EXPECTED_B12_RECEIPT_ID:
        _fail("Routing Evidence Pack receipt ID is not the frozen B12 identity.")
    return {
        "contract.json": contract_bytes,
        "confirmation.json": confirmation_bytes,
        "evidence.json": evidence_bytes,
        "result.json": result_bytes,
        "receipt.json": receipt_bytes,
    }


def _run_summaries(
    result: RoutingCampaignReductionResultV1,
) -> tuple[tuple[RoutingEvidencePackRunSummaryV1, ...], tuple[RoutingEvidencePackRunSummaryV1, ...]]:
    candidate: list[RoutingEvidencePackRunSummaryV1] = []
    baseline: list[RoutingEvidencePackRunSummaryV1] = []
    for run in result.run_results:
        for role, target in (("candidate", candidate), ("baseline", baseline)):
            policy = run.policy_results[0 if role == "candidate" else 1]
            target.append(
                RoutingEvidencePackRunSummaryV1(
                    repetition_index=run.repetition_index,
                    eligible_assignment_count=policy.eligible_assignment_count,
                    attained_count=policy.attained_count,
                    not_attained_count=policy.not_attained_count,
                    not_proven_count=policy.not_proven_count,
                    point_estimate=policy.point_estimate,
                    wilson_lower_bound=policy.wilson_lower_bound,
                    required_attainment_rate=policy.required_attainment_rate,
                    minimum_sample_count=policy.minimum_sample_count,
                    verdict=policy.verdict,
                    evidence_issues=(
                        *run.evidence_issues,
                        *policy.evidence_issues,
                    ),
                )
            )
    return tuple(candidate), tuple(baseline)


def _summary(
    contract: POCContract,
    result: RoutingCampaignReductionResultV1,
    receipt: RoutingPolicyQualificationReceiptV1,
) -> RoutingEvidencePackSummaryV1:
    campaign = contract.criteria[0]
    candidate_runs, baseline_runs = _run_summaries(result)
    missing = tuple(receipt.missing_repetition_indices)
    if missing:
        listed = ", ".join(str(index) for index in missing)
        reason = f"Required repetition {listed} is missing; routing qualification is NOT_PROVEN."
        next_action = (
            f"A named human/product owner must review the missing repetition ({listed}) "
            "and decide whether to run it; this pack authorizes nothing."
        )
    elif receipt.verdict == "PASS":
        reason = "All required repetitions met the candidate qualification gate with the approved confidence rule."
        next_action = "A named human/product owner must decide whether this evidence supports the intended product action."
    else:
        reason = "The candidate qualification gate did not meet the approved attainment rule."
        next_action = "A named human/product owner must review the failed qualification evidence and decide the next experiment."
    return RoutingEvidencePackSummaryV1(
        evidence_class=receipt.evidence_class,
        evidence_use=receipt.evidence_use,
        verdict=receipt.verdict,
        reason=reason,
        contract_id=receipt.contract_id,
        contract_version=receipt.contract_version,
        contract_sha256=receipt.contract_sha256,
        candidate_policy_id=campaign.candidate_policy.policy_id,
        candidate_policy_sha256=campaign.candidate_policy.policy_sha256,
        baseline_policy_id=campaign.baseline_policy.policy_id,
        baseline_policy_sha256=campaign.baseline_policy.policy_sha256,
        required_repetition_indices=receipt.required_repetition_indices,
        observed_repetition_indices=tuple(
            run.repetition_index for run in result.run_results
        ),
        missing_repetition_indices=missing,
        candidate_runs=candidate_runs,
        baseline_runs=baseline_runs,
        next_human_action=next_action,
        authorization=receipt.authorization,
    )


def _artifact_link(pack_id: str, name: str) -> str:
    return f"/artifacts/{pack_id}/{name}"


def _render_routing_decision_packet(
    pack_id: str,
    summary: RoutingEvidencePackSummaryV1,
    receipt: RoutingPolicyQualificationReceiptV1,
) -> bytes:
    """Render a concise, escaped packet without serializing raw JSON into HTML."""

    esc = lambda value: html.escape(str(value), quote=True)
    candidate_run = summary.candidate_runs[0] if summary.candidate_runs else None
    baseline_run = summary.baseline_runs[0] if summary.baseline_runs else None
    missing = (
        ", ".join(str(index) for index in summary.missing_repetition_indices)
        if summary.missing_repetition_indices
        else "None"
    )
    observed = ", ".join(str(index) for index in summary.observed_repetition_indices) or "None"
    required = ", ".join(str(index) for index in summary.required_repetition_indices)

    def run_detail(label: str, run: RoutingEvidencePackRunSummaryV1 | None) -> str:
        if run is None:
            return f"<p><strong>{esc(label)}:</strong> No repetition admitted.</p>"
        issues = ", ".join(run.evidence_issues) or "None"
        return (
            f"<p><strong>{esc(label)}:</strong> repetition {run.repetition_index}; "
            f"point {esc(run.point_estimate)}; Wilson lower bound {esc(run.wilson_lower_bound)}; "
            f"threshold {esc(run.required_attainment_rate)}; verdict {esc(run.verdict)}; "
            f"issues {esc(issues)}.</p>"
        )

    artifact_links = "".join(
        f'<li><a href="{esc(_artifact_link(pack_id, name))}">{esc(name)}</a></li>'
        for name in ROUTING_EVIDENCE_PACK_ARTIFACTS
        if name != "decision-packet.html"
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ExitSpec · Routing Evidence Pack</title>
  <style>
    :root {{ color-scheme: dark; --ink: #111820; --panel: #1b252e; --border: #35414a; --text: #f2efe8; --muted: #b8c0bf; --orange: #ff9550; --orange-soft: #ffc08f; --green: #a7d7b1; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--ink); color: var(--text); font: 15px/1.45 Inter, ui-sans-serif, system-ui, sans-serif; }}
    main {{ width: min(100% - 32px, 980px); margin: 0 auto; padding: 20px 0 32px; }}
    .kicker {{ margin: 0 0 8px; color: var(--orange-soft); font: 700 11px/1.2 ui-monospace, monospace; letter-spacing: .11em; text-transform: uppercase; }}
    header {{ border-bottom: 1px solid var(--border); padding-bottom: 14px; }}
    h1 {{ margin: 0; font: 600 clamp(42px, 8vw, 76px)/.92 "Iowan Old Style", Charter, Georgia, serif; letter-spacing: -.06em; }}
    h2 {{ margin: 0 0 7px; font-size: 14px; }}
    p {{ margin: 0; color: var(--muted); }}
    .badge {{ display: inline-block; margin-top: 12px; border: 1px solid var(--orange); padding: 5px 8px; color: var(--orange-soft); font: 800 11px/1 ui-monospace, monospace; letter-spacing: .08em; }}
    .reason {{ margin-top: 12px; color: var(--text); font-size: 17px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }}
    .card, .authority {{ border: 1px solid var(--border); background: var(--panel); padding: 13px; }}
    .card p + p {{ margin-top: 5px; }}
    .label {{ color: var(--orange-soft); font: 800 10px/1.2 ui-monospace, monospace; letter-spacing: .08em; text-transform: uppercase; }}
    .value {{ display: block; margin-top: 4px; color: var(--text); font-size: 16px; font-weight: 700; }}
    .next {{ margin-top: 10px; border-left: 3px solid var(--orange); }}
    .authority {{ margin-top: 10px; border-left: 3px solid var(--orange); }}
    .authority strong {{ display: block; margin-bottom: 4px; color: var(--text); }}
    details {{ margin-top: 10px; border: 1px solid var(--border); background: #151e25; }}
    summary {{ cursor: pointer; padding: 10px 12px; color: var(--text); font-weight: 700; }}
    .detail-body {{ max-height: 170px; overflow: auto; border-top: 1px solid var(--border); padding: 11px 12px; }}
    .detail-body p + p {{ margin-top: 7px; }}
    code {{ overflow-wrap: anywhere; color: var(--orange-soft); font: 12px ui-monospace, monospace; }}
    ul {{ margin: 0; padding-left: 20px; }}
    a {{ color: var(--orange-soft); }}
    :focus-visible {{ outline: 3px solid var(--orange); outline-offset: 3px; }}
    @media (max-width: 650px) {{ main {{ width: min(100% - 22px, 980px); padding-top: 14px; }} .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <p class="kicker">ExitSpec · Routing Evidence Pack · {esc(summary.evidence_use)}</p>
    <h1 id="routing-verdict">{esc(summary.verdict)}</h1>
    <p class="badge" id="routing-test-only">TEST ONLY · Synthetic fixture</p>
    <p class="reason" id="routing-reason">{esc(summary.reason)}</p>
  </header>
  <section class="grid" aria-label="Routing qualification summary">
    <article class="card"><span class="label">Candidate qualification subject</span><strong class="value">{esc(summary.candidate_policy_id)}</strong><p>Gate · {esc(candidate_run.verdict if candidate_run else "NOT_PROVEN")}</p></article>
    <article class="card"><span class="label">Contextual baseline</span><strong class="value">{esc(summary.baseline_policy_id)}</strong><p>Reference control · never pooled with the candidate.</p></article>
    <article class="card"><span class="label">Confidence and threshold</span>{run_detail("Candidate", candidate_run)}</article>
    <article class="card"><span class="label">Evidence completeness</span><p><strong>Required:</strong> {esc(required)}</p><p><strong>Observed:</strong> {esc(observed)}</p><p><strong>Missing:</strong> {esc(missing)}</p></article>
  </section>
  <section class="card next" aria-labelledby="next-human-action"><h2 id="next-human-action">Next human action</h2><p>{esc(summary.next_human_action)}</p></section>
  <section class="authority" aria-label="Zero authority"><strong>Zero authority</strong><p>Every verdict is evidence only: no deployment, shipping, production traffic, traffic expansion, release, spending, procurement, or contract-mutation authority. A separate named human/product decision remains required.</p></section>
  <details><summary>Method and contextual baseline</summary><div class="detail-body">{run_detail("Candidate", candidate_run)}{run_detail("Baseline", baseline_run)}<p>Reducer: <code>{esc(receipt.reducer_id)} {esc(receipt.reducer_version)}</code>. Candidate is the qualification gate; baseline is contextual reference control.</p></div></details>
  <details><summary>Bound identities and hashes</summary><div class="detail-body"><p>Contract: <code>{esc(summary.contract_id)} · v{esc(summary.contract_version)} · {esc(summary.contract_sha256)}</code></p><p>Receipt: <code>{esc(receipt.receipt_id)}</code></p><p>Evidence set: <code>{esc(receipt.evidence_set_sha256)}</code></p><p>Run identities: <code>{esc(", ".join(run.run_id for run in receipt.evidence_runs) or "None")}</code></p></div></details>
  <details><summary>Artifact links</summary><div class="detail-body"><ul>{artifact_links}</ul></div></details>
</main>
</body>
</html>
"""
    return document.encode("utf-8")


def _publication(
    pack_id: str,
    manifest_bytes: bytes,
    artifact_bytes: Mapping[str, bytes],
    receipt: RoutingPolicyQualificationReceiptV1,
) -> RoutingEvidencePackPublication:
    hashes = {name: _sha256(content) for name, content in sorted(artifact_bytes.items())}
    manifest_sha256 = _sha256(manifest_bytes)
    return RoutingEvidencePackPublication(
        pack_id=pack_id,
        evidence_pack_url=_artifact_link(pack_id, "decision-packet.html"),
        evidence_pack_sha256=manifest_sha256,
        manifest_sha256=manifest_sha256,
        decision_packet_sha256=hashes["decision-packet.html"],
        artifact_hashes=hashes,
        receipt_id=receipt.receipt_id,
        verdict=receipt.verdict,
    )


def publish_routing_evidence_pack(
    output_root: Path,
    contract: POCContract,
    confirmation: ContractConfirmation,
    evidence: RoutingCampaignEvidenceBundleV1,
    result: RoutingCampaignReductionResultV1,
    receipt: RoutingPolicyQualificationReceiptV1,
) -> RoutingEvidencePackPublication:
    """Publish one content-addressed, immutable, completion-marked B13 pack."""

    root = _validated_root(output_root, create=True)
    authority_artifacts = _context_artifacts(contract, confirmation, evidence, result, receipt)
    validated_contract = parse_routing_campaign_contract(authority_artifacts["contract.json"])
    validated_confirmation = parse_routing_campaign_confirmation(authority_artifacts["confirmation.json"])
    validated_evidence = parse_routing_campaign_evidence(authority_artifacts["evidence.json"])
    validated_result = reduce_routing_campaign(validated_contract, validated_confirmation, validated_evidence)
    validated_receipt = parse_routing_qualification_receipt(authority_artifacts["receipt.json"])
    summary = _summary(validated_contract, validated_result, validated_receipt)
    summary_bytes = canonical_json_bytes(summary.model_dump(mode="json"))
    pack_id = _validate_pack_id(f"rpk_{_sha256(authority_artifacts['receipt.json'])}")
    artifact_bytes: dict[str, bytes] = {
        **authority_artifacts,
        "summary.json": summary_bytes,
        "decision-packet.html": _render_routing_decision_packet(pack_id, summary, validated_receipt),
    }
    for name, content in artifact_bytes.items():
        limit = _MAX_HTML_ARTIFACT_BYTES if name.endswith(".html") else (
            _MAX_SUMMARY_BYTES if name == "summary.json" else _MAX_JSON_ARTIFACT_BYTES
        )
        if len(content) > limit:
            _fail("Routing Evidence Pack artifact is oversized.")
    manifest = {
        "schema_version": ROUTING_EVIDENCE_PACK_SCHEMA_VERSION,
        "protocol_id": ROUTING_EVIDENCE_PACK_PROTOCOL_ID,
        "pack_id": pack_id,
        "artifacts": {
            name: _sha256(content) for name, content in sorted(artifact_bytes.items())
        },
    }
    manifest_bytes = canonical_json_bytes(manifest)
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        _fail("Routing Evidence Pack manifest is oversized.")
    destination = root / pack_id
    temporary: Path | None = None
    claimed = False
    try:
        temporary = Path(tempfile.mkdtemp(prefix=f".{pack_id}.", dir=root))
        temporary.chmod(0o700)
        for name, content in artifact_bytes.items():
            _write_file(temporary / name, content, _MAX_HTML_ARTIFACT_BYTES if name.endswith(".html") else _MAX_JSON_ARTIFACT_BYTES)
        _write_file(temporary / ROUTING_EVIDENCE_PACK_MANIFEST, manifest_bytes, _MAX_MANIFEST_BYTES)
        _fsync_directory(temporary)
        try:
            destination.mkdir(mode=0o700)
        except FileExistsError as error:
            raise RoutingEvidencePackError("Routing Evidence Pack destination collision detected.") from error
        claimed = True
        for name in (*ROUTING_EVIDENCE_PACK_ARTIFACTS, ROUTING_EVIDENCE_PACK_MANIFEST):
            source = temporary / name
            target = destination / name
            try:
                os.link(source, target)
            except FileExistsError as error:
                raise RoutingEvidencePackError("Routing Evidence Pack destination collision detected.") from error
            source.unlink()
            target.chmod(0o600)
        _write_file(destination / ROUTING_EVIDENCE_PACK_COMPLETION_MARKER, b"", 0)
        _fsync_directory(destination)
        shutil.rmtree(temporary)
        temporary = None
        _fsync_directory(root)
    except RoutingEvidencePackError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise RoutingEvidencePackError("Routing Evidence Pack publication failed safely.") from error
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        if claimed and temporary is not None:
            shutil.rmtree(destination, ignore_errors=True)
    return _publication(pack_id, manifest_bytes, artifact_bytes, validated_receipt)


def _verify_pack_contents(
    pack_id: str,
    manifest_bytes: bytes,
    artifact_bytes: Mapping[str, bytes],
    completion_marker: bytes,
) -> RoutingEvidencePackPublication:
    manifest = _strict_json_loads(manifest_bytes, label="manifest")
    if (
        type(manifest) is not dict
        or set(manifest) != {"schema_version", "protocol_id", "pack_id", "artifacts"}
        or manifest["schema_version"] != ROUTING_EVIDENCE_PACK_SCHEMA_VERSION
        or manifest["protocol_id"] != ROUTING_EVIDENCE_PACK_PROTOCOL_ID
        or manifest["pack_id"] != pack_id
        or type(manifest["artifacts"]) is not dict
        or set(manifest["artifacts"]) != set(ROUTING_EVIDENCE_PACK_ARTIFACTS)
    ):
        _fail("Routing Evidence Pack manifest is invalid.")
    if set(artifact_bytes) != set(ROUTING_EVIDENCE_PACK_ARTIFACTS):
        _fail("Routing Evidence Pack artifact set is invalid.")
    for name in ROUTING_EVIDENCE_PACK_ARTIFACTS:
        expected = manifest["artifacts"].get(name)
        if type(expected) is not str or _ARTIFACT_HASH.fullmatch(expected) is None:
            _fail("Routing Evidence Pack manifest artifact entry is invalid.")
        content = artifact_bytes[name]
        if _sha256(content) != expected:
            _fail("Routing Evidence Pack artifact hash verification failed.")
        if name.endswith(".json"):
            _strict_json_loads(content, label=name)
    if completion_marker != b"":
        _fail("Routing Evidence Pack completion marker is invalid.")
    contract = parse_routing_campaign_contract(artifact_bytes["contract.json"])
    confirmation = parse_routing_campaign_confirmation(artifact_bytes["confirmation.json"])
    evidence = parse_routing_campaign_evidence(artifact_bytes["evidence.json"])
    result = RoutingCampaignReductionResultV1.model_validate_json(
        artifact_bytes["result.json"], strict=True
    )
    receipt = parse_routing_qualification_receipt(artifact_bytes["receipt.json"])
    _context_artifacts(contract, confirmation, evidence, result, receipt)
    expected_result_bytes = serialize_routing_campaign_reduction_result(
        contract, confirmation, evidence, result
    )
    if expected_result_bytes != artifact_bytes["result.json"]:
        _fail("Routing Evidence Pack result is not canonical or context-bound.")
    summary = RoutingEvidencePackSummaryV1.model_validate_json(
        artifact_bytes["summary.json"], strict=True
    )
    expected_summary = _summary(contract, result, receipt)
    if summary != expected_summary:
        _fail("Routing Evidence Pack summary is not context-bound.")
    expected_html = _render_routing_decision_packet(pack_id, summary, receipt)
    if expected_html != artifact_bytes["decision-packet.html"]:
        _fail("Routing Evidence Pack decision packet is not deterministic.")
    return _publication(pack_id, manifest_bytes, artifact_bytes, receipt)


def _read_verified_pack(
    output_root: Path,
    pack_id: str,
) -> tuple[RoutingEvidencePackPublication, dict[str, bytes]]:
    """Read and verify one pack entirely through one anchored descriptor chain."""

    pack_id = _validate_pack_id(pack_id)
    try:
        with _open_pack_directory(output_root, pack_id) as (
            root_descriptor,
            pack_descriptor,
        ):
            _assert_pack_path_identity(root_descriptor, pack_id, pack_descriptor)
            entries = set(os.listdir(pack_descriptor))
            if entries != ROUTING_EVIDENCE_PACK_ENTRIES:
                _fail("Routing Evidence Pack directory entries are invalid.")
            manifest_bytes = _read_pack_entry(
                pack_descriptor, ROUTING_EVIDENCE_PACK_MANIFEST
            )
            artifact_bytes = {
                name: _read_pack_entry(pack_descriptor, name)
                for name in ROUTING_EVIDENCE_PACK_ARTIFACTS
            }
            completion_marker = _read_pack_entry(
                pack_descriptor, ROUTING_EVIDENCE_PACK_COMPLETION_MARKER
            )
            _assert_pack_path_identity(root_descriptor, pack_id, pack_descriptor)
            if set(os.listdir(pack_descriptor)) != entries:
                _fail("Routing Evidence Pack directory changed during verification.")
            publication = _verify_pack_contents(
                pack_id, manifest_bytes, artifact_bytes, completion_marker
            )
            return publication, {
                **artifact_bytes,
                ROUTING_EVIDENCE_PACK_MANIFEST: manifest_bytes,
                ROUTING_EVIDENCE_PACK_COMPLETION_MARKER: completion_marker,
            }
    except RoutingEvidencePackError:
        raise
    except Exception as error:
        raise RoutingEvidencePackError(
            "Routing Evidence Pack verification failed safely."
        ) from error


def verify_routing_evidence_pack(
    output_root: Path,
    pack_id: str,
) -> RoutingEvidencePackPublication:
    """Verify bytes, exact layout, and the full B11/B12 context before linking."""

    publication, _ = _read_verified_pack(output_root, pack_id)
    return publication


def read_routing_evidence_pack_artifact(
    output_root: Path,
    pack_id: str,
    artifact_name: str,
) -> bytes:
    """Return one artifact only after full anchored pack verification."""

    if (
        type(artifact_name) is not str
        or artifact_name not in ROUTING_EVIDENCE_PACK_ENTRIES
    ):
        _fail("Routing Evidence Pack artifact name is invalid.")
    _, artifacts = _read_verified_pack(output_root, pack_id)
    return artifacts[artifact_name]


def load_routing_evidence_demo_context(
    root: Path | None = None,
) -> RoutingEvidencePackDemoContext:
    """Load the explicit repository-seeded synthetic B13 fixture and validate it."""

    fixture_root = _PACKAGED_ROUTING_ROOT if root is None else root
    paths = {
        "contract": fixture_root / "contracts" / "routing-campaign-reduction-v1.synthetic.json",
        "confirmation": fixture_root / "contracts" / "routing-campaign-reduction-v1.synthetic.confirmation.json",
        "evidence": fixture_root / "evidence" / "routing-campaign-evidence-v1.synthetic.json",
        "receipt": fixture_root / "receipts" / "routing-qualification-receipt-v1.synthetic.json",
    }
    try:
        # The repository fixture files are human-readable JSON.  B11's byte
        # boundary intentionally requires canonical bytes, so parse the
        # reviewed fixture objects first and immediately canonicalize them at
        # the B13 authority boundary.
        contract = parse_routing_campaign_contract(
            json.loads(paths["contract"].read_text(encoding="utf-8"))
        )
        confirmation = parse_routing_campaign_confirmation(
            json.loads(paths["confirmation"].read_text(encoding="utf-8"))
        )
        evidence = parse_routing_campaign_evidence(
            json.loads(paths["evidence"].read_text(encoding="utf-8"))
        )
        result = reduce_routing_campaign(contract, confirmation, evidence)
        receipt = parse_routing_qualification_receipt(
            json.loads(paths["receipt"].read_text(encoding="utf-8"))
        )
        _context_artifacts(contract, confirmation, evidence, result, receipt)
        validate_routing_qualification_receipt(contract, confirmation, evidence, result, receipt)
        return RoutingEvidencePackDemoContext(contract, confirmation, evidence, result, receipt)
    except RoutingEvidencePackError:
        raise
    except Exception as error:
        raise RoutingEvidencePackError("Seeded routing Evidence Pack fixture is unavailable.") from error


__all__ = [
    "EXPECTED_B11_CONFIRMATION_SHA256",
    "EXPECTED_B11_CONTRACT_SHA256",
    "EXPECTED_B11_EVIDENCE_SHA256",
    "EXPECTED_B12_RECEIPT_ID",
    "EXPECTED_B12_RECEIPT_SHA256",
    "ROUTING_EVIDENCE_PACK_ARTIFACTS",
    "ROUTING_EVIDENCE_PACK_COMPLETION_MARKER",
    "ROUTING_EVIDENCE_PACK_DISPLAY_NAME",
    "ROUTING_EVIDENCE_PACK_ENTRIES",
    "ROUTING_EVIDENCE_PACK_MANIFEST",
    "ROUTING_EVIDENCE_PACK_POC_ID",
    "ROUTING_EVIDENCE_PACK_PROTOCOL_ID",
    "ROUTING_EVIDENCE_PACK_SCHEMA_VERSION",
    "ROUTING_EVIDENCE_PACK_SUMMARY_SCHEMA_VERSION",
    "RoutingEvidencePackDemoContext",
    "RoutingEvidencePackError",
    "RoutingEvidencePackPublication",
    "RoutingEvidencePackRunSummaryV1",
    "RoutingEvidencePackSummaryV1",
    "load_routing_evidence_demo_context",
    "publish_routing_evidence_pack",
    "read_routing_evidence_pack_artifact",
    "verify_routing_evidence_pack",
]
