"""Lookup-state computation across endpoint unions."""

from __future__ import annotations

from typing import Any

from .models import EndpointLookupState, LookupState, LookupStateName


def endpoint_lookup_state(endpoint: Any, tf_checksum: str) -> EndpointLookupState:
    irreproducible = endpoint.get_irreproducible_records(tf_checksum)
    if irreproducible:
        results = sorted({str(item.get("result")) for item in irreproducible if item.get("result")})
        return EndpointLookupState(
            endpoint=endpoint.spec.raw,
            state=LookupStateName.IRREPRODUCIBLE.value,
            details={"row_count": len(irreproducible), "result_checksums": results},
        )
    result_checksum = endpoint.get_transformation_result(tf_checksum)
    if result_checksum is None:
        return EndpointLookupState(
            endpoint=endpoint.spec.raw,
            state=LookupStateName.NOT_PRESENT.value,
            details={},
        )
    if endpoint.buffer_available(result_checksum):
        return EndpointLookupState(
            endpoint=endpoint.spec.raw,
            state=LookupStateName.PRESENT_AS_HIT.value,
            details={"result_checksum": result_checksum},
        )
    return EndpointLookupState(
        endpoint=endpoint.spec.raw,
        state=LookupStateName.PRESENT_RESULT_UNAVAILABLE.value,
        details={"result_checksum": result_checksum, "reason": "not_in_bufferdir"},
    )


def lookup_state(endpoints: list[Any], tf_checksum: str) -> LookupState:
    per_endpoint = [endpoint_lookup_state(endpoint, tf_checksum) for endpoint in endpoints]
    states = [item.state for item in per_endpoint]
    if LookupStateName.IRREPRODUCIBLE.value in states:
        result_checksums = sorted(
            {
                checksum
                for item in per_endpoint
                if item.state == LookupStateName.IRREPRODUCIBLE.value
                for checksum in item.details.get("result_checksums", [])
            }
        )
        row_count = sum(
            item.details.get("row_count", 0)
            for item in per_endpoint
            if item.state == LookupStateName.IRREPRODUCIBLE.value
        )
        return LookupState(
            state=LookupStateName.IRREPRODUCIBLE.value,
            per_endpoint=per_endpoint,
            details={"row_count": row_count, "result_checksums": result_checksums},
        )
    if LookupStateName.PRESENT_AS_HIT.value in states:
        served_by = next(
            item.endpoint for item in per_endpoint if item.state == LookupStateName.PRESENT_AS_HIT.value
        )
        result_checksum = next(
            item.details.get("result_checksum")
            for item in per_endpoint
            if item.state == LookupStateName.PRESENT_AS_HIT.value
        )
        return LookupState(
            state=LookupStateName.PRESENT_AS_HIT.value,
            per_endpoint=per_endpoint,
            details={"result_checksum": result_checksum, "served_by": served_by},
        )
    if LookupStateName.PRESENT_RESULT_UNAVAILABLE.value in states:
        item = next(
            item
            for item in per_endpoint
            if item.state == LookupStateName.PRESENT_RESULT_UNAVAILABLE.value
        )
        return LookupState(
            state=LookupStateName.PRESENT_RESULT_UNAVAILABLE.value,
            per_endpoint=per_endpoint,
            details=dict(item.details),
        )
    return LookupState(
        state=LookupStateName.NOT_PRESENT.value,
        per_endpoint=per_endpoint,
        details={},
    )
