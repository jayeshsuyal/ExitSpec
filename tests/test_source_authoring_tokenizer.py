"""Deterministic accounting boundaries; real vocabulary checks are external."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from exitspec import source_authoring_tokenizer as tokens
from tests.test_source_authoring_live_worker import body


def test_exact_chat_prefix_counts_system_schema_and_source_once_without_extra_eos():
    import json
    raw = body("Exact synthetic requirement.")
    messages = json.loads(raw)["messages"]
    rendered = tokens._render(raw, (tokens.BOS, tokens.USER, tokens.ASSISTANT, tokens.NO_THINKING))
    assert rendered == (tokens.BOS + messages[0]["content"] + tokens.USER
                        + messages[1]["content"] + tokens.ASSISTANT + tokens.NO_THINKING)
    assert rendered.count(tokens.BOS) == 1 and "<｜end▁of▁sentence｜>" not in rendered
    assert rendered.count("Exact synthetic requirement.") == 1
    assert rendered.count("Return JSON matching this schema:") == 1


@pytest.mark.parametrize("source", ["Café 中文 👋", "<section>ordinary markup</section>",
                                      "A value of 123456789 and punctuation — !?", "line one\nline two\tend"])
def test_ordinary_source_characters_are_preserved_exactly(source):
    import json
    raw = body(source)
    rendered = tokens._render(raw, (tokens.BOS, tokens.USER, tokens.ASSISTANT, tokens.NO_THINKING))
    assert json.loads(raw)["messages"][1]["content"] in rendered


@pytest.mark.parametrize("special", [tokens.BOS, tokens.USER, tokens.ASSISTANT, tokens.NO_THINKING,
                                       "<｜end▁of▁sentence｜>", "<think>"])
def test_registered_control_token_in_source_is_refused(special):
    with pytest.raises(tokens.TokenAccountingError):
        tokens._render(body("Literal " + special + " source"), (special,))


@pytest.mark.parametrize("ids", [[], [0] * 8193, [True], [1.0], [-1], [129280], (1, 2)])
def test_token_count_refuses_invalid_or_over_budget_ids(monkeypatch, ids):
    fake = SimpleNamespace(encode=lambda *_, **__: SimpleNamespace(ids=ids), decode=lambda *_, **__: "irrelevant")
    monkeypatch.setattr(tokens, "_load", lambda _: (fake, (tokens.BOS,)))
    with pytest.raises(tokens.TokenAccountingError):
        tokens.count_tokens((), body())


def test_boundary_count_requires_exact_decode_and_never_normalizes_source(monkeypatch):
    prompt = tokens._render(body(), (tokens.BOS,))
    fake = SimpleNamespace(encode=lambda *_, **__: SimpleNamespace(ids=[0] * 8192),
                           decode=lambda *_, **__: prompt)
    monkeypatch.setattr(tokens, "_load", lambda _: (fake, (tokens.BOS,)))
    assert tokens.count_tokens((), body()) == 8192
    fake.decode = lambda *_, **__: prompt + " "
    with pytest.raises(tokens.TokenAccountingError):
        tokens.count_tokens((), body())


@pytest.mark.parametrize("fault", ["digest", "bytes", "duplicate", "relative", "symlink", "directory", "fifo"])
def test_artifact_reader_is_bounded_and_consumes_only_verified_bytes(monkeypatch, tmp_path, fault):
    import os
    path = tmp_path / "tokenizer.json"
    raw = b"synthetic data-only fixture"
    digest = hashlib.sha256(raw).hexdigest()
    path.write_bytes(raw)
    monkeypatch.setattr(tokens, "ARTIFACTS", {path.name: (len(raw), digest)})
    entries = ((str(path), digest),)
    assert tokens._read_artifacts(entries) == {path.name: raw}
    if fault == "digest":
        entries = ((str(path), "0" * 64),)
    elif fault == "bytes":
        path.write_bytes(b"x" * len(raw))
    elif fault == "duplicate":
        entries = entries + entries
    elif fault == "relative":
        entries = ((path.name, digest),)
    elif fault == "symlink":
        target = tmp_path / "other"
        path.rename(target)
        path.symlink_to(target)
    elif fault == "directory":
        path.unlink()
        path.mkdir()
    elif fault == "fifo":
        path.unlink()
        os.mkfifo(path)
    with pytest.raises(tokens.TokenAccountingError):
        tokens._read_artifacts(entries)


def test_default_tokenizer_cannot_make_a_live_contract_qualified():
    from exitspec import source_authoring_launch as launch
    assert launch._QUALIFIED_SERVING_CONTRACTS == ()
    assert launch._PRODUCTION_PROFILES == ()
    assert Path(tokens.__file__).is_file()
