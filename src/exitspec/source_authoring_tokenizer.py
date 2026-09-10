"""Local DeepSeek V4 chat accounting; this is not Fireworks parity evidence.

Only the pinned data-only Hugging Face tokenizer and a pinned Tokenizers core
are loaded locally. No Hub API, remote code, transformers, model or GPU is used.
See docs/source-authoring-live-accounting.md for provenance and qualification.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path

TOKENIZER_ID = "deepseek-v4-flash-0731/7872f01b1d1fe23eabc4c98b48bffcef5a386062/chat-r2/tokenizers-0.23.2"
RUNTIME_VERSION = "0.23.2"
ARTIFACTS = {
    "tokenizer.json": (6367146, "8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf"),
    "tokenizer_config.json": (801, "6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547"),
}
BOS = "<｜begin▁of▁sentence｜>"
USER = "<｜User｜>"
ASSISTANT = "<｜Assistant｜>"
NO_THINKING = "</think>"


class TokenAccountingError(ValueError):
    def __init__(self):
        super().__init__("Local token accounting is unavailable.")


def _read_artifacts(entries):
    """Hash and consume the same bounded, regular, nonsymlink file bytes."""
    try:
        if type(entries) is not tuple or len(entries) != len(ARTIFACTS):
            raise ValueError()
        result = {}
        deadline = time.monotonic() + 2
        for entry in entries:
            if type(entry) is not tuple or len(entry) != 2 or any(type(v) is not str for v in entry):
                raise ValueError()
            name, digest = entry
            path = Path(name)
            if (not path.is_absolute() or ".." in path.parts or len(name) > 4096
                or any(ord(c) < 32 for c in name) or path.name in result
                or path.name not in ARTIFACTS or any(p.is_symlink() for p in (path, *path.parents))):
                raise ValueError()
            size, expected = ARTIFACTS[path.name]
            if digest != expected:
                raise ValueError()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                status = os.fstat(descriptor)
                if not stat.S_ISREG(status.st_mode) or status.st_size != size:
                    raise ValueError()
                raw = bytearray()
                while len(raw) <= size:
                    if time.monotonic() >= deadline:
                        raise ValueError()
                    chunk = os.read(descriptor, min(65536, size + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError()
                result[path.name] = bytes(raw)
            finally:
                os.close(descriptor)
        return result
    except (OSError, ValueError, TypeError, OverflowError):
        raise TokenAccountingError() from None


def _runtime_from_data(data):
    # Import only after fixed data verification; no from_pretrained or Hub APIs.
    import tokenizers
    if tokenizers.__version__ != RUNTIME_VERSION:
        raise TokenAccountingError()
    tokenizer = tokenizers.Tokenizer.from_str(data["tokenizer.json"].decode("utf-8"))
    if (tokenizer.truncation is not None or tokenizer.padding is not None
        or tokenizer.get_vocab_size(with_added_tokens=True) != 129280):
        raise TokenAccountingError()
    vectors = (
        ("Hello world.", [19923, 2058, 16]),
        (BOS + "System" + USER + "Hello" + ASSISTANT + NO_THINKING,
         [0, 8375, 128803, 19923, 128804, 128822]),
        ("Café 中文 👋 123456789", [37, 2797, 619, 223, 21134, 52780, 236, 223, 6895, 18009, 25744]),
    )
    for text, expected in vectors:
        if (tokenizer.encode(text, add_special_tokens=False).ids != expected
            or tokenizer.decode(expected, skip_special_tokens=False) != text):
            raise TokenAccountingError()
    return tokenizer


def _load(entries):
    try:
        data = _read_artifacts(entries)
        added = json.loads(data["tokenizer.json"])["added_tokens"]
        special = tuple(item["content"] for item in added)
        return _runtime_from_data(data), special
    except Exception:  # noqa: BLE001 - native loader failures must remain content-free
        raise TokenAccountingError() from None


def verify(entries):
    _load(entries)


def _render(body, special):
    from .source_authoring_transport import validate_request_body
    validate_request_body(body)
    messages = json.loads(body)["messages"]
    # The r2 body already contains its explicit schema in the system string.
    # response_format is an output constraint, not a second prompt insertion.
    system, user = (message["content"] for message in messages)
    if any(token in content for content in (system, user) for token in special):
        raise TokenAccountingError()
    # Official encoding_dsv4.py: system followed by one user, chat mode, BOS,
    # generation prefix, no tools/history/developer/reasoning/EOS additions.
    return BOS + system + USER + user + ASSISTANT + NO_THINKING


def count_tokens(entries, body):
    """Count the exact local chat encoding; qualified serving parity is separate."""
    try:
        tokenizer, special = _load(entries)
        prompt = _render(body, special)
        ids = tokenizer.encode(prompt, add_special_tokens=False).ids
        if (type(ids) is not list or not 1 <= len(ids) <= 8192
            or any(type(value) is not int or not 0 <= value < 129280 for value in ids)
            or tokenizer.decode(ids, skip_special_tokens=False) != prompt):
            raise TokenAccountingError()
        return len(ids)
    except Exception:  # noqa: BLE001 - no source or native tokenizer traceback
        raise TokenAccountingError() from None
