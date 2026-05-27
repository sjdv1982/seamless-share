import json

from seamless_share.replay.models import Finding, to_json


def test_finding_id_ignores_context():
    fields = {
        "checksum": "a" * 64,
        "requested_by": "test",
        "script_position": None,
        "available_authorizations": [],
    }
    first = Finding.build("unauthorized_materialization", fields, context={"wall_ms": 1})
    second = Finding.build("unauthorized_materialization", fields, context={"wall_ms": 2})
    assert first.id == second.id


def test_finding_json_is_deterministic():
    finding = Finding.build(
        "irreproducible_only_hit",
        {
            "tf_checksum": "b" * 64,
            "row_count": 2,
            "result_checksums": ["d" * 64, "c" * 64],
            "script_position": "x.py:1",
        },
    )
    first = to_json(finding)
    second = to_json(finding)
    assert first == second
    assert json.loads(first)["fields"]["result_checksums"] == ["c" * 64, "d" * 64]
