"""Check that the Python and npm packages carry the same version, and that a release tag matches it.

Usage: python scripts/check-version.py [vX.Y.Z]
With no tag (or an empty one, as on a manual workflow run) only the lockstep check runs.
"""
import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
py = re.search(r'^version = "([^"]+)"', (root / "python/pyproject.toml").read_text(), re.M)[1]
js = json.loads((root / "js/package.json").read_text())["version"]
if py != js:
    sys.exit("Python (%s) and npm (%s) versions differ" % (py, js))
tag = sys.argv[1] if len(sys.argv) > 1 else ""
if tag and tag != "v" + py:
    sys.exit("Tag %s does not match package version %s; tag v%s instead" % (tag, py, py))
print("Lockstep version %s verified%s" % (py, " for tag " + tag if tag else ""))
