from types import SimpleNamespace

from seamless_share.why_not.selection import (
    DEFAULT_MAX_PLAIN_DELTA,
    choose_candidate,
    plain_delta_count,
)


def test_plain_delta_count_counts_one_sided_and_value_changes():
    assert plain_delta_count({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == 2


def test_plain_delta_count_includes_load_bearing_dunders():
    assert (
        plain_delta_count(
            {"code": "a", "__language__": "python"},
            {"code": "a", "__language__": "bash"},
        )
        == 1
    )


def test_refuses_candidate_with_one_shared_value_but_too_many_deltas():
    input_dict = {"shared": 1, "a": 1, "b": 2, "c": 3, "d": 4}
    candidate = SimpleNamespace(
        tf_checksum="b" * 64,
        transformation_dict={"shared": 1, "a": 9, "b": 9, "c": 9, "d": 9},
    )
    selected, score, runners, warnings = choose_candidate(input_dict, [candidate])
    assert DEFAULT_MAX_PLAIN_DELTA == 3
    assert selected is None
    assert score is None
    assert runners == []
    assert warnings == ["candidate_not_near_enough"]


def test_selects_near_candidate_deterministically():
    input_dict = {"code": "a", "x": 1}
    worse = SimpleNamespace(tf_checksum="c" * 64, transformation_dict={"code": "b", "x": 1})
    better = SimpleNamespace(tf_checksum="b" * 64, transformation_dict={"code": "a", "x": 2})
    selected, score, _, warnings = choose_candidate(input_dict, [worse, better])
    assert selected.tf_checksum == "b" * 64
    assert score.plain_delta_count == 1
    assert warnings == []
