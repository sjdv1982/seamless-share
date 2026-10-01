"""Make source-tree tests independent of the caller's working directory."""

import os
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT_TEXT = str(PACKAGE_ROOT)

# Pytest imports test modules after loading this conftest. Add the source root
# for those imports and export it to CLI subprocesses started by the tests.
def _is_package_root(entry):
    try:
        return Path(entry or os.curdir).resolve() == PACKAGE_ROOT
    except OSError:
        return False


sys.path[:] = [entry for entry in sys.path if not _is_package_root(entry)]
sys.path.insert(0, PACKAGE_ROOT_TEXT)

pythonpath = os.environ.get("PYTHONPATH", "")
pythonpath_entries = [
    entry
    for entry in pythonpath.split(os.pathsep)
    if entry and not _is_package_root(entry)
]
os.environ["PYTHONPATH"] = os.pathsep.join([PACKAGE_ROOT_TEXT, *pythonpath_entries])
