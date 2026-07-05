"""Transform interface — the contract every post-build transform implements.

A **transform** is a pure VF→VF post-build step: it takes one compiled
``.ttf`` and returns a (possibly new) ``.ttf``. It never edits the source
``.glyphs``, the shadow, or the CSV — that keeps transforms orthogonal to
the control-axis/shadow machinery and lets them compose by chaining.

Built-in transforms live in :mod:`avar2_studio.transforms.builtin_*`.
User transforms are ordinary ``.py`` files dropped into
``~/.avar2-studio/transforms/`` that ``import`` these classes and define a
:class:`Transform` subclass — see the README the studio writes into that
folder on first run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


@dataclass
class ParamSpec:
    """One editable parameter, rendered as an input in the header dropdown
    and used to validate PUT bodies.

    ``type`` is one of "int" | "float" | "bool" | "select". A "select" param
    must supply ``options`` (list of ``{"value","label"}``); its value is
    coerced to one of the option values (falling back to ``default``)."""

    key: str
    label: str
    type: str = "int"
    default: object = 0
    min: Optional[float] = None
    max: Optional[float] = None
    options: Optional[list] = None     # for type == "select": [{"value","label"}]

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "options": self.options,
        }

    def coerce(self, value):
        """Coerce + clamp an incoming value to this param's type/range."""
        if self.type == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if self.type == "select":
            allowed = [o["value"] for o in (self.options or [])]
            return value if value in allowed else self.default
        try:
            num = int(value) if self.type == "int" else float(value)
        except (TypeError, ValueError):
            return self.default
        if self.min is not None:
            num = max(num, int(self.min) if self.type == "int" else self.min)
        if self.max is not None:
            num = min(num, int(self.max) if self.type == "int" else self.max)
        return num


@dataclass
class TransformSpec:
    """Declarative metadata. The single source of truth that both drives
    the header UI and validates input — the header needs no per-transform
    code."""

    id: str                            # stable key persisted in the sidecar
    name: str                          # header label
    description: str = ""              # one-line subtitle
    params: List[ParamSpec] = field(default_factory=list)
    default_enabled: bool = False
    # If this transform injects an fvar axis (e.g. SPAC), its tag. The registry
    # enforces at most one enabled transform per injected tag, so two SPAC
    # transforms can't both add a SPAC axis (which would corrupt the font).
    injected_axis_tag: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "params_schema": [p.to_dict() for p in self.params],
            "default_enabled": self.default_enabled,
            "injected_axis_tag": self.injected_axis_tag,
        }

    def default_params(self) -> dict:
        return {p.key: p.default for p in self.params}

    def coerce_params(self, params: Optional[dict]) -> dict:
        """Return a full param dict: every declared param filled from the
        input (coerced/clamped) or its default."""
        params = params or {}
        return {p.key: p.coerce(params.get(p.key, p.default)) for p in self.params}


@dataclass
class BuildContext:
    """Read-only build state handed to :meth:`Transform.apply`."""

    build_dir: Path                    # .avar2-studio/build
    source_path: Path                  # ORIGINAL_PATH (the user's source)
    glyphs_path: Path                  # active source (shadow or original)
    family: str                        # source stem, for output naming
    log: Callable[[str], None] = lambda _m: None


class Transform:
    """Base class. Subclass it, set ``spec``, implement ``apply``."""

    spec: TransformSpec

    def validate(self, params: dict) -> None:
        """Cross-field parameter validation, run BEFORE persisting an enabled
        transform (so a bad config is rejected at the API with feedback rather
        than silently failing at build time). Raise ``ValueError`` with a
        user-facing message on invalid params. Default: accept anything the
        per-field ``ParamSpec`` coercion already allowed."""
        return None

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        """Take a compiled VF, return a (possibly new) compiled VF.

        Must not mutate ``vf_path`` in place unless it also returns it —
        callers treat the return value as the new font. Write outputs into
        ``vf_path.parent`` (co-located with the build) so the served-font
        plumbing stays consistent.
        """
        raise NotImplementedError
