"""The bundled example projects (Load Font dropdown, the static demo
snapshot). Dependency-free on purpose: scripts/ import it in a fresh
interpreter, where importing routes/ would cycle through server.py."""

_BUILTIN_EXAMPLES = [
    {
        "id": "roboto-delta-mini",
        "name": "Roboto Delta Mini",
        "subtitle": "Case-split parametric axes (XOUC/YOUC/XTUC, XOLC/YOLC/XTLC, XOFI/YOFI/XTFI)",
        "source_rel": "examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace",
    },
    {
        "id": "crispy-mini",
        "name": "Crispy Mini",
        "subtitle": "Unified parametric axes (XTRA/XOPQ/YOPQ)",
        "source_rel": "examples/crispy-mini/sources/CrispyMini.glyphs",
    },
]
