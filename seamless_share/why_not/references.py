"""Reference resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .diff import non_checksum_keys
from .errors import EndpointError, UsageError
from .models import Reference, ReferenceForm

try:
    from seamless.checksum.calculate_checksum import calculate_dict_checksum
except Exception:  # pragma: no cover - fallback for isolated tests
    import hashlib

    def calculate_dict_checksum(d: dict) -> str:
        payload = json.dumps(d, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        return hashlib.sha256(payload).hexdigest()


def plain_transformation_dict(tf_dict: dict[str, Any]) -> dict[str, Any]:
    excluded = non_checksum_keys()
    return {str(key): value for key, value in tf_dict.items() if str(key) not in excluded}


def transformation_checksum(tf_dict: dict[str, Any]) -> str:
    return calculate_dict_checksum(plain_transformation_dict(tf_dict))


def coerce_reference(ref: Reference | str) -> Reference:
    if isinstance(ref, Reference):
        return ref
    return Reference.from_str(ref)


def resolve_reference(ref: Reference | str, endpoints: list[Any] | None = None) -> Reference:
    resolved = coerce_reference(ref)
    if resolved.transformation_dict is not None:
        if resolved.tf_checksum is None:
            resolved.tf_checksum = transformation_checksum(resolved.transformation_dict)
        return resolved
    if resolved.reference_form == ReferenceForm.DICT_PATH.value:
        path = Path(resolved.original)
        if not path.exists():
            raise UsageError(f"definition path cannot load: {path}")
        try:
            with path.open("r", encoding="utf-8") as handle:
                tf_dict = json.load(handle)
        except Exception as exc:
            raise UsageError(f"definition path cannot load: {path}") from exc
        if not isinstance(tf_dict, dict):
            raise UsageError(f"transformation dict path is not a JSON object: {path}")
        resolved.transformation_dict = tf_dict
        resolved.tf_checksum = transformation_checksum(tf_dict)
        return resolved
    if resolved.reference_form == ReferenceForm.DEFINITION_PATH.value:
        raise UsageError(
            f"unsupported definition path for v1: {resolved.original}; use a JSON transformation dict"
        )
    if resolved.reference_form == ReferenceForm.TF_CHECKSUM.value:
        for endpoint in endpoints or []:
            tf_dict = endpoint.get_transformation_dict(resolved.tf_checksum)
            if tf_dict is not None:
                resolved.transformation_dict = tf_dict
                resolved.source_endpoint = endpoint.spec.raw
                return resolved
        raise EndpointError(
            f"transformation dict for checksum {resolved.tf_checksum} is unavailable from endpoints"
        )
    raise UsageError(f"unsupported reference: {resolved.original}")
