"""CLI bridge regression tests; positive persisted receipts are UNIT-ONLY artifacts.

Positive tests substitute in-memory verifier facts and a nonexecuting transport
seam. No CUSTOMER_ELIGIBLE bundle is written, and these are not qualification.
The public synthetic rejection test uses the real child stub and verifier.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import threading
from dataclasses import replace
from decimal import Decimal

import pytest

from exitspec import inferdrome_dispatch as transport
from exitspec import inferdrome_prospective_admission as admission
from exitspec import inferdrome_receipt_bridge as bridge
from exitspec.canonical import canonical_json_bytes
from exitspec.inferdrome_prospective_context import capture_prospective_context
from tests.test_inferdrome_dispatch import setup
from tests.test_inferdrome_prospective_admission import unit_verified_facts


@pytest.fixture
def unit_only_operation(tmp_path, monkeypatch):
    """Real local storage + accepted admission logic; IN-MEMORY evidence stand-in."""
    root, pin, _executable, runs = setup(tmp_path, "fail")
    plan = transport._load(root, pin)
    ctx = capture_prospective_context(
        root / "handoff", plan.case_id, plan.manifest_sha256
    )
    facts = unit_verified_facts(ctx)
    facts.descriptor["run_id"] = plan.run_id
    facts.request_plan["run_id"] = plan.run_id
    facts.request_plan["producer_request_id_prefix"] = plan.run_id + "-"
    for i, request in enumerate(facts.request_plan["requests"]):
        request["producer_request_id"] = f"{plan.run_id}-{i}"
    facts.descriptor["digests"]["request_plan_digest"] = (
        "sha256:"
        + hashlib.sha256(
            b"inferdrome:request-plan-v1\0" + canonical_json_bytes(facts.request_plan)
        ).hexdigest()
    )
    returned = {
        "run_id": plan.run_id,
        "workspace_path": str(runs / plan.run_id),
        "bundle_path": str(runs / plan.run_id / "bundle"),
        "bundle_digest": facts.bundle_digest,
        "evidence_eligibility": "CUSTOMER_ELIGIBLE",
        "integrity_status": "VALID",
    }
    calls = []
    holder = [facts]

    def execute(*args, **kwargs):
        calls.append("unit-only-no-process")
        return "RETURNED", 0, canonical_json_bytes(returned), b""

    monkeypatch.setattr(transport, "_execute", execute)
    monkeypatch.setattr(
        transport, "verify_inferdrome_bundle", lambda *a, **k: holder[0]
    )
    monkeypatch.setattr(
        admission, "verify_inferdrome_bundle", lambda *a, **k: holder[0]
    )
    return root, pin, holder, calls


def read(root, name):
    return json.loads((root / name).read_bytes())


def assert_saved(result, verdict="PASS"):
    assert result["bridge_status"] == "RECEIPT_SAVED"
    assert result["transport_status"] == "AWAITING_ADMISSION"
    assert result["receipt_saved"] is True
    assert result["acceptance_verdict"] == verdict
    assert result["shipping_authorized"] is False


def test_unit_only_one_command_saves_canonical_receipt_and_repeat_never_dispatches(
    unit_only_operation,
):
    root, pin, _, calls = unit_only_operation
    result = bridge.run_operation(root, pin)
    assert_saved(result)
    original = {name: (root / name).read_bytes() for name in bridge.FILES}
    inodes = {name: (root / name).stat().st_ino for name in bridge.FILES}
    again = bridge.run_operation(root, pin)
    assert_saved(again)
    assert again["receipt_id"] == result["receipt_id"]
    assert result["producer_dispatch_requested"] is True
    assert again["producer_dispatch_requested"] is False
    assert calls == ["unit-only-no-process"]
    assert original == {name: (root / name).read_bytes() for name in bridge.FILES}
    assert inodes == {name: (root / name).stat().st_ino for name in bridge.FILES}
    receipt = read(root, bridge.RECEIPT)
    assert receipt["received_at"] == read(root, bridge.INTENT)["received_at"]
    assert receipt["receipt_id"] == result["receipt_id"]
    assert (root / bridge.RECEIPT).stat().st_mode & 0o777 == 0o600
    assert all(
        event["acceptance_verdict"] is None
        for event in transport.status(root, pin)["history"]
    )
    assert bridge.exit_code(result) == 0


@pytest.mark.parametrize(
    "p95,failed,verdict,code",
    [
        (20_000_000, 0, "FAIL", 3),
        (None, 0, "NOT_PROVEN", 4),
        (None, 1, "FAIL", 3),
    ],
)
def test_unit_only_saved_outcome_separates_numerical_verdict(
    unit_only_operation, p95, failed, verdict, code
):
    root, pin, holder, _ = unit_only_operation
    holder[0] = replace(
        holder[0],
        recalculated=replace(
            holder[0].recalculated,
            p95_ttft_ns=p95,
            failed_count=failed,
            successful_count=100 - failed,
            error_rate=Decimal(failed) / 100,
        ),
    )
    result = bridge.run_operation(root, pin)
    assert_saved(result, verdict)
    assert bridge.exit_code(result) == code


class Crash(BaseException):
    pass


@pytest.mark.parametrize("stage", bridge.FILES)
def test_unit_only_crash_after_each_atomic_publication_reconciles_without_execution(
    unit_only_operation, monkeypatch, stage
):
    root, pin, _, calls = unit_only_operation
    publish = bridge._OperationFiles.publish

    def crash(self, name, raw):
        publish(self, name, raw)
        if name == stage:
            raise Crash()

    monkeypatch.setattr(bridge._OperationFiles, "publish", crash)
    with pytest.raises(Crash):
        bridge.run_operation(root, pin)
    intent = (root / bridge.INTENT).read_bytes()
    monkeypatch.setattr(bridge._OperationFiles, "publish", publish)
    result = bridge.run_operation(root, pin)
    assert_saved(result)
    assert calls == ["unit-only-no-process"]
    assert (root / bridge.INTENT).read_bytes() == intent
    assert result["producer_dispatch_requested"] is False


@pytest.mark.parametrize("missing", [bridge.INTENT, bridge.RECEIPT])
def test_unit_only_missing_success_artifact_never_reports_saved_or_recreates(
    unit_only_operation, missing
):
    root, pin, _, calls = unit_only_operation
    assert_saved(bridge.run_operation(root, pin))
    (root / missing).unlink()
    result = bridge.run_operation(root, pin)
    assert result["bridge_status"] == "REFUSED" and not result["receipt_saved"]
    assert not (root / missing).exists()
    assert len(calls) == 1


@pytest.mark.parametrize("name", bridge.FILES)
def test_unit_only_corrupt_record_is_refused_without_replacement(
    unit_only_operation, name
):
    root, pin, _, calls = unit_only_operation
    assert_saved(bridge.run_operation(root, pin))
    bad = b'{"truncated":'
    (root / name).write_bytes(bad)
    result = bridge.run_operation(root, pin)
    assert result["bridge_status"] == "REFUSED"
    assert result["acceptance_verdict"] is None
    assert (root / name).read_bytes() == bad
    assert len(calls) == 1


def test_unit_only_changed_verified_measurements_refuse_self_retained_receipt(
    unit_only_operation,
):
    root, pin, holder, calls = unit_only_operation
    assert_saved(bridge.run_operation(root, pin))
    holder[0] = replace(
        holder[0], recalculated=replace(holder[0].recalculated, p95_ttft_ns=19_999_999)
    )
    result = bridge.run_operation(root, pin)
    assert result["reason"] == "RECEIPT_MISMATCH"
    assert not result["receipt_saved"] and result["acceptance_verdict"] is None
    assert len(calls) == 1


@pytest.mark.parametrize("stage", bridge.FILES)
def test_unit_only_save_failure_never_claims_saved_and_can_reconcile(
    unit_only_operation, monkeypatch, stage
):
    root, pin, _, calls = unit_only_operation
    publish = bridge._OperationFiles.publish

    def fail(self, name, raw):
        if name == stage:
            raise OSError("unit-only storage failure")
        publish(self, name, raw)

    monkeypatch.setattr(bridge._OperationFiles, "publish", fail)
    result = bridge.run_operation(root, pin)
    assert not result["receipt_saved"]
    assert result["bridge_status"] == "REFUSED"
    assert len(calls) == 1
    monkeypatch.setattr(bridge._OperationFiles, "publish", publish)
    assert_saved(bridge.run_operation(root, pin))
    assert len(calls) == 1


def test_unit_only_fsync_failure_after_link_never_reports_durable_success(
    unit_only_operation, monkeypatch
):
    root, pin, _, calls = unit_only_operation
    transport.dispatch(root, pin)
    fsync = os.fsync
    count = 0

    def fail(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("unit-only directory durability failure")
        return fsync(fd)

    monkeypatch.setattr(bridge.os, "fsync", fail)
    result = bridge.run_operation(root, pin)
    assert not result["receipt_saved"]
    assert (root / bridge.INTENT).exists()
    monkeypatch.setattr(bridge.os, "fsync", fsync)
    assert_saved(bridge.run_operation(root, pin))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", "oversize", "writable"]
)
@pytest.mark.parametrize(
    "name", [bridge.INTENT, bridge.RECEIPT, bridge.RESULT, "dispatch.lock"]
)
def test_unsafe_storage_is_bounded_and_never_dispatches(
    unit_only_operation, tmp_path, kind, name
):
    root, pin, _, calls = unit_only_operation
    target = root / name
    if target.exists():
        target.unlink()
    other = tmp_path / "untouched"
    other.write_bytes(b"sentinel")
    if kind == "symlink":
        target.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, target)
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.write_bytes(b"x" * (65537 if kind == "oversize" else 1))
        if kind == "writable":
            target.chmod(0o666)
    result = bridge.run_operation(root, pin)
    assert result["bridge_status"] == "REFUSED" and not result["receipt_saved"]
    assert calls == [] and other.read_bytes() == b"sentinel"


def test_unit_only_atomic_publication_never_clobbers_racing_destination(
    unit_only_operation, monkeypatch, tmp_path
):
    root, pin, _, _ = unit_only_operation
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"untouched")
    link = os.link

    def raced(src, dst, **kwargs):
        if dst == bridge.RECEIPT:
            (root / bridge.RECEIPT).symlink_to(sentinel)
        return link(src, dst, **kwargs)

    monkeypatch.setattr(bridge.os, "link", raced)
    result = bridge.run_operation(root, pin)
    assert not result["receipt_saved"]
    assert sentinel.read_bytes() == b"untouched"
    assert (root / bridge.RECEIPT).is_symlink()
    assert not (root / bridge.RESULT).exists()


def test_unit_only_cancellation_after_dispatch_can_resume_receipt_only(
    unit_only_operation,
):
    root, pin, _, calls = unit_only_operation
    transport.dispatch(root, pin)
    stop = threading.Event()
    stop.set()
    result = bridge.run_operation(root, pin, cancelled=stop)
    assert result["reason"] == "CANCELLED"
    assert not any((root / name).exists() for name in bridge.FILES)
    assert_saved(bridge.run_operation(root, pin))
    assert len(calls) == 1


def test_claimed_awaiting_record_cannot_bypass_actual_synthetic_verifier(tmp_path):
    # A deliberately untrusted transport journal claim, not an eligible bundle.
    from tests.test_inferdrome_dispatch import FAKE

    root, pin, _, runs = setup(tmp_path, "fail")
    plan = transport._load(root, pin)
    bundle = runs / plan.run_id / "bundle"
    shutil.copytree(FAKE, bundle)
    manifest = (bundle / "integrity/artifact-hashes.json").read_bytes()
    digest = (
        "sha256:"
        + hashlib.sha256(b"inferdrome:bundle-manifest-v1\0" + manifest).hexdigest()
    )
    transport._event(root, pin, "RUNNING")
    transport._event(
        root,
        pin,
        "AWAITING_ADMISSION",
        producer_result={
            "run_id": plan.run_id,
            "workspace_path": str(bundle.parent),
            "bundle_path": str(bundle),
            "bundle_digest": digest,
            "integrity_status": "VALID",
            "evidence_eligibility": "CUSTOMER_ELIGIBLE",
        },
    )
    before = {
        str(p.relative_to(bundle)): p.read_bytes()
        for p in bundle.rglob("*")
        if p.is_file()
    }
    result = bridge.run_operation(root, pin)
    assert result["bridge_status"] == "ADMISSION_REJECTED"
    assert result["reason"] == "EVIDENCE_INELIGIBLE"
    assert result["acceptance_verdict"] is None and not result["receipt_saved"]
    assert not (root / bridge.RECEIPT).exists()
    saved_refusal = (root / bridge.RESULT).read_bytes()
    assert bridge.run_operation(root, pin)["bridge_status"] == "ADMISSION_REJECTED"
    assert (root / bridge.RESULT).read_bytes() == saved_refusal
    assert before == {
        str(p.relative_to(bundle)): p.read_bytes()
        for p in bundle.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "field",
    [
        "plan_sha256",
        "transport_record_sha256",
        "run_id",
        "case_id",
        "handoff_manifest_sha256",
        "bundle_digest",
        "producer_contract_link",
        "received_at",
    ],
)
def test_unit_only_intent_pin_or_time_substitution_is_refused(
    unit_only_operation, field
):
    root, pin, _, calls = unit_only_operation
    assert_saved(bridge.run_operation(root, pin))
    value = read(root, bridge.INTENT)
    value[field] = "different"
    raw = canonical_json_bytes(value)
    (root / bridge.INTENT).write_bytes(raw)
    result = bridge.run_operation(root, pin)
    assert not result["receipt_saved"] and result["acceptance_verdict"] is None
    assert (root / bridge.INTENT).read_bytes() == raw and len(calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("receipt_id", "ipr1_" + "0" * 64),
        ("receipt_sha256", "0" * 64),
        ("receipt_bytes", False),
        ("acceptance_verdict", "FAIL"),
        ("shipping_authorized", True),
        ("bridge_status", "ADMISSION_REJECTED"),
    ],
)
def test_unit_only_terminal_record_substitution_is_refused(
    unit_only_operation, field, value
):
    root, pin, _, _ = unit_only_operation
    assert_saved(bridge.run_operation(root, pin))
    changed = read(root, bridge.RESULT)
    changed[field] = value
    raw = canonical_json_bytes(changed)
    (root / bridge.RESULT).write_bytes(raw)
    result = bridge.run_operation(root, pin)
    assert result["bridge_status"] == "REFUSED" and not result["receipt_saved"]
    assert (root / bridge.RESULT).read_bytes() == raw


def test_unit_only_final_durability_failure_does_not_report_saved(
    unit_only_operation, monkeypatch
):
    root, pin, _, calls = unit_only_operation
    barrier = bridge._OperationFiles.barrier

    def fail(self, expected):
        if bridge.RESULT in expected and bridge.RECEIPT in expected:
            raise OSError("unit-only final barrier failure")
        return barrier(self, expected)

    monkeypatch.setattr(bridge._OperationFiles, "barrier", fail)
    result = bridge.run_operation(root, pin)
    assert result["bridge_status"] == "REFUSED" and not result["receipt_saved"]
    assert (root / bridge.RESULT).exists()  # Visible is not a durability success.
    monkeypatch.setattr(bridge._OperationFiles, "barrier", barrier)
    assert_saved(bridge.run_operation(root, pin))
    assert len(calls) == 1


def test_unit_only_interrupted_link_pair_is_refused_without_cleanup_or_rerun(
    unit_only_operation,
    monkeypatch,
):
    root, pin, _, calls = unit_only_operation
    publish = bridge._OperationFiles.publish

    def stop(self, name, raw):
        publish(self, name, raw)
        if name == bridge.RECEIPT:
            raise Crash()

    monkeypatch.setattr(bridge._OperationFiles, "publish", stop)
    with pytest.raises(Crash):
        bridge.run_operation(root, pin)
    orphan = root / ".receipt-unit-only-orphan"
    os.link(root / bridge.RECEIPT, orphan)
    monkeypatch.setattr(bridge._OperationFiles, "publish", publish)
    result = bridge.run_operation(root, pin)
    assert result["reason"] == "UNSAFE_STORAGE_FILE" and not result["receipt_saved"]
    assert orphan.exists() and (root / bridge.RECEIPT).stat().st_nlink == 2
    assert not (root / bridge.RESULT).exists() and len(calls) == 1


def test_unit_only_operation_change_during_admission_never_reports_saved(
    unit_only_operation,
    monkeypatch,
):
    root, pin, _, _ = unit_only_operation
    verify = bridge.verify_prospective_receipt

    def changed(*args, **kwargs):
        receipt = verify(*args, **kwargs)
        event = root / "event-2.json"
        payload = read(root, event.name)
        payload["changed_during_admission"] = True
        event.write_bytes(canonical_json_bytes(payload))
        return receipt

    monkeypatch.setattr(bridge, "verify_prospective_receipt", changed)
    result = bridge.run_operation(root, pin)
    assert result["bridge_status"] == "REFUSED" and not result["receipt_saved"]
    assert not (root / bridge.RECEIPT).exists()


def test_unit_only_replaced_operation_directory_does_not_receive_publication(
    unit_only_operation,
    monkeypatch,
):
    root, pin, _, calls = unit_only_operation
    publish = bridge._OperationFiles.publish
    retained = root.with_name("unit-only-retained-root")

    def replaced(self, name, raw):
        root.rename(retained)
        root.mkdir(mode=0o700)
        publish(self, name, raw)

    monkeypatch.setattr(bridge._OperationFiles, "publish", replaced)
    result = bridge.run_operation(root, pin)
    assert not result["receipt_saved"] and len(calls) == 1
    assert list(root.iterdir()) == []
    assert (retained / "plan.json").exists()


def test_storage_entry_bound_refuses_before_execution(unit_only_operation):
    root, pin, _, calls = unit_only_operation
    for i in range(33):
        (root / f"unexpected-{i}").touch()
    result = bridge.run_operation(root, pin)
    assert result["reason"] == "STORAGE_ENTRY_LIMIT"
    assert calls == []


@pytest.mark.parametrize(
    "state", sorted(transport.TERMINAL - {"AWAITING_ADMISSION"}) + ["RUNNING"]
)
def test_terminal_or_unclosed_transport_is_never_automatically_retried(
    unit_only_operation, state
):
    root, pin, _, calls = unit_only_operation
    transport._event(root, pin, state)
    result = bridge.run_operation(root, pin)
    assert (
        result["transport_status"] == state and result["bridge_status"] == "NO_RECEIPT"
    )
    assert not result["receipt_saved"] and bridge.exit_code(result) == 2
    assert calls == []


def test_unit_only_concurrent_finalization_has_one_writer_and_no_second_execution(
    unit_only_operation,
    monkeypatch,
):
    root, pin, _, calls = unit_only_operation
    transport.dispatch(root, pin)
    original = bridge.admit_prospective_bundle
    entered, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(bridge, "admit_prospective_bundle", held)
    outputs = []
    worker = threading.Thread(
        target=lambda: outputs.append(bridge.run_operation(root, pin))
    )
    worker.start()
    try:
        assert entered.wait(5)
        busy = bridge.run_operation(root, pin)
        assert busy["reason"] == "DISPATCH_BUSY" and not busy["receipt_saved"]
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert_saved(outputs[0])
    assert_saved(bridge.run_operation(root, pin))
    assert len(calls) == 1


def test_actual_public_bridge_rejects_synthetic_and_repeat_never_dispatches(tmp_path):
    root, pin, _, runs = setup(tmp_path, "synthetic")
    result = bridge.run_operation(root, pin)
    assert result["transport_status"] == "INGESTION_REJECTED"
    assert result["reason"] == "EVIDENCE_INELIGIBLE"
    assert not result["receipt_saved"]
    history = copy.deepcopy(transport.status(root, pin)["history"])
    children = sorted(p.name for p in runs.iterdir())
    again = bridge.run_operation(root, pin)
    assert again["producer_dispatch_requested"] is False
    assert transport.status(root, pin)["history"] == history
    assert sorted(p.name for p in runs.iterdir()) == children
    assert not any((root / name).exists() for name in bridge.FILES)


@pytest.mark.parametrize(
    "verdict,code", [("PASS", 0), ("FAIL", 3), ("NOT_PROVEN", 4), (None, 2)]
)
def test_unit_only_cli_wiring_preserves_exit_and_non_authorizing_output(
    monkeypatch, capsys, verdict, code
):
    from exitspec.cli import main

    value = {
        "bridge_status": "RECEIPT_SAVED" if verdict else "REFUSED",
        "acceptance_verdict": verdict,
        "shipping_authorized": False,
        "receipt_saved": verdict is not None,
    }
    monkeypatch.setattr(bridge, "run_operation", lambda *a, **k: value)
    result = main(
        [
            "inferdrome-handoff",
            "run",
            "--operation",
            "/unit-only",
            "--plan-sha256",
            "0" * 64,
        ]
    )
    assert result == code
    assert json.loads(capsys.readouterr().out) == value


@pytest.mark.parametrize("option", ["--bundle-path", "--receipt-path", "--case"])
def test_cli_refuses_unpinned_override_arguments(monkeypatch, option):
    from exitspec.cli import main

    monkeypatch.setattr(
        bridge, "run_operation", lambda *a, **k: pytest.fail("bridge invoked")
    )
    with pytest.raises(SystemExit) as error:
        main(
            [
                "inferdrome-handoff",
                "run",
                "--operation",
                "/unit-only",
                "--plan-sha256",
                "0" * 64,
                option,
                "/untrusted",
            ]
        )
    assert error.value.code == 2


@pytest.mark.parametrize(
    "function", ["serialize_prospective_receipt", "verify_prospective_receipt"]
)
def test_unit_only_representation_or_reverification_failure_cannot_publish(
    unit_only_operation, monkeypatch, function
):
    root, pin, _, calls = unit_only_operation

    def broken(*args, **kwargs):
        raise ValueError("unit-only processing fault")

    monkeypatch.setattr(bridge, function, broken)
    result = bridge.run_operation(root, pin)
    assert not result["receipt_saved"] and result["bridge_status"] == "REFUSED"
    assert not (root / bridge.RECEIPT).exists() and not (root / bridge.RESULT).exists()
    assert len(calls) == 1


def test_unit_only_valid_but_changed_intent_time_cannot_rebind_receipt(
    unit_only_operation,
):
    root, pin, _, _ = unit_only_operation
    assert_saved(bridge.run_operation(root, pin))
    value = read(root, bridge.INTENT)
    value["received_at"] = "2099-01-01T00:00:00.000000Z"
    (root / bridge.INTENT).write_bytes(canonical_json_bytes(value))
    result = bridge.run_operation(root, pin)
    assert result["reason"] == "RECEIPT_BINDING_MISMATCH"
    assert not result["receipt_saved"]


def test_unit_only_fifo_swap_at_storage_open_is_nonblocking(
    unit_only_operation, monkeypatch
):
    root, pin, _, _ = unit_only_operation
    assert_saved(bridge.run_operation(root, pin))
    original = os.open
    swapped = False

    def raced(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == bridge.RECEIPT and not swapped:
            assert flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW
            swapped = True
            (root / bridge.RECEIPT).unlink()
            os.mkfifo(root / bridge.RECEIPT)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(bridge.os, "open", raced)
    result = bridge.run_operation(root, pin)
    assert swapped and result["reason"] == "UNSAFE_STORAGE_FILE"
    assert not result["receipt_saved"]


def test_unit_only_cancellation_during_admission_leaves_only_resumable_intent(
    unit_only_operation, monkeypatch
):
    root, pin, _, calls = unit_only_operation
    original = bridge.admit_prospective_bundle
    cancelled = threading.Event()

    def cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        cancelled.set()
        return result

    monkeypatch.setattr(bridge, "admit_prospective_bundle", cancel)
    result = bridge.run_operation(root, pin, cancelled=cancelled)
    assert result["reason"] == "CANCELLED" and not result["receipt_saved"]
    assert (root / bridge.INTENT).exists()
    assert not (root / bridge.RECEIPT).exists() and not (root / bridge.RESULT).exists()
    monkeypatch.setattr(bridge, "admit_prospective_bundle", original)
    assert_saved(bridge.run_operation(root, pin))
    assert len(calls) == 1


def test_unit_only_cancel_after_receipt_barrier_leaves_reconcilable_receipt(
    unit_only_operation, monkeypatch
):
    root, pin, _, calls = unit_only_operation
    stop = threading.Event()
    barrier = bridge._OperationFiles.barrier

    def cancel(self, expected):
        barrier(self, expected)
        if bridge.RECEIPT in expected and bridge.RESULT not in expected:
            stop.set()

    monkeypatch.setattr(bridge._OperationFiles, "barrier", cancel)
    result = bridge.run_operation(root, pin, cancelled=stop)
    assert result["reason"] == "CANCELLED" and not result["receipt_saved"]
    assert (root / bridge.RECEIPT).exists()
    assert not (root / bridge.RESULT).exists()
    monkeypatch.setattr(bridge._OperationFiles, "barrier", barrier)
    assert_saved(bridge.run_operation(root, pin))
    assert len(calls) == 1
