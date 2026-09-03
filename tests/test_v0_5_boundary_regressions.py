"""Deterministic v0.5 regressions for local evidence boundary races."""

from __future__ import annotations

import pytest

import exitspec.inferdrome_bundle as bundle_module
from exitspec.inferdrome_bundle import (
    InferdromeBundleErrorCode,
    InferdromeBundleRejected,
    verify_inferdrome_bundle,
)
from tests.inferdrome_helpers import mutable_bundle_copy


def test_extra_file_injected_after_initial_scan_is_detected(tmp_path, monkeypatch):
    bundle = mutable_bundle_copy(tmp_path)
    original_scandir = bundle_module.os.scandir
    injected = False

    class _InjectAfterExhaustion:
        def __init__(self, iterator):
            self._iterator = iterator

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._iterator.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal injected
            try:
                return next(self._iterator)
            except StopIteration:
                if not injected:
                    injected = True
                    (bundle / "unexpected.json").write_text(
                        "injected", encoding="utf-8"
                    )
                raise

    def scanning(path):
        iterator = original_scandir(path)
        if not injected:
            return _InjectAfterExhaustion(iterator)
        return iterator

    monkeypatch.setattr(bundle_module.os, "scandir", scanning)

    with pytest.raises(InferdromeBundleRejected) as caught:
        verify_inferdrome_bundle(bundle, require_customer_eligible=False)

    assert injected is True
    assert caught.value.code is InferdromeBundleErrorCode.UNSAFE_BUNDLE
