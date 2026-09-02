"""CLI contract tests for PR11 qualification checking."""

from __future__ import annotations

import json
from pathlib import Path

from exitspec.cli import (
    QUALIFICATION_EXIT_CURRENT_FAIL,
    QUALIFICATION_EXIT_CURRENT_PASS,
    QUALIFICATION_EXIT_INVALID,
    QUALIFICATION_EXIT_STALE,
    main,
)
from exitspec.qualification_scope import serialize_qualification_scope
from exitspec.serving_subject import serialize_serving_subject_manifest
from test_qualification_receipts import FIXED_TIME, _inputs, _issue
from exitspec.qualification_receipts import (
    serialize_inference_performance_qualification_receipt,
)


def _write_inputs(tmp_path: Path, monkeypatch, *, failed: int = 0):
    authority, _, _, _, _ = _inputs()
    receipt = _issue(monkeypatch, failed=failed)
    subject_path = tmp_path / "subject.json"
    scope_path = tmp_path / "scope.json"
    receipt_path = tmp_path / "receipt.json"
    subject_path.write_bytes(serialize_serving_subject_manifest(authority.subject))
    scope_path.write_bytes(serialize_qualification_scope(authority.scope))
    receipt_path.write_bytes(
        serialize_inference_performance_qualification_receipt(receipt)
    )
    return authority, subject_path, scope_path, receipt_path


def _run_check(subject: Path, scope: Path, receipt: Path, *, assessed_at=FIXED_TIME):
    return main(
        [
            "qualification",
            "check",
            "--subject",
            str(subject),
            "--scope",
            str(scope),
            "--receipt",
            str(receipt),
            "--assessed-at",
            assessed_at.isoformat().replace("+00:00", "Z"),
            "--json",
        ]
    )


def test_cli_reserves_zero_for_current_pass_and_emits_stable_json(
    tmp_path, monkeypatch, capsys
):
    _, subject, scope, receipt = _write_inputs(tmp_path, monkeypatch)
    assert _run_check(subject, scope, receipt) == QUALIFICATION_EXIT_CURRENT_PASS
    payload = json.loads(capsys.readouterr().out)
    assert payload["validity"] == "CURRENT"
    assert payload["verdict"] == "PASS"
    assert payload["reason"] == "CURRENT"


def test_cli_keeps_current_fail_nonzero(tmp_path, monkeypatch, capsys):
    _, subject, scope, receipt = _write_inputs(tmp_path, monkeypatch, failed=1)
    assert _run_check(subject, scope, receipt) == QUALIFICATION_EXIT_CURRENT_FAIL
    payload = json.loads(capsys.readouterr().out)
    assert payload["validity"] == "CURRENT"
    assert payload["verdict"] == "FAIL"


def test_cli_reports_stale_and_malformed_as_nonzero(tmp_path, monkeypatch, capsys):
    authority, subject, scope, receipt = _write_inputs(tmp_path, monkeypatch)
    subject_payload = authority.subject.model_dump(mode="json", exclude={"subject_digest"})
    subject_payload["model"]["revision"] = "fedcba9876543210"
    subject.write_text(json.dumps(subject_payload), encoding="utf-8")
    assert _run_check(subject, scope, receipt) == QUALIFICATION_EXIT_INVALID
    assert json.loads(capsys.readouterr().out)["reason"] == "INVALID_INPUT"

    subject.write_bytes(serialize_serving_subject_manifest(authority.subject))
    receipt.write_bytes(b'{"schema_version":"bad"}')
    assert _run_check(subject, scope, receipt) == QUALIFICATION_EXIT_INVALID
    assert json.loads(capsys.readouterr().out)["validity"] == "INVALID"

    # A valid drifted subject must be represented by a valid digest-bound file.
    from exitspec.serving_subject import create_serving_subject_manifest

    changed = create_serving_subject_manifest(subject_payload)
    subject.write_bytes(serialize_serving_subject_manifest(changed))
    receipt.write_bytes(
        serialize_inference_performance_qualification_receipt(_issue(monkeypatch))
    )
    assert _run_check(subject, scope, receipt) == QUALIFICATION_EXIT_STALE
    assert json.loads(capsys.readouterr().out)["validity"] == "STALE"
