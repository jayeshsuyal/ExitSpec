"""Offline P1 admission tests.

Positive cases use explicitly unit-only in-memory verified facts. They never
create CUSTOMER_ELIGIBLE bundles on disk or claim real verifier qualification.
Public negative tests use the unchanged independent verifier and synthetic bytes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from exitspec import inferdrome_prospective_admission as admission
from exitspec import inferdrome_prospective_context as snapshots
from exitspec import inferdrome_prospective_receipt as reporting
from exitspec.canonical import canonical_json_bytes
from exitspec.inferdrome_bundle import (
    InferdromeBundleLimits,
    InferdromeBundleRejected,
    RecalculatedInferdromeMeasurements,
    VerifiedInferdromeBundle,
)

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "examples/inference-performance/inferdrome-p1"
FAKE = ROOT / "tests/fixtures/inferdrome/fake-template"
CASE = "native-p95-under-20ms"
RUN = "run-" + "a" * 32
BUNDLE = "sha256:" + "b" * 64
RECEIVED = datetime(2026, 9, 13, tzinfo=UTC)
PIN = hashlib.sha256((HANDOFF / "handoff-manifest.json").read_bytes()).hexdigest()


def context(case=CASE, root=HANDOFF):
    return snapshots.capture_prospective_context(root, case, PIN)


def unit_verified_facts(ctx, *, p95=1_000_000, failed=0, anomalous=0):
    """Typed normalization stand-in, NOT a verified real bundle or fixture tree."""
    identity = ctx.case.methodology
    resolved = yaml.safe_load(ctx.source_bytes)
    resolved["schema_version"] = "inferdrome.experiment.v1"
    resolved["target"]["api"] = identity.target_api
    resolved["target"]["endpoint"] = identity.target_endpoint
    resolved["execution"].update(
        producer_name="vllm",
        producer_version="0.26.0",
        adapter="vllm_bench_serve",
        adapter_version="1.0.0",
    )
    resolved["workload"]["temperature"] = "0"
    resolved["measurement"] = {
        "streaming": True,
        "ttft_definition": "vllm_first_choices_event_v0_26",
        "choices_span_definition": "last_choices_event_span_v1",
        "metric_definitions_version": "1.0.0",
        "reducer_version": "1.0.0",
    }
    resolved["evidence"].update(
        native_output_sensitivity="RESPONSE_CONTENT", include_request_plan=True
    )
    plan = {
        "run_id": RUN,
        "producer_request_id_prefix": RUN + "-",
        "traffic": copy.deepcopy(resolved["traffic"]),
        "requests": [],
    }
    for index, row in enumerate(ctx.workload_bytes.splitlines()):
        prompt = json.loads(row)["prompt"]
        plan["requests"].append(
            {
                "sequence_index": index,
                "request_id": f"req-{index:08d}",
                "producer_request_id": f"{RUN}-{index}",
                "prompt": {
                    "kind": "inline",
                    "text": prompt,
                    "sha256": "sha256:" + hashlib.sha256(prompt.encode()).hexdigest(),
                },
                "sampling": {
                    "requested_output_tokens": 32,
                    "temperature": "0",
                    "seed": 42,
                },
            }
        )
    descriptor = {
        "run_id": RUN,
        "schema_version": "inferdrome.evidence.v1",
        "execution_mode": "attached_endpoint",
        "evidence_eligibility": "CUSTOMER_ELIGIBLE",
        "producer": {
            "name": "vllm",
            "version": "0.26.0",
            "adapter": "vllm_bench_serve",
            "adapter_version": "1.0.0",
            "native_schema_fingerprint": identity.native_schema_fingerprint,
        },
        "digests": {
            "exitspec_contract_digest": ctx.case.producer_contract_link,
            "source_spec_digest": "sha256:"
            + hashlib.sha256(
                b"inferdrome:source-spec-v1\0" + ctx.source_bytes
            ).hexdigest(),
            "execution_fingerprint": identity.expected_execution_fingerprint,
            "request_plan_digest": "sha256:"
            + hashlib.sha256(
                b"inferdrome:request-plan-v1\0" + canonical_json_bytes(plan)
            ).hexdigest(),
        },
    }
    facts = SimpleNamespace(
        profile_id=identity.managed_profile_id,
        profile_sha256=identity.managed_profile_sha256,
        local_gpu_proof_schema_id=identity.local_gpu_proof_schema_id,
        local_gpu_proof_schema_sha256=identity.local_gpu_proof_schema_sha256,
        validator_version="1.0.0",
        claims_assurance="INTERNAL_CONSISTENCY_ONLY",
    )
    measurement = RecalculatedInferdromeMeasurements(
        100,
        100 - failed,
        failed,
        anomalous,
        Decimal(failed) / 100,
        p95,
        "vllm_first_choices_event_v0_26",
        "sha256:" + "c" * 64,
        "d" * 64,
    )
    return VerifiedInferdromeBundle(
        root=Path("/unit-only-no-bundle-written"),
        bundle_digest=BUNDLE,
        descriptor=descriptor,
        resolved_spec=resolved,
        request_plan=plan,
        execution={
            "configured_traffic": copy.deepcopy(resolved["traffic"]),
            "started_at": "2026-09-12T00:00:00Z",
            "ended_at": "2026-09-12T00:00:01Z",
        },
        environment={},
        records=tuple({} for _ in range(100)),
        recalculated=measurement,
        managed_profile=facts,
    )


def admit_unit(ctx, facts):
    return admission._admit_verified(ctx, facts, RUN, BUNDLE, RECEIVED)


def public_kwargs(**changes):
    return {
        "expected_handoff_manifest_sha256": PIN,
        "expected_bundle_digest": BUNDLE,
        "expected_run_id": RUN,
        "received_at": RECEIVED,
        **changes,
    }


@pytest.mark.parametrize(
    "case,verdict",
    [
        (CASE, "PASS"),
        ("native-p95-under-10ms", "PASS"),
        ("semantic-first-nonempty-under-20ms", "NOT_PROVEN"),
    ],
)
def test_unit_only_each_exact_case_has_truthful_assurance(case, verdict):
    ctx = context(case)
    receipt = admit_unit(ctx, unit_verified_facts(ctx))
    assert receipt.acceptance_verdict == verdict
    assert receipt.assurance.temporal_assurance == "UNAVAILABLE"
    assert receipt.assurance.contract_preceded_measurement is None
    assert receipt.assurance.production_authorization is False
    assert (
        receipt.assurance.confirmation_identity_assurance
        == "PROCESS_LOCAL_DECLARED_IDENTITY_NOT_AUTHENTICATED"
    )
    assert receipt.producer_contract_link == ctx.case.producer_contract_link
    assert "requested_request_plan_digest" not in receipt.model_dump()
    raw = reporting.serialize_prospective_receipt(receipt)
    assert reporting.parse_prospective_receipt(raw) == receipt
    assert (
        reporting.serialize_prospective_receipt(
            admit_unit(ctx, unit_verified_facts(ctx))
        )
        == raw
    )


@pytest.mark.parametrize(
    "threshold,case", [(20_000_000, CASE), (10_000_000, "native-p95-under-10ms")]
)
@pytest.mark.parametrize("offset,verdict", [(-1, "PASS"), (0, "FAIL"), (1, "FAIL")])
def test_unit_only_strict_threshold_equality(threshold, case, offset, verdict):
    ctx = context(case)
    assert (
        admit_unit(
            ctx, unit_verified_facts(ctx, p95=threshold + offset)
        ).acceptance_verdict
        == verdict
    )


@pytest.mark.parametrize("case", [CASE, "semantic-first-nonempty-under-20ms"])
@pytest.mark.parametrize(
    "failed,anomalous,expected",
    [(0, 0, None), (1, 0, "FAIL"), (1, 1, "FAIL"), (100, 100, "FAIL")],
)
def test_unit_only_reliability_precedence_and_missing_latency(
    case, failed, anomalous, expected
):
    ctx = context(case)
    receipt = admit_unit(
        ctx, unit_verified_facts(ctx, p95=None, failed=failed, anomalous=anomalous)
    )
    assert receipt.acceptance_verdict == (expected or "NOT_PROVEN")
    assert receipt.population.failed_count == failed
    assert receipt.population.observed_error_rate == format(Decimal(failed) / 100, "f")


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempted_count", 0),
        ("attempted_count", 99),
        ("successful_count", 101),
        ("failed_count", True),
        ("anomalous_count", 1),
        ("error_rate", Decimal("NaN")),
        ("error_rate", Decimal("0.01")),
        ("p95_ttft_ns", -1),
        ("p95_ttft_ns", False),
        ("p95_ttft_ns", 9_007_199_254_740_992),
    ],
)
def test_invalid_measurements_never_become_performance_fail(field, value):
    ctx = context()
    facts = unit_verified_facts(ctx)
    facts = replace(facts, recalculated=replace(facts.recalculated, **{field: value}))
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="P1_NOT_APPLICABLE"
    ):
        admit_unit(ctx, facts)


def test_public_synthetic_bytes_remain_ineligible():
    raw = (FAKE / "integrity/artifact-hashes.json").read_bytes()
    digest = (
        "sha256:" + hashlib.sha256(b"inferdrome:bundle-manifest-v1\0" + raw).hexdigest()
    )
    run = json.loads((FAKE / "bundle.json").read_bytes())["run_id"]
    with pytest.raises(InferdromeBundleRejected) as error:
        admission.admit_prospective_bundle(
            HANDOFF,
            CASE,
            FAKE,
            **public_kwargs(expected_bundle_digest=digest, expected_run_id=run),
        )
    assert error.value.code.value == "EVIDENCE_INELIGIBLE"
    assert (
        json.loads((FAKE / "bundle.json").read_bytes())["evidence_eligibility"]
        == "SYNTHETIC_ONLY"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_handoff_manifest_sha256", "not-a-pin"),
        ("expected_bundle_digest", "x"),
        ("expected_run_id", "historical"),
        ("received_at", RECEIVED.replace(tzinfo=None)),
        ("limits", InferdromeBundleLimits(max_files=65)),
    ],
)
def test_bad_parameters_refuse_before_verifier(monkeypatch, field, value):
    monkeypatch.setattr(
        admission,
        "verify_inferdrome_bundle",
        lambda *a, **k: pytest.fail("verifier called"),
    )
    with pytest.raises(admission.ProspectiveAdmissionRejected):
        admission.admit_prospective_bundle(
            HANDOFF, CASE, FAKE, **public_kwargs(**{field: value})
        )


def test_public_does_not_accept_caller_created_verified_object():
    ctx = context()
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="BINDING_MISMATCH"
    ):
        admission.admit_prospective_bundle(
            HANDOFF, CASE, unit_verified_facts(ctx), **public_kwargs()
        )


@pytest.mark.parametrize(
    "drift",
    [
        "manifest",
        "confirmation",
        "contract",
        "source",
        "missing",
        "extra",
        "symlink",
        "hardlink",
        "fifo",
    ],
)
def test_context_drift_refuses_before_bundle_verifier(tmp_path, monkeypatch, drift):
    root = tmp_path / "handoff"
    shutil.copytree(HANDOFF, root)
    names = {
        "manifest": "handoff-manifest.json",
        "confirmation": f"confirmations/{CASE}.confirmation.json",
        "contract": f"contracts/{CASE}.frozen.json",
        "source": f"sources/{CASE}.yaml",
    }
    path = root / names.get(drift, f"contracts/{CASE}.frozen.json")
    if drift in names:
        path.write_bytes(path.read_bytes() + b" ")
    elif drift == "missing":
        path.unlink()
    elif drift == "extra":
        (root / "extra").write_bytes(b"x")
    elif drift == "hardlink":
        os.link(path, root / "linked")
    else:
        path.unlink()
        if drift == "symlink":
            path.symlink_to(HANDOFF / f"contracts/{CASE}.frozen.json")
        elif drift == "fifo":
            os.mkfifo(path)
    monkeypatch.setattr(
        admission,
        "verify_inferdrome_bundle",
        lambda *a, **k: pytest.fail("verifier called"),
    )
    with pytest.raises(admission.ProspectiveAdmissionRejected):
        admission.admit_prospective_bundle(root, CASE, FAKE, **public_kwargs())


def test_context_changed_after_capture_cannot_escape_as_receipt(tmp_path, monkeypatch):
    root = tmp_path / "handoff"
    shutil.copytree(HANDOFF, root)
    facts = unit_verified_facts(context(root=root))

    def changed(*args, **kwargs):
        path = root / f"sources/{CASE}.yaml"
        path.write_bytes(path.read_bytes() + b" ")
        return facts

    monkeypatch.setattr(admission, "verify_inferdrome_bundle", changed)
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="CONTEXT_NOT_AUTHORIZED"
    ):
        admission.admit_prospective_bundle(root, CASE, FAKE, **public_kwargs())


@pytest.mark.parametrize(
    "key,value",
    [
        ("descriptor.run_id", "run-" + "f" * 32),
        ("descriptor.digests.exitspec_contract_digest", None),
        ("resolved_spec.links.exitspec_contract_digest", None),
        ("descriptor.digests.exitspec_contract_digest", "sha256:" + "0" * 64),
    ],
)
def test_link_or_run_mismatch_refuses(key, value):
    ctx = context()
    facts = unit_verified_facts(ctx)
    parts = key.split(".")
    item = getattr(facts, parts[0])
    for part in parts[1:-1]:
        item = item[part]
    item[parts[-1]] = value
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="BINDING_MISMATCH"
    ):
        admit_unit(ctx, facts)


@pytest.mark.parametrize(
    "mutation",
    [
        "swap",
        "replace-rehash",
        "prefix",
        "index",
        "request-id",
        "producer-id",
        "digest-only",
        "seed",
        "temperature",
        "tokens",
        "warmup-offset",
        "short",
    ],
)
def test_ordered_workload_binding_is_independent_of_internal_plan_hashes(mutation):
    ctx = context()
    facts = unit_verified_facts(ctx)
    plan = facts.request_plan
    requests = plan["requests"]
    if mutation == "swap":
        requests[0]["prompt"], requests[1]["prompt"] = (
            requests[1]["prompt"],
            requests[0]["prompt"],
        )
    elif mutation == "replace-rehash":
        requests[0]["prompt"]["text"] = "different internally consistent prompt"
        requests[0]["prompt"]["sha256"] = (
            "sha256:"
            + hashlib.sha256(requests[0]["prompt"]["text"].encode()).hexdigest()
        )
    elif mutation == "prefix":
        plan["producer_request_id_prefix"] = "other-"
    elif mutation == "index":
        requests[0]["sequence_index"] = True
    elif mutation == "request-id":
        requests[0]["request_id"] = "req-00000001"
    elif mutation == "producer-id":
        requests[0]["producer_request_id"] = "wrong"
    elif mutation == "digest-only":
        requests[0]["prompt"]["kind"] = "digest_only"
    elif mutation == "seed":
        requests[0]["sampling"]["seed"] = 43
    elif mutation == "temperature":
        requests[0]["sampling"]["temperature"] = 0
    elif mutation == "tokens":
        requests[0]["sampling"]["requested_output_tokens"] = 31
    elif mutation == "warmup-offset":
        requests[0]["prompt"] = requests[10]["prompt"]
    elif mutation == "short":
        requests.pop()
    facts.descriptor["digests"]["request_plan_digest"] = (
        "sha256:"
        + hashlib.sha256(
            b"inferdrome:request-plan-v1\0" + canonical_json_bytes(plan)
        ).hexdigest()
    )
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="P1_NOT_APPLICABLE"
    ):
        admit_unit(ctx, facts)


@pytest.mark.parametrize(
    "where,value",
    [
        ("started_at", "2026-08-26T00:00:00Z"),
        ("started_at", "2026-09-12T00:00:02Z"),
        ("ended_at", "2026-09-14T00:00:00Z"),
        ("ended_at", "2026-09-12T00:00:01"),
    ],
)
def test_declared_chronology_contradiction_never_claims_attestation(where, value):
    ctx = context()
    facts = unit_verified_facts(ctx)
    facts.execution[where] = value
    with pytest.raises(
        admission.ProspectiveAdmissionRejected,
        match="DECLARED_CHRONOLOGY_CONTRADICTION",
    ):
        admit_unit(ctx, facts)


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"[]",
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":1.0}',
        b'{"x":9007199254740992}',
        b'{"x":' + b"[" * 30 + b"0" + b"]" * 30 + b"}",
        b" " * 65537,
    ],
)
def test_bounded_strict_receipt_input_refuses_before_any_admission(monkeypatch, raw):
    monkeypatch.setattr(
        admission,
        "admit_prospective_bundle",
        lambda *a, **k: pytest.fail("admission called"),
    )
    with pytest.raises(admission.ProspectiveAdmissionRejected, match="INVALID_RECEIPT"):
        admission.verify_prospective_receipt(
            raw,
            HANDOFF,
            CASE,
            FAKE,
            expected_handoff_manifest_sha256=PIN,
            expected_bundle_digest=BUNDLE,
            expected_run_id=RUN,
        )


METHOD_DRIFT = (
    [
        "descriptor." + p
        for p in [
            "schema_version",
            "execution_mode",
            "evidence_eligibility",
            "producer.name",
            "producer.version",
            "producer.adapter",
            "producer.adapter_version",
            "producer.native_schema_fingerprint",
            "digests.execution_fingerprint",
            "digests.source_spec_digest",
        ]
    ]
    + [
        "resolved_spec.target." + p
        for p in [
            "engine",
            "engine_version",
            "api",
            "model",
            "model_revision",
            "tokenizer_revision",
            "endpoint",
        ]
    ]
    + [
        "resolved_spec.execution." + p
        for p in [
            "mode",
            "producer_name",
            "producer_version",
            "adapter",
            "adapter_version",
            "max_runtime_seconds",
            "max_measured_requests",
        ]
    ]
    + [
        "resolved_spec.traffic." + p
        for p in ["kind", "concurrency", "warmup_requests", "measured_requests"]
    ]
    + [
        "resolved_spec.workload." + p
        for p in [
            "sha256",
            "prompt_content_policy",
            "requested_output_tokens",
            "temperature",
            "seed",
        ]
    ]
    + [
        "resolved_spec.measurement." + p
        for p in [
            "streaming",
            "ttft_definition",
            "choices_span_definition",
            "metric_definitions_version",
            "reducer_version",
        ]
    ]
    + [
        "resolved_spec.evidence." + p
        for p in [
            "native_output_sensitivity",
            "canonical_response_content",
            "include_request_plan",
        ]
    ]
    + [
        "managed_profile." + p
        for p in [
            "profile_id",
            "profile_sha256",
            "local_gpu_proof_schema_id",
            "local_gpu_proof_schema_sha256",
            "validator_version",
            "claims_assurance",
        ]
    ]
    + [
        "resolved_spec.experiment.id",
        "request_plan.traffic.concurrency",
        "execution.configured_traffic.warmup_requests",
    ]
)


@pytest.mark.parametrize("path", METHOD_DRIFT)
def test_unit_only_each_observed_methodology_drift_is_not_applicable(path):
    ctx = context()
    facts = unit_verified_facts(ctx)
    parts = path.split(".")
    item = getattr(facts, parts[0])
    for part in parts[1:-1]:
        item = item[part]
    if isinstance(item, SimpleNamespace):
        setattr(item, parts[-1], "different")
    else:
        old = item[parts[-1]]
        item[parts[-1]] = (
            not old
            if type(old) is bool
            else old + 1
            if type(old) is int
            else "different"
        )
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="P1_NOT_APPLICABLE"
    ) as error:
        admit_unit(ctx, facts)
    assert error.value.field_paths and len(error.value.field_paths) < 80
    assert all(len(p) < 160 for p in error.value.field_paths)


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "REQUEST_CHANGES"),
        ("agreement_acknowledged", False),
        ("contract_fingerprint", "0" * 64),
        ("confirmation_id", "cnf_" + "0" * 64),
        ("contract_version", "other"),
    ],
)
def test_rehashed_confirmation_still_requires_actual_affirmative_binding(
    tmp_path, field, value
):
    root = tmp_path / "handoff"
    shutil.copytree(HANDOFF, root)
    manifest = json.loads((root / "handoff-manifest.json").read_bytes())
    case = manifest["cases"][0]
    path = root / case["confirmation_artifact_path"]
    confirmation = json.loads(path.read_bytes())
    confirmation[field] = value
    raw = canonical_json_bytes(confirmation)
    path.write_bytes(raw)
    case["confirmation_record_sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    raw = canonical_json_bytes(manifest)
    (root / "handoff-manifest.json").write_bytes(raw)
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="CONTEXT_NOT_AUTHORIZED"
    ):
        admission.admit_prospective_bundle(
            root,
            CASE,
            FAKE,
            **public_kwargs(
                expected_handoff_manifest_sha256=hashlib.sha256(raw).hexdigest()
            ),
        )


def test_fifo_swap_between_scan_and_read_refuses_with_bounded_wait(tmp_path):
    root = tmp_path / "handoff"
    shutil.copytree(HANDOFF, root)
    code = """
import os,sys
from pathlib import Path
from exitspec import inferdrome_prospective_context as snapshots
root=Path(sys.argv[1]);pin=sys.argv[2]
original=snapshots._capture_file
swapped=False
def raced(reader,name):
 global swapped
 if not swapped and reader.root==root and name=='handoff-manifest.json':
  swapped=True
  (root/name).unlink();os.mkfifo(root/name)
 return original(reader,name)
snapshots._capture_file=raced
try:snapshots.capture_prospective_context(root,'native-p95-under-20ms',pin)
except ValueError:sys.exit(0 if swapped else 2)
sys.exit(3)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(root), PIN],
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def rehashed(payload):
    payload = copy.deepcopy(payload)
    payload.pop("receipt_id", None)
    payload["receipt_id"] = reporting.prospective_receipt_id(payload)
    return canonical_json_bytes(payload)


def unit_receipt_payload():
    ctx = context()
    facts = unit_verified_facts(ctx)
    return (
        ctx,
        facts,
        json.loads(reporting.serialize_prospective_receipt(admit_unit(ctx, facts))),
    )


@pytest.mark.parametrize(
    "field", list(reporting.InferdromeProspectiveReceiptV1.model_fields)
)
def test_every_receipt_field_is_bound_to_its_original_identity(field):
    _, _, payload = unit_receipt_payload()
    old = payload[field]
    payload[field] = (
        old + "changed"
        if type(old) is str
        else []
        if type(old) is dict
        else ["changed"]
    )
    with pytest.raises((ValueError, TypeError)):
        reporting.parse_prospective_receipt(canonical_json_bytes(payload))


@pytest.mark.parametrize(
    "field,value",
    [
        ("acceptance_verdict", "FAIL"),
        ("applicability_codes", ["TARGET_MODEL_MISMATCH"]),
        ("binding_mode", "EXTERNAL_RECEIPT_BINDING"),
        ("producer_contract_link", "ABSENT"),
        ("verifier_version", "2.0.0"),
        ("calculation_version", "exitspec.inferdrome-managed-importer.v1"),
        ("schema_version", "exitspec.inferdrome-managed-receipt.v2"),
    ],
)
def test_rehash_cannot_change_receipt_authority(field, value):
    _, _, payload = unit_receipt_payload()
    payload[field] = value
    with pytest.raises((ValueError, TypeError)):
        reporting.parse_prospective_receipt(rehashed(payload))


@pytest.mark.parametrize(
    "field,value",
    [
        ("hardware_attestation", "VERIFIED"),
        ("execution_attestation", "VERIFIED"),
        ("temporal_assurance", "AUTHENTICATED"),
        ("contract_preceded_measurement", True),
        ("contract_preceded_measurement", False),
        ("production_authorization", True),
        ("production_authorization", 0),
        ("claims_assurance", "AUTHENTICATED"),
        ("confirmation_identity_assurance", "AUTHENTICATED"),
    ],
)
def test_rehash_never_upgrades_assurance_or_coerces_booleans(field, value):
    _, _, payload = unit_receipt_payload()
    payload["assurance"][field] = value
    with pytest.raises((ValueError, TypeError)):
        reporting.parse_prospective_receipt(rehashed(payload))


@pytest.mark.parametrize(
    "field",
    [
        "bundle_digest",
        "handoff_manifest_sha256",
        "observed_request_plan_digest",
        "recalculation_sha256",
        "handoff_case.confirmation_record_sha256",
        "handoff_case.contract_confirmation_fingerprint",
        "handoff_case.confirmation_id",
    ],
)
def test_unit_only_receipt_reverification_binds_facts_not_just_self_hash(
    monkeypatch, field
):
    _ctx, facts, payload = unit_receipt_payload()
    parts = field.split(".")
    item = payload
    for part in parts[:-1]:
        item = item[part]
    old = item[parts[-1]]
    item[parts[-1]] = (
        "sha256:"
        if old.startswith("sha256:")
        else "cnf_"
        if old.startswith("cnf_")
        else ""
    ) + "0" * 64
    raw = rehashed(payload)
    reporting.parse_prospective_receipt(
        raw
    )  # Structurally self-consistent is insufficient.
    calls = []

    def unit_verifier(path, **kwargs):
        calls.append((path, kwargs))
        return facts

    monkeypatch.setattr(admission, "verify_inferdrome_bundle", unit_verifier)
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="RECEIPT_MISMATCH"
    ):
        admission.verify_prospective_receipt(
            raw,
            HANDOFF,
            CASE,
            FAKE,
            expected_handoff_manifest_sha256=PIN,
            expected_bundle_digest=BUNDLE,
            expected_run_id=RUN,
        )
    assert calls[0][1]["require_customer_eligible"] is True
    assert calls[0][1]["expected_bundle_digest"] == BUNDLE


def test_unit_only_reverification_and_distinct_audit_time_identity(monkeypatch):
    ctx, facts, payload = unit_receipt_payload()
    monkeypatch.setattr(admission, "verify_inferdrome_bundle", lambda *a, **k: facts)
    raw = canonical_json_bytes(payload)
    result = admission.verify_prospective_receipt(
        raw,
        HANDOFF,
        CASE,
        FAKE,
        expected_handoff_manifest_sha256=PIN,
        expected_bundle_digest=BUNDLE,
        expected_run_id=RUN,
    )
    assert reporting.serialize_prospective_receipt(result) == raw
    newer = admission._admit_verified(
        ctx, facts, RUN, BUNDLE, RECEIVED + timedelta(seconds=1)
    )
    assert newer.receipt_id != result.receipt_id
    assert result.received_at == RECEIVED
    # Audit time is intentionally caller-supplied: a later value is a new receipt,
    # never a mutation under the old identity or authenticated chronology.
    assert newer.assurance.temporal_assurance == "UNAVAILABLE"


@pytest.mark.parametrize(
    "field",
    [
        "attempted_count",
        "successful_count",
        "failed_count",
        "anomalous_count",
        "required_warmup_requests",
    ],
)
def test_nested_receipt_numbers_do_not_accept_boolean_coercion(field):
    _, _, payload = unit_receipt_payload()
    payload["population"][field] = False
    with pytest.raises(ValueError):
        reporting.parse_prospective_receipt(rehashed(payload))


@pytest.mark.parametrize(
    "kind",
    [
        "noncanonical",
        "oversized-string",
        "too-many-nodes",
        "deep-recursion",
        "extra-field",
    ],
)
def test_additional_canonical_and_structure_bounds(kind):
    _, _, payload = unit_receipt_payload()
    if kind == "noncanonical":
        raw = json.dumps(payload, indent=2).encode()
    elif kind == "oversized-string":
        raw = json.dumps({"x": "y" * 8193}).encode()
    elif kind == "too-many-nodes":
        raw = json.dumps({"x": [0] * 8192}).encode()
    elif kind == "deep-recursion":
        raw = b'{"x":' + b"[" * 1200 + b"0" + b"]" * 1200 + b"}"
    else:
        payload["unexpected"] = "field"
        raw = rehashed(payload)
    with pytest.raises(ValueError):
        reporting.parse_prospective_receipt(raw)


@pytest.mark.parametrize(
    "damage", ["changed", "missing", "extra", "symlink", "unsafe-root"]
)
def test_actual_public_verifier_negative_closure_without_eligibility_relabel(
    tmp_path, damage
):
    root = tmp_path / "bundle"
    shutil.copytree(FAKE, root)
    original = (root / "integrity/artifact-hashes.json").read_bytes()
    digest = (
        "sha256:"
        + hashlib.sha256(b"inferdrome:bundle-manifest-v1\0" + original).hexdigest()
    )
    run = json.loads((root / "bundle.json").read_bytes())["run_id"]
    if damage == "changed":
        (root / "bundle.json").write_bytes((root / "bundle.json").read_bytes() + b" ")
    elif damage == "missing":
        (root / "bundle.json").unlink()
    elif damage == "extra":
        (root / "extra").write_bytes(b"x")
    elif damage == "symlink":
        path = root / "bundle.json"
        path.unlink()
        path.symlink_to(FAKE / "bundle.json")
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    with pytest.raises(
        (InferdromeBundleRejected, admission.ProspectiveAdmissionRejected)
    ):
        admission.admit_prospective_bundle(
            HANDOFF,
            CASE,
            root,
            **public_kwargs(expected_bundle_digest=digest, expected_run_id=run),
        )


@pytest.mark.parametrize("low_count,expected", [(94, "FAIL"), (95, "PASS")])
def test_existing_nearest_rank_boundary_flows_to_unit_only_receipt(low_count, expected):
    from exitspec.inferdrome_bundle import _nearest_rank

    ctx = context()
    values = tuple([19_999_999] * low_count + [20_000_000] * (100 - low_count))
    p95 = _nearest_rank(values, 95)
    assert p95 == sorted(values)[94]
    assert (
        admit_unit(ctx, unit_verified_facts(ctx, p95=p95)).acceptance_verdict
        == expected
    )


@pytest.mark.parametrize("bound", ["file", "total", "directories"])
def test_context_resource_limits_refuse_before_parsing_or_bundle_work(
    tmp_path, monkeypatch, bound
):
    root = tmp_path / "handoff"
    shutil.copytree(HANDOFF, root)
    if bound == "directories":
        (root / "extra").mkdir()
    else:
        paths = list((root / "contracts").iterdir())
        for path in paths[: 1 if bound == "file" else 3]:
            with path.open("r+b") as output:
                output.truncate(8 * 1024 * 1024 + (1 if bound == "file" else 0))
    monkeypatch.setattr(
        admission,
        "verify_inferdrome_bundle",
        lambda *a, **k: pytest.fail("bundle verifier called"),
    )
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="CONTEXT_NOT_AUTHORIZED"
    ):
        admission.admit_prospective_bundle(root, CASE, FAKE, **public_kwargs())


@pytest.mark.parametrize(
    "stage", ["_methodology", "_ordered_plan", "prospective_receipt_id"]
)
def test_unit_only_injected_processing_faults_return_no_partial_receipt(
    monkeypatch, stage
):
    facts = unit_verified_facts(context())
    monkeypatch.setattr(admission, "verify_inferdrome_bundle", lambda *a, **k: facts)

    def broken(*args, **kwargs):
        raise ValueError("unit-only private detail must not escape")

    monkeypatch.setattr(admission, stage, broken)
    with pytest.raises(
        admission.ProspectiveAdmissionRejected, match="P1_NOT_APPLICABLE"
    ) as error:
        admission.admit_prospective_bundle(HANDOFF, CASE, FAKE, **public_kwargs())
    assert str(error.value) == "P1_NOT_APPLICABLE"
    assert error.value.field_paths == ("validated_facts",)


def test_exact_approved_receipt_field_mapping_is_implemented():
    # Explicit approved field inventory: no legacy requested plan digest or
    # inherited retrospective chronology/authority fields can be added silently.
    assert len(reporting.InferdromeProspectiveReceiptV1.model_fields) == 43
    assert len(reporting.ProspectiveEvidenceAssuranceV1.model_fields) == 12
    assert (
        "requested_request_plan_digest"
        not in reporting.InferdromeProspectiveReceiptV1.model_fields
    )
    assert admission.INFERDROME_VERIFIER_VERSION == "1.0.0"
