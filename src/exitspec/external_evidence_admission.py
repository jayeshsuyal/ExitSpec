"""Provider-neutral admission for one untrusted local evidence package.

The package envelope binds an externally supplied Inferdrome evidence tree to
the PR7 handoff.  The tree is verified by ExitSpec's existing independent
Inferdrome reader and its supported measurements are returned as facts only.
This module never contacts a producer, invokes a provider, or assigns a
verdict.  PR9 owns protocol-specific verdict and receipt issuance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from .canonical import CanonicalizationError, canonical_json_bytes
from .inferdrome_bundle import (
    INFERDROME_VERIFIER_VERSION,
    InferdromeBundleLimits,
    InferdromeBundleRejected,
    RecalculatedInferdromeMeasurements,
    verify_inferdrome_bundle,
)
from .models import FrozenExitSpecModel, POCContract
from .producer_capability import (
    DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID,
    DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION,
    ProducerCapabilityDescriptorV1,
    producer_capability_digest,
)
from .proofability import ProofabilityReportV1, proofability_report_digest
from .prospective_handoff import (
    ProspectiveHandoffV1,
    verify_prospective_handoff,
)
from .qualification_scope import (
    QualificationContextV1,
    QualificationScopeV1,
    qualification_context_digest,
    qualification_scope_digest,
)
from .serving_subject import (
    ServingSubjectManifestV1,
    serving_subject_digest,
)

EXTERNAL_EVIDENCE_PACKAGE_SCHEMA_VERSION: Final = (
    "exitspec.external-evidence-package.v1"
)
EXTERNAL_EVIDENCE_PACKAGE_CANONICALIZATION_VERSION: Final = "rfc8785_jcs_v1"
EXTERNAL_EVIDENCE_PACKAGE_HASH_VERSION: Final = "sha256_v1"
EXTERNAL_EVIDENCE_PACKAGE_DIGEST_DOMAIN: Final = (
    b"exitspec-external-evidence-package-v1\x00"
)
EVIDENCE_CLASS_EXTERNAL_INFERDROME_V1: Final = "EXTERNAL_INFERDROME_V1"
EVIDENCE_CLASS_SYNTHETIC_CI_INFERDROME_V1: Final = "SYNTHETIC_CI_INFERDROME_V1"

_DIGEST_PATTERN: Final = r"^sha256:[a-f0-9]{64}$"
_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_MAX_PACKAGE_BYTES: Final = 32 * 1024
_FORBIDDEN_KEYS: Final = frozenset(
    {
        "acceptance_verdict",
        "authorization",
        "deployment_authorized",
        "producer_verdict",
        "traffic_authorized",
        "verdict",
    }
)


class ExternalEvidenceAdmissionCode(str, Enum):
    """Stable content-safe rejection classes for the PR8 boundary."""

    INVALID_PACKAGE = "INVALID_PACKAGE"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    PROFILE_MISMATCH = "PROFILE_MISMATCH"
    EVIDENCE_CLASS_MISMATCH = "EVIDENCE_CLASS_MISMATCH"
    PRODUCER_VERDICT = "PRODUCER_VERDICT"
    BUNDLE_REJECTED = "BUNDLE_REJECTED"
    RECALCULATION_REJECTED = "RECALCULATION_REJECTED"


class ExternalEvidenceAdmissionRejected(ValueError):
    """An untrusted package cannot enter the ExitSpec evidence boundary."""

    def __init__(self, code: ExternalEvidenceAdmissionCode, message: str) -> None:
        self.code = ExternalEvidenceAdmissionCode(code)
        super().__init__(message)


class _StrictFrozenEvidenceModel(FrozenExitSpecModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


def _reject(code: ExternalEvidenceAdmissionCode, message: str) -> None:
    raise ExternalEvidenceAdmissionRejected(code, message)


def _package_digest_from_projection(projection: Mapping[str, Any]) -> str:
    try:
        content = canonical_json_bytes(projection)
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        _reject(
            ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
            "Evidence package is outside the canonical JSON domain.",
        )
    return "sha256:" + hashlib.sha256(
        EXTERNAL_EVIDENCE_PACKAGE_DIGEST_DOMAIN + content
    ).hexdigest()


class ExternalEvidencePackageV1(_StrictFrozenEvidenceModel):
    """A bounded declaration for one externally supplied evidence tree.

    The local filesystem path is deliberately not part of this identity.  A
    caller supplies it separately to :func:`admit_external_evidence_package`;
    the package binds the tree by its expected bundle digest instead.
    """

    schema_version: Literal[EXTERNAL_EVIDENCE_PACKAGE_SCHEMA_VERSION]
    canonicalization_version: Literal[
        EXTERNAL_EVIDENCE_PACKAGE_CANONICALIZATION_VERSION
    ]
    hash_version: Literal[EXTERNAL_EVIDENCE_PACKAGE_HASH_VERSION]
    evidence_class: Literal[
        EVIDENCE_CLASS_EXTERNAL_INFERDROME_V1,
        EVIDENCE_CLASS_SYNTHETIC_CI_INFERDROME_V1,
    ]
    profile_id: Literal[DECLARED_EXTERNAL_EVIDENCE_PROFILE_ID]
    profile_version: Literal[DECLARED_EXTERNAL_EVIDENCE_PROFILE_VERSION]
    capability_digest: str = Field(pattern=_DIGEST_PATTERN)
    subject_digest: str = Field(pattern=_DIGEST_PATTERN)
    scope_digest: str = Field(pattern=_DIGEST_PATTERN)
    qualification_context_digest: str = Field(pattern=_DIGEST_PATTERN)
    contract_canonical_digest: str = Field(pattern=_DIGEST_PATTERN)
    proofability_report_digest: str = Field(pattern=_DIGEST_PATTERN)
    evidence_set_id: str = Field(
        pattern=_IDENTIFIER_PATTERN,
        min_length=3,
        max_length=128,
    )
    bundle_digest: str = Field(pattern=_DIGEST_PATTERN)
    package_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def require_matching_package_digest(self) -> ExternalEvidencePackageV1:
        projection = self.model_dump(mode="json", exclude={"package_digest"})
        expected = _package_digest_from_projection(projection)
        if not hmac.compare_digest(self.package_digest, expected):
            raise ValueError("package_digest does not bind the package projection.")
        return self


@dataclass(frozen=True, slots=True)
class AdmittedExternalEvidenceV1:
    """Independent facts admitted from one verified package, never a verdict."""

    package_digest: str
    evidence_set_id: str
    evidence_class: str
    profile_id: str
    profile_version: str
    bundle_digest: str
    verifier_version: str
    recalculated: RecalculatedInferdromeMeasurements


def _validated_package(value: object) -> ExternalEvidencePackageV1:
    if type(value) is not ExternalEvidencePackageV1:
        _reject(
            ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
            "A typed ExternalEvidencePackageV1 is required.",
        )
    try:
        raw_state = object.__getattribute__(value, "__dict__")
        extras = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
    except AttributeError:
        _reject(
            ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
            "Evidence package has no inspectable typed state.",
        )
    if (
        type(raw_state) is not dict
        or set(raw_state) != set(ExternalEvidencePackageV1.model_fields)
        or extras
        or private
    ):
        _reject(
            ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
            "Evidence package contains undocumented state.",
        )
    try:
        return ExternalEvidencePackageV1.model_validate(
            value.model_dump(mode="python"),
            strict=True,
        )
    except (ValidationError, TypeError, ValueError):
        _reject(
            ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
            "Evidence package failed strict validation.",
        )


def _verify_context_binding(
    package: ExternalEvidencePackageV1,
    handoff: ProspectiveHandoffV1,
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
    report: ProofabilityReportV1,
) -> None:
    try:
        if not verify_prospective_handoff(
            handoff,
            subject,
            scope,
            context,
            contract,
            descriptor,
            report,
        ):
            _reject(
                ExternalEvidenceAdmissionCode.CONTEXT_MISMATCH,
                "Evidence package is not bound to the prospective handoff.",
            )
        expected = {
            "capability_digest": producer_capability_digest(descriptor),
            "subject_digest": serving_subject_digest(subject),
            "scope_digest": qualification_scope_digest(scope),
            "qualification_context_digest": qualification_context_digest(context),
            "contract_canonical_digest": "sha256:" + (contract.canonical_hash or ""),
            "proofability_report_digest": proofability_report_digest(report),
        }
    except (TypeError, ValueError):
        _reject(
            ExternalEvidenceAdmissionCode.CONTEXT_MISMATCH,
            "Evidence package context could not be validated.",
        )
    for field, expected_value in expected.items():
        if getattr(package, field) != expected_value:
            _reject(
                ExternalEvidenceAdmissionCode.CONTEXT_MISMATCH,
                "Evidence package context does not match the handoff.",
            )
    if (
        package.profile_id != descriptor.profile.profile_id
        or package.profile_version != descriptor.profile.profile_version
    ):
        _reject(
            ExternalEvidenceAdmissionCode.PROFILE_MISMATCH,
            "Evidence package profile does not match the declared capability.",
        )


def _contains_forbidden_key(value: object) -> bool:
    if type(value) is dict:
        return any(
            key.casefold() in _FORBIDDEN_KEYS
            or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if type(value) is list:
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _verify_allowed_bundle_facts(
    verified: Any,
    package: ExternalEvidencePackageV1,
) -> None:
    documents = (
        verified.descriptor,
        verified.resolved_spec,
        verified.request_plan,
        verified.execution,
        verified.environment,
    )
    if any(_contains_forbidden_key(document) for document in documents):
        _reject(
            ExternalEvidenceAdmissionCode.PRODUCER_VERDICT,
            "Producer outcome or authority fields are not admitted.",
        )
    digests = verified.descriptor.get("digests")
    if not isinstance(digests, dict) or digests.get(
        "exitspec_contract_digest"
    ) != package.contract_canonical_digest:
        _reject(
            ExternalEvidenceAdmissionCode.CONTEXT_MISMATCH,
            "Evidence bundle contract digest does not match the package.",
        )

    eligibility = verified.descriptor.get("evidence_eligibility")
    if package.evidence_class == EVIDENCE_CLASS_EXTERNAL_INFERDROME_V1:
        expected = "CUSTOMER_ELIGIBLE"
    else:
        expected = "SYNTHETIC"
    if eligibility != expected:
        _reject(
            ExternalEvidenceAdmissionCode.EVIDENCE_CLASS_MISMATCH,
            "Evidence class does not match the bundle eligibility declaration.",
        )


def admit_external_evidence_package(
    package_path: str | Path,
    package: ExternalEvidencePackageV1,
    handoff: ProspectiveHandoffV1,
    subject: ServingSubjectManifestV1,
    scope: QualificationScopeV1,
    context: QualificationContextV1,
    contract: POCContract,
    descriptor: ProducerCapabilityDescriptorV1,
    report: ProofabilityReportV1,
    *,
    limits: InferdromeBundleLimits | None = None,
) -> AdmittedExternalEvidenceV1:
    """Admit and recalculate one local package without assigning a verdict."""

    package = _validated_package(package)
    _verify_context_binding(
        package,
        handoff,
        subject,
        scope,
        context,
        contract,
        descriptor,
        report,
    )
    try:
        path = Path(package_path)
        verified = verify_inferdrome_bundle(
            path,
            expected_bundle_digest=package.bundle_digest,
            limits=limits,
            require_customer_eligible=(
                package.evidence_class == EVIDENCE_CLASS_EXTERNAL_INFERDROME_V1
            ),
        )
        _verify_allowed_bundle_facts(verified, package)
    except ExternalEvidenceAdmissionRejected:
        raise
    except (InferdromeBundleRejected, OSError, TypeError, ValueError) as error:
        _reject(
            ExternalEvidenceAdmissionCode.BUNDLE_REJECTED,
            "Evidence bundle failed independent admission.",
        )
        raise AssertionError("unreachable") from error
    recalculated = verified.recalculated
    if type(recalculated) is not RecalculatedInferdromeMeasurements:
        _reject(
            ExternalEvidenceAdmissionCode.RECALCULATION_REJECTED,
            "Evidence recalculation did not produce typed facts.",
        )
    return AdmittedExternalEvidenceV1(
        package_digest=package.package_digest,
        evidence_set_id=package.evidence_set_id,
        evidence_class=package.evidence_class,
        profile_id=package.profile_id,
        profile_version=package.profile_version,
        bundle_digest=package.bundle_digest,
        verifier_version=INFERDROME_VERIFIER_VERSION,
        recalculated=recalculated,
    )


def serialize_external_evidence_package(
    value: ExternalEvidencePackageV1,
) -> bytes:
    package = _validated_package(value)
    content = canonical_json_bytes(package.model_dump(mode="json"))
    if len(content) > _MAX_PACKAGE_BYTES:
        _reject(
            ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
            "Evidence package declaration is oversized.",
        )
    return content


def parse_external_evidence_package(
    content: bytes | Mapping[str, Any],
) -> ExternalEvidencePackageV1:
    if type(content) is bytes:
        if len(content) > _MAX_PACKAGE_BYTES:
            _reject(
                ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
                "Evidence package declaration is oversized.",
            )
        try:
            payload = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_float=_reject_float,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            _reject(
                ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
                "Evidence package declaration is not valid JSON.",
            )
        if type(payload) is not dict:
            _reject(
                ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
                "Evidence package declaration must be one JSON object.",
            )
        try:
            canonical = canonical_json_bytes(payload)
        except (CanonicalizationError, RecursionError, TypeError, ValueError):
            _reject(
                ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
                "Evidence package declaration is outside the canonical JSON domain.",
            )
        if canonical != content:
            _reject(
                ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
                "Evidence package declaration is not canonical JSON.",
            )
    elif type(content) is dict:
        payload = content
    else:
        _reject(
            ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
            "Evidence package declaration must be bytes or one JSON object.",
        )
    try:
        return ExternalEvidencePackageV1.model_validate(payload, strict=True)
    except (ValidationError, TypeError, ValueError):
        _reject(
            ExternalEvidenceAdmissionCode.INVALID_PACKAGE,
            "Evidence package declaration failed strict validation.",
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _reject_float(_: str) -> None:
    raise ValueError("floating-point values are not in the package envelope")


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite values are not in the package envelope")


def external_evidence_package_digest(
    value: ExternalEvidencePackageV1,
) -> str:
    package = _validated_package(value)
    return _package_digest_from_projection(
        package.model_dump(mode="json", exclude={"package_digest"})
    )


__all__ = [
    "EVIDENCE_CLASS_EXTERNAL_INFERDROME_V1",
    "EVIDENCE_CLASS_SYNTHETIC_CI_INFERDROME_V1",
    "EXTERNAL_EVIDENCE_PACKAGE_CANONICALIZATION_VERSION",
    "EXTERNAL_EVIDENCE_PACKAGE_DIGEST_DOMAIN",
    "EXTERNAL_EVIDENCE_PACKAGE_HASH_VERSION",
    "EXTERNAL_EVIDENCE_PACKAGE_SCHEMA_VERSION",
    "AdmittedExternalEvidenceV1",
    "ExternalEvidenceAdmissionCode",
    "ExternalEvidenceAdmissionRejected",
    "ExternalEvidencePackageV1",
    "admit_external_evidence_package",
    "external_evidence_package_digest",
    "parse_external_evidence_package",
    "serialize_external_evidence_package",
]
