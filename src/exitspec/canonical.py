"""RFC 8785 JSON canonicalization at ExitSpec's trust boundary."""

from __future__ import annotations

from typing import Any

import rfc8785


class CanonicalizationError(ValueError):
    """Raised when a value is outside ExitSpec's canonical JSON domain."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return RFC 8785 JSON Canonicalization Scheme bytes for ``value``.

    Callers must supply JSON-compatible values. In particular, object keys must
    be strings, strings must contain valid Unicode, and numbers must be finite
    and within the interoperable domain enforced by ``rfc8785``.
    """

    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, UnicodeError) as exc:
        raise CanonicalizationError(
            "RFC 8785 canonicalization failed: {0}".format(exc)
        ) from exc
