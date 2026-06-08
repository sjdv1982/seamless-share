from seamless_share.why_not.diff import transformation_diff_core


def test_identical_dict_diff_empty():
    entries, identity_relevant, warnings = transformation_diff_core(
        {"code": ["plain", None, "a" * 64]},
        {"code": ["plain", None, "a" * 64]},
    )
    assert entries == []
    assert identity_relevant is False
    assert warnings == []


def test_plain_diff_identity_relevant():
    entries, identity_relevant, warnings = transformation_diff_core(
        {"code": ["plain", None, "a" * 64]},
        {"code": ["plain", None, "b" * 64]},
    )
    assert [entry.key for entry in entries] == ["code"]
    assert entries[0].classification == "plain"
    assert entries[0].side == "value_differs"
    assert identity_relevant is True
    assert warnings == []


def test_load_bearing_dunder_diff_is_identity_relevant():
    entries, identity_relevant, warnings = transformation_diff_core(
        {"__language__": "python"},
        {"__language__": "bash"},
    )
    assert entries[0].classification == "load_bearing_dunder"
    assert identity_relevant is True
    assert warnings == []


def test_orthogonal_dunder_diff_warns_dunder_only():
    entries, identity_relevant, warnings = transformation_diff_core(
        {"__meta__": {"local": True}},
        {"__meta__": {"local": False}},
    )
    assert entries[0].classification == "orthogonal_dunder"
    assert identity_relevant is False
    assert warnings == ["dunder_only_diff"]


def test_derived_dunder_diff_warns_dunder_only():
    entries, identity_relevant, warnings = transformation_diff_core(
        {"__header__": "a" * 64},
        {"__header__": "b" * 64},
    )
    assert entries[0].classification == "derived_dunder"
    assert identity_relevant is False
    assert warnings == ["dunder_only_diff"]


def test_entry_order_is_deterministic():
    entries, _, _ = transformation_diff_core({"z": 1, "a": 1}, {"z": 2, "b": 2})
    assert [(entry.key, entry.side) for entry in entries] == [
        ("a", "key_only_in_A"),
        ("b", "key_only_in_B"),
        ("z", "value_differs"),
    ]
