import ast
from pathlib import Path


FORBIDDEN_CALLS = {
    "create",
    "save",
    "delete",
    "set_transformation_result",
    "set_execution_record",
    "set_bucket_probe",
    "undo_transformation_result",
    "put",
    "post",
}


def test_production_package_has_no_known_write_calls():
    package_root = Path(__file__).parents[1] / "seamless_share" / "why_not"
    offenders = []
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in FORBIDDEN_CALLS:
                    offenders.append((path.name, node.lineno, name))
    assert offenders == []
