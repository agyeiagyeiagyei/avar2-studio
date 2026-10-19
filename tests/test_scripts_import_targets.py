"""Scripts outside the package import private names from it; the blueprint
split moved one (_BUILTIN_EXAMPLES) and the Pages snapshot step broke
silently. Every `from avar2_studio… import X` in scripts/ must resolve."""
import importlib
import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_every_package_import_in_scripts_resolves():
    pattern = re.compile(r"^\s*from (avar2_studio[\w.]*) import ([\w, ]+)", re.M)
    checked = 0
    for script in SCRIPTS.glob("*.py"):
        for module, names in pattern.findall(script.read_text()):
            mod = importlib.import_module(module)
            for name in (n.strip() for n in names.split(",")):
                assert hasattr(mod, name), f"{script.name}: {module}.{name} does not exist"
                checked += 1
    assert checked, "no package imports found under scripts/ — pattern broken?"
