import json
from pathlib import Path

from seamless_share.why_not import transformation_diff
from seamless_share.why_not.models import to_json


def test_schema_file_is_valid_json():
    schema_path = Path(__file__).parents[1] / "seamless_share" / "why_not" / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["version"] == "0.1.0"


def test_output_has_schema_version(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text('{"code":"a"}', encoding="utf-8")
    b.write_text('{"code":"b"}', encoding="utf-8")
    payload = json.loads(to_json(transformation_diff(str(a), str(b))))
    assert payload["version"] == "0.1.0"
