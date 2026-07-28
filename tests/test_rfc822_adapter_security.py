from __future__ import annotations

import ast
import hashlib
import inspect
import json
import logging
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path

import pytest

import exitspec.adapters.rfc822 as rfc822
from exitspec.adapters.rfc822 import (
    Rfc822PreparationError,
    prepare_support_agent_email_fixture,
)
from exitspec.demo_data import support_agent_email_paths


def _assert_public_error_is_sanitized(
    error: Rfc822PreparationError,
    forbidden: tuple[str, ...],
) -> None:
    assert error.__context__ is None
    assert error.__cause__ is None
    assert error.__suppress_context__ is False
    traceback = error.__traceback__
    adapter_frames = []
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename == inspect.getfile(rfc822):
            adapter_frames.append(frame)
            assert frame.f_code.co_name == "prepare_support_agent_email_fixture"
            for value in frame.f_locals.values():
                assert not isinstance(value, (bytes, bytearray, memoryview, Message))
                if isinstance(value, str):
                    assert all(secret not in value for secret in forbidden)
        traceback = traceback.tb_next
    assert len(adapter_frames) == 1
    rendered = " ".join(
        [str(error), repr(error), repr(error.args), error.next_action]
    )
    assert all(secret not in rendered for secret in forbidden)


def test_public_facade_accepts_only_registry_case_and_observed_time() -> None:
    signature = inspect.signature(prepare_support_agent_email_fixture)
    assert tuple(signature.parameters) == (
        "resources",
        "fixture_case_id",
        "observed_at",
    )
    assert signature.parameters["observed_at"].kind is inspect.Parameter.KEYWORD_ONLY
    forbidden = {
        "bytes",
        "path",
        "url",
        "customer_terms",
        "oauth",
        "mailbox",
        "provider",
        "config",
    }
    assert not forbidden & set(signature.parameters)


def test_unknown_case_and_extra_arguments_fail_without_path_access() -> None:
    with support_agent_email_paths() as resources:
        with pytest.raises(Rfc822PreparationError) as unknown:
            prepare_support_agent_email_fixture(
                resources,
                "../../thread-root",
                observed_at=datetime.now(timezone.utc),
            )
        assert unknown.value.code == "source_not_approved"
        with pytest.raises(TypeError):
            prepare_support_agent_email_fixture(
                resources,
                "thread-root",
                observed_at=datetime.now(timezone.utc),
                raw_bytes=b"forbidden",
            )


def test_private_and_raw_values_do_not_escape_models_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    forbidden = (
        "support-poc-001@customer.example",
        "priya@customer.example",
        "demo-token-SYNTHETIC-0000",
        "64b188fac2f4a7e0f2eef1397099ce7ca081654a7c5f23fb5b04d1cf761810d8",
    )
    with support_agent_email_paths() as resources:
        with caplog.at_level(logging.DEBUG):
            prepared = prepare_support_agent_email_fixture(
                resources,
                "thread-root",
                observed_at=datetime(2026, 7, 27, 19, tzinfo=timezone.utc),
            )
    public_dump = json.dumps(
        prepared.prepared_envelope.model_dump(mode="json"),
        sort_keys=True,
    )
    rendered = " ".join(
        [
            repr(prepared),
            str(prepared),
            repr(prepared.approved_synthetic_fixture),
            str(prepared.approved_synthetic_fixture),
            public_dump,
            caplog.text,
        ]
    )
    for secret in forbidden:
        assert secret not in rendered
    assert "[EMAIL]" in public_dump
    assert "[SECRET]" in public_dump

    def assert_no_raw_buffers(value, seen: set[int]) -> None:
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        assert not isinstance(value, (bytes, bytearray, memoryview))
        if isinstance(value, dict):
            for item in value.values():
                assert_no_raw_buffers(item, seen)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                assert_no_raw_buffers(item, seen)
        elif hasattr(value, "__dict__"):
            for item in vars(value).values():
                assert_no_raw_buffers(item, seen)

    assert_no_raw_buffers(prepared, set())


def test_error_is_content_free_when_fixture_digest_changes(
    tmp_path: Path,
) -> None:
    with support_agent_email_paths() as packaged:
        for path in packaged.root.iterdir():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    resources = type(packaged).from_root(tmp_path)
    fixture = resources.fixture_for("thread-root")
    fixture.write_bytes(fixture.read_bytes() + b"private@example.test")

    with pytest.raises(Rfc822PreparationError) as caught:
        prepare_support_agent_email_fixture(
            resources,
            "thread-root",
            observed_at=datetime.now(timezone.utc),
        )
    rendered = " ".join(
        [str(caught.value), repr(caught.value), repr(caught.value.args)]
    )
    assert caught.value.code == "fixture_digest_mismatch"
    assert "private@example.test" not in rendered
    assert caught.value.__cause__ is None


def test_post_loader_manifest_fixture_and_customer_term_mutation_fails_closed(
    tmp_path: Path,
) -> None:
    with support_agent_email_paths() as packaged:
        for path in packaged.root.iterdir():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    resources = type(packaged).from_root(tmp_path)
    fixture_path = resources.fixture_for("thread-root")
    mutated_fixture = fixture_path.read_bytes().replace(
        b"95% of 200 cases",
        b"99% of 200 cases",
    )
    fixture_path.write_bytes(mutated_fixture)

    manifest = json.loads(resources.manifest.read_text(encoding="utf-8"))
    record = next(
        item
        for item in manifest["fixture_set"]["fixtures"]
        if item["case_id"] == "thread-root"
    )
    record["sha256"] = hashlib.sha256(mutated_fixture).hexdigest()
    record["raw_bytes"] = len(mutated_fixture)
    record["customer_terms"] = []
    resources.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(Rfc822PreparationError) as caught:
        prepare_support_agent_email_fixture(
            resources,
            "thread-root",
            observed_at=datetime.now(timezone.utc),
        )
    assert caught.value.code == "source_not_approved"
    _assert_public_error_is_sanitized(
        caught.value,
        (
            "ExampleCo",
            "99% of 200 cases",
            "priya@customer.example",
            mutated_fixture.decode("utf-8"),
        ),
    )


def test_operation_time_symlink_swap_is_not_authorized(tmp_path: Path) -> None:
    with support_agent_email_paths() as packaged:
        for path in packaged.root.iterdir():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    resources = type(packaged).from_root(tmp_path)
    selected = resources.fixture_for("thread-root")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.eml"
    outside.write_bytes(selected.read_bytes())
    selected.unlink()
    selected.symlink_to(outside)
    try:
        with pytest.raises(Rfc822PreparationError) as caught:
            prepare_support_agent_email_fixture(
                resources,
                "thread-root",
                observed_at=datetime.now(timezone.utc),
            )
        assert caught.value.code == "source_not_approved"
        _assert_public_error_is_sanitized(caught.value, ())
    finally:
        outside.unlink(missing_ok=True)


def test_public_sender_error_has_no_sensitive_traceback_state() -> None:
    with support_agent_email_paths() as resources:
        with pytest.raises(Rfc822PreparationError) as caught:
            prepare_support_agent_email_fixture(
                resources,
                "sender-ambiguous",
                observed_at=datetime.now(timezone.utc),
            )
    assert caught.value.code == "sender_ambiguous"
    _assert_public_error_is_sanitized(
        caught.value,
        (
            "first@customer.example",
            "second@customer.example",
            "First Sender",
            "Second Sender",
        ),
    )


def test_injected_redactor_error_has_no_context_or_sensitive_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_marker = "RAW-REDACTOR-SECRET-priya@customer.example"

    def fail_redaction(value, customer_terms):
        raise RuntimeError(f"{raw_marker}:{value}")

    monkeypatch.setattr(rfc822, "redact_rfc822_value", fail_redaction)
    with support_agent_email_paths() as resources:
        with pytest.raises(Rfc822PreparationError) as caught:
            prepare_support_agent_email_fixture(
                resources,
                "thread-root",
                observed_at=datetime.now(timezone.utc),
            )
    assert caught.value.code == "redaction_failed"
    _assert_public_error_is_sanitized(
        caught.value,
        (raw_marker, "priya@customer.example", "ExampleCo"),
    )


def test_manifest_and_selected_fixture_are_each_read_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with support_agent_email_paths() as resources:
        original = rfc822._read_bounded
        reads = {resources.manifest: 0, resources.fixture_for("thread-root"): 0}
        limits: dict[Path, int] = {}

        def counted(path: Path, maximum_bytes: int) -> bytes:
            if path in reads:
                reads[path] += 1
                limits[path] = maximum_bytes
            return original(path, maximum_bytes)

        monkeypatch.setattr(rfc822, "_read_bounded", counted)
        prepare_support_agent_email_fixture(
            resources,
            "thread-root",
            observed_at=datetime.now(timezone.utc),
        )
    assert set(reads.values()) == {1}
    assert limits[resources.manifest] == rfc822._PINNED_MANIFEST_BYTES
    assert (
        limits[resources.fixture_for("thread-root")]
        == rfc822.RAW_MESSAGE_BYTES_MAX
    )


def test_manifest_and_fixture_reads_stop_at_one_byte_past_their_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with support_agent_email_paths() as packaged:
        for path in packaged.root.iterdir():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    resources = type(packaged).from_root(tmp_path)
    manifest = resources.manifest
    manifest.write_bytes(
        manifest.read_bytes() + b"x" * 1_000
    )
    observed_lengths: list[tuple[Path, int]] = []
    original = rfc822._read_bounded

    def observed(path: Path, maximum_bytes: int) -> bytes:
        value = original(path, maximum_bytes)
        observed_lengths.append((path, len(value)))
        return value

    monkeypatch.setattr(rfc822, "_read_bounded", observed)
    with pytest.raises(Rfc822PreparationError) as manifest_error:
        prepare_support_agent_email_fixture(
            resources,
            "thread-root",
            observed_at=datetime.now(timezone.utc),
        )
    assert manifest_error.value.code == "source_not_approved"
    assert observed_lengths == [
        (manifest, rfc822._PINNED_MANIFEST_BYTES + 1)
    ]

    manifest.write_bytes(
        resources.manifest.read_bytes()[: rfc822._PINNED_MANIFEST_BYTES]
    )
    fixture = resources.fixture_for("thread-root")
    fixture.write_bytes(b"x" * (rfc822.RAW_MESSAGE_BYTES_MAX + 1_000))
    observed_lengths.clear()
    with pytest.raises(Rfc822PreparationError) as fixture_error:
        prepare_support_agent_email_fixture(
            resources,
            "thread-root",
            observed_at=datetime.now(timezone.utc),
        )
    assert fixture_error.value.code == "raw_message_too_large"
    assert observed_lengths == [
        (manifest, rfc822._PINNED_MANIFEST_BYTES),
        (fixture, rfc822.RAW_MESSAGE_BYTES_MAX + 1),
    ]


def test_adapter_has_no_network_provider_or_mailbox_imports() -> None:
    root = Path(inspect.getfile(rfc822)).parent
    files = (
        root / "rfc822.py",
        root / "rfc822_policy.py",
        root / "rfc822_candidates.py",
    )
    forbidden_roots = {
        "aiohttp",
        "fireworks",
        "http",
        "httpx",
        "imaplib",
        "oauthlib",
        "requests",
        "smtplib",
        "socket",
        "urllib",
    }
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & forbidden_roots


def test_candidate_module_does_not_read_manifest_or_fixture_answers() -> None:
    source = Path(inspect.getfile(rfc822.extract_candidate_matches)).read_text(
        encoding="utf-8"
    )
    assert "expected_candidates" not in source
    assert "wave-2-acceptance" not in source
    assert "json" not in source
