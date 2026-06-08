"""Deterministic candidate selection for why-not."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .diff import (
    classify_key,
    is_identity_classification,
    is_non_identity_dunder_classification,
    normalize_value,
)

DEFAULT_MAX_PLAIN_DELTA = 3


@dataclass(frozen=True)
class CandidateScore:
    tf_checksum: str
    selection_score: float
    plain_delta_count: int
    matching_plain_key_values: int
    overlapping_plain_keys: int
    matching_dunder_key_values: int
    sort_tuple: tuple[float, float, float, float, str]

    def explanation(self) -> dict[str, Any]:
        return {
            "plain_delta_count": self.plain_delta_count,
            "matching_plain_key_values": self.matching_plain_key_values,
            "overlapping_plain_keys": self.overlapping_plain_keys,
            "matching_dunder_key_values": self.matching_dunder_key_values,
        }


def split_keys(tf_dict: dict[str, Any], classification: str) -> dict[str, Any]:
    return {
        str(key): normalize_value(value)
        for key, value in tf_dict.items()
        if classify_key(str(key)) == classification
    }


def split_identity_keys(tf_dict: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): normalize_value(value)
        for key, value in tf_dict.items()
        if is_identity_classification(classify_key(str(key)))
    }


def split_non_identity_dunder_keys(tf_dict: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): normalize_value(value)
        for key, value in tf_dict.items()
        if is_non_identity_dunder_classification(classify_key(str(key)))
    }


def plain_delta_count(input_dict: dict[str, Any], candidate_dict: dict[str, Any]) -> int:
    plain_input = split_identity_keys(input_dict)
    plain_candidate = split_identity_keys(candidate_dict)
    count = 0
    for key in set(plain_input) | set(plain_candidate):
        if key not in plain_input or key not in plain_candidate:
            count += 1
        elif plain_input[key] != plain_candidate[key]:
            count += 1
    return count


def score_candidate(
    input_dict: dict[str, Any],
    candidate_dict: dict[str, Any],
    tf_checksum: str,
    *,
    max_plain_delta: int = DEFAULT_MAX_PLAIN_DELTA,
) -> CandidateScore | None:
    plain_input = split_identity_keys(input_dict)
    plain_candidate = split_identity_keys(candidate_dict)
    dunder_input = split_non_identity_dunder_keys(input_dict)
    dunder_candidate = split_non_identity_dunder_keys(candidate_dict)
    delta = plain_delta_count(input_dict, candidate_dict)
    if delta > max_plain_delta:
        return None
    matching_plain = sum(
        1 for key, value in plain_input.items() if key in plain_candidate and plain_candidate[key] == value
    )
    overlapping_plain = len(set(plain_input) & set(plain_candidate))
    matching_dunder = sum(
        1 for key, value in dunder_input.items() if key in dunder_candidate and dunder_candidate[key] == value
    )
    denominator = max(len(plain_input), 1)
    delta_score = max(0.0, (max_plain_delta - delta) / max(max_plain_delta, 1))
    plain_match_score = matching_plain / denominator
    plain_key_overlap_score = overlapping_plain / denominator
    dunder_tiebreak = matching_dunder / max(len(dunder_input), 1)
    selection_score = round(
        (delta_score * 0.5) + (plain_match_score * 0.3) + (plain_key_overlap_score * 0.2),
        6,
    )
    return CandidateScore(
        tf_checksum=tf_checksum,
        selection_score=selection_score,
        plain_delta_count=delta,
        matching_plain_key_values=matching_plain,
        overlapping_plain_keys=overlapping_plain,
        matching_dunder_key_values=matching_dunder,
        sort_tuple=(delta_score, plain_match_score, plain_key_overlap_score, dunder_tiebreak, tf_checksum),
    )


def choose_candidate(
    input_dict: dict[str, Any],
    records: list[Any],
    *,
    explain_selection: bool = False,
    max_plain_delta: int = DEFAULT_MAX_PLAIN_DELTA,
) -> tuple[Any | None, CandidateScore | None, list[dict[str, Any]], list[str]]:
    records_with_dicts = [record for record in records if record.transformation_dict is not None]
    if not records:
        return None, None, [], ["empty_haystack"]
    scored = []
    for record in records_with_dicts:
        score = score_candidate(
            input_dict,
            record.transformation_dict,
            record.tf_checksum,
            max_plain_delta=max_plain_delta,
        )
        if score is not None:
            scored.append((record, score))
    if not scored:
        warning = "candidate_not_near_enough" if records_with_dicts else "candidate_not_found"
        return None, None, [], [warning]
    scored.sort(
        key=lambda item: (
            -item[1].sort_tuple[0],
            -item[1].sort_tuple[1],
            -item[1].sort_tuple[2],
            -item[1].sort_tuple[3],
            item[1].tf_checksum,
        )
    )
    selected_record, selected_score = scored[0]
    runners = []
    if explain_selection:
        for record, score in scored[1:4]:
            runners.append(
                {
                    "tf_checksum": record.tf_checksum,
                    "selection_score": score.selection_score,
                    "explanation": score.explanation(),
                }
            )
    return selected_record, selected_score, runners, []
