"""Scripts outside the package import names from it; the blueprint split
moved one (_BUILTIN_EXAMPLES) and the Pages snapshot step broke silently
— and a direct import of routes/ cycles through server.py in a fresh
interpreter, which pytest never sees because conftest imports server
first. So every `from avar2_studio… import X` in scripts/ is resolved in a
fresh interpreter."""
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_every_package_import_in_scripts_resolves_in_a_fresh_interpreter():
    pattern = re.compile(r"^\s*from (avar2_studio[\w.]*) import ([\w, ]+)", re.M)
    checked = 0
    for script in SCRIPTS.glob("*.py"):
        for module, names in pattern.findall(script.read_text()):
            for name in (n.strip() for n in names.split(",")):
                code = f"from {module} import {name}"
                r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                assert r.returncode == 0, f"{script.name}: `{code}` failed:\n{r.stderr[-600:]}"
                checked += 1
    assert checked, "no package imports found under scripts/ — pattern broken?"
