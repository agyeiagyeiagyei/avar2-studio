"""Transform registry + discovery.

*Available* transforms come from two places, merged into one registry:

  1. **Built-ins** shipped in this package (SPAC now; more later).
  2. **User scripts** — any ``.py`` in ``~/.avar2-studio/transforms/`` that
     defines a :class:`~avar2_studio.transforms.base.Transform` subclass.

Discovery runs once at server startup (:func:`discover`). A malformed user
script is logged and skipped — it never crashes the studio.

Note: user scripts are arbitrary Python executed in-process. That's an
intentional plugin surface (a local dev tool running the designer's own
scripts), the same trust model as a Glyphs.app or fontmake plugin.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from . import config as _config
from .base import Transform
from .builtin_gftools import (
    FixInstancesTransform,
    FixUnhintedTransform,
    GenStatTransform,
)
from .builtin_spac import SpacTransform
from .builtin_spac_edgeaware import EdgeAwareSpacTransform
from .builtin_spac_widthaware import WidthAwareSpacTransform

# id -> Transform instance
REGISTRY: Dict[str, Transform] = {}

_discovered = False


def register(transform: Transform) -> None:
    tid = transform.spec.id
    if tid in REGISTRY:
        print(f"transforms: '{tid}' already registered; overriding", file=sys.stderr)
    REGISTRY[tid] = transform


def user_transforms_dir() -> Path:
    return Path.home() / ".avar2-studio" / "transforms"


def discover(force: bool = False) -> None:
    """Populate REGISTRY with built-ins + user scripts. Idempotent."""
    global _discovered
    if _discovered and not force:
        return
    REGISTRY.clear()

    # Built-ins.
    register(SpacTransform())
    register(WidthAwareSpacTransform())
    register(EdgeAwareSpacTransform())
    register(FixInstancesTransform())
    register(GenStatTransform())
    register(FixUnhintedTransform())

    # User scripts.
    d = user_transforms_dir()
    try:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            _write_readme(d)
    except OSError as e:
        print(f"transforms: could not create {d}: {e}", file=sys.stderr)

    if d.exists():
        for py in sorted(d.glob("*.py")):
            try:
                _load_script(py)
            except Exception as e:  # noqa: BLE001 — never let a bad script break startup
                print(f"transforms: failed to load {py.name}: {e}", file=sys.stderr)

    _discovered = True


def _load_script(path: Path) -> None:
    mod_name = f"avar2_user_transform_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    found = 0
    for _name, obj in vars(module).items():
        if inspect.isclass(obj) and issubclass(obj, Transform) and obj is not Transform:
            if not getattr(obj, "spec", None):
                continue
            register(obj())
            found += 1
    if found == 0:
        print(
            f"transforms: {path.name} defines no Transform subclass — skipped",
            file=sys.stderr,
        )


def get(transform_id: str):
    return REGISTRY.get(transform_id)


def all_specs() -> List[Transform]:
    """Registered transforms, ordered by id for stable UI listing."""
    return [REGISTRY[k] for k in sorted(REGISTRY)]


# --------------------------------------------------------------------------
# Registry × per-project config merges
# --------------------------------------------------------------------------


def available(source_path: Path) -> List[Dict]:
    """UI list: every registered transform merged with the project's saved
    enabled state + params. Registered order, with any saved-but-unknown
    types dropped (an uninstalled script)."""
    saved = {e["type"]: e for e in _config.entries(source_path)}
    out = []
    for t in all_specs():
        spec = t.spec
        entry = saved.get(spec.id)
        enabled = bool(entry["enabled"]) if entry else spec.default_enabled
        params = spec.coerce_params(entry["params"] if entry else None)
        d = spec.to_dict()
        d["enabled"] = enabled
        d["params"] = params
        out.append(d)
    return out


def active(source_path: Path) -> List[Tuple[Transform, Dict]]:
    """Build list: ordered ``(transform, params)`` for enabled entries whose
    type is a registered transform. Preserves the sidecar's order."""
    out: List[Tuple[Transform, Dict]] = []
    for e in _config.entries(source_path):
        if not e["enabled"]:
            continue
        t = REGISTRY.get(e["type"])
        if t is None:
            continue
        out.append((t, t.spec.coerce_params(e["params"])))
    return out


def set_active(source_path: Path, entries_list: List[Dict]) -> List[Dict]:
    """Validate + persist the transform entries. Unknown types are dropped;
    params are coerced against each transform's schema. Each ENABLED
    transform's params are cross-validated (``Transform.validate``) BEFORE
    anything is persisted, so an invalid config (e.g. SPAC min >= max) raises
    ``ValueError`` and the caller returns a 400 with feedback — rather than
    persisting an enabled-but-doomed transform that silently fails at build.
    Returns the fresh UI list."""
    cleaned = []
    enabled_by_tag = {}
    for e in entries_list or []:
        if not isinstance(e, dict):
            continue
        t = REGISTRY.get(str(e.get("type", "")))
        if t is None:
            continue
        enabled = bool(e.get("enabled", False))
        params = t.spec.coerce_params(e.get("params"))
        if enabled:
            try:
                t.validate(params)
            except ValueError as exc:
                raise ValueError(f"{t.spec.name}: {exc}") from exc
            # At most one enabled transform may inject a given fvar axis —
            # two SPAC injectors would produce a font with two SPAC axes.
            tag = t.spec.injected_axis_tag
            if tag:
                if tag in enabled_by_tag:
                    raise ValueError(
                        f"Only one transform can add the {tag} axis at a time "
                        f"('{enabled_by_tag[tag]}' and '{t.spec.name}' both do)."
                    )
                enabled_by_tag[tag] = t.spec.name
        cleaned.append({"type": t.spec.id, "enabled": enabled, "params": params})
    _config.save(source_path, cleaned)
    return available(source_path)


_README = """# avar2-studio transforms

Drop a `.py` file here to add a build transform. It runs on the compiled
variable font (VF -> VF) after every build and shows up in the studio's
header "Transforms" dropdown, off by default.

Each file must define a `Transform` subclass:

    from avar2_studio.transforms import Transform, TransformSpec, ParamSpec

    class MyTransform(Transform):
        spec = TransformSpec(
            id="mytransform",              # stable, unique, persisted
            name="My transform",
            description="What it does.",
            params=[ParamSpec(key="amount", label="Amount", type="int", default=10)],
            default_enabled=False,
        )

        def apply(self, vf_path, params, ctx):
            # vf_path: Path to the compiled .ttf
            # params:  {"amount": 10}   (coerced against your ParamSpec)
            # ctx:     .build_dir .source_path .glyphs_path .family .log
            # Return a Path to the (possibly new) .ttf. Write outputs into
            # vf_path.parent so the served-font plumbing stays consistent.
            ...
            return vf_path

Notes:
- Transforms are VF post-processors: they must not edit the source .glyphs.
- Errors are caught and logged; a failing transform is skipped, the build
  degrades to the last-good font.
- Scripts run in-process (arbitrary code) — only add scripts you trust.
"""


def _write_readme(d: Path) -> None:
    try:
        (d / "README.md").write_text(_README, encoding="utf-8")
    except OSError:
        pass
