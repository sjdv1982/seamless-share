"""Read-only deep diff helpers."""

from __future__ import annotations

import difflib
import json
from typing import Any

from .models import DeepDiff, DeepKind


def _checksum_from_value(value: Any) -> str | None:
    if isinstance(value, str) and len(value) == 64:
        return value
    if isinstance(value, list) and len(value) == 3 and isinstance(value[2], str) and len(value[2]) == 64:
        return value[2]
    return None


def build_deep_diff(value_a: Any, value_b: Any, endpoints: list[Any]) -> tuple[DeepDiff | None, bool]:
    checksum_a = _checksum_from_value(value_a)
    checksum_b = _checksum_from_value(value_b)
    if checksum_a is None or checksum_b is None:
        return None, False
    buffer_a = _get_buffer(checksum_a, endpoints)
    buffer_b = _get_buffer(checksum_b, endpoints)
    if buffer_a is None or buffer_b is None:
        return (
            DeepDiff(
                kind=DeepKind.CHECKSUM_FALLBACK.value,
                body=None,
                fallback_reason="buffer_unavailable",
            ),
            True,
        )
    try:
        text_a = buffer_a.decode("utf-8")
        text_b = buffer_b.decode("utf-8")
    except UnicodeDecodeError:
        return (
            DeepDiff(kind=DeepKind.CHECKSUM_FALLBACK.value, body=None, fallback_reason="binary_or_opaque"),
            False,
        )
    try:
        json_a = json.loads(text_a)
        json_b = json.loads(text_b)
    except Exception:
        body = "".join(
            difflib.unified_diff(
                text_a.splitlines(keepends=True),
                text_b.splitlines(keepends=True),
                fromfile="A",
                tofile="B",
                n=3,
            )
        )
        return DeepDiff(kind=DeepKind.TEXT_DIFF.value, body=body, fallback_reason=None), False
    return (
        DeepDiff(
            kind=DeepKind.JSON_DIFF.value,
            body={"A": json_a, "B": json_b} if json_a != json_b else {},
            fallback_reason=None,
        ),
        False,
    )


def _get_buffer(checksum: str, endpoints: list[Any]) -> bytes | None:
    for endpoint in endpoints:
        data = endpoint.get_buffer(checksum)
        if data is not None:
            return data
    return None
