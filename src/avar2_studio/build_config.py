"""The committed build config — ``<stem>-build.json`` (phase C item C7).

One file drives the whole headless export from the source, the mappings CSV and
this config: which transforms run (and with what params), which axes are hidden,
the resting style, the named instances / STAT and the output formats. CI needs
no flags at all when it is present.

Boundary (binding): everything design-authored stays studio-only and read-only
for the build — ``<stem>-control.json`` (secondary axes, brace layers, instance
values), ``<stem>-grade.json``, ``<stem>-kinetikern.json`` and the instance
coordinates in the CSV. This module refuses a config that tries to name one of
them, with a message pointing at the studio.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

BUILD_CONFIG_FORMAT = "avar2-studio-build"
BUILD_CONFIG_VERSION = 1

#: Top-level keys the build config may carry.
ALLOWED_KEYS = {
    "format", "format_version", "axes", "default", "transforms",
    "instances", "formats",
}

#: Design-authored state the build only ever READS. A config key naming one is
#: a usage error: editing them is the studio's job.
DESIGN_ONLY_KEYS = {
    "control", "controls", "control_axes", "control_axis",
    "grade", "grades", "grade_scale", "grade_percent",
    "kinetikern", "kk2",
    "coordinates", "instance_coordinates",
}


def path_for(source: Path) -> Path:
    """``sources/MyFont.glyphs`` → ``sources/MyFont-build.json``."""
    source = Path(source)
    return source.parent / f"{source.stem}-build.json"


def _usage(message: str) -> ValueError:
    return ValueError(message)


def validate(data: Dict, *, where: str = "build config") -> Dict:
    """Check a parsed config and return a normalised copy.

    Raises ``ValueError`` with a message meant for the user: the CLI turns it
    into exit 2.
    """
    if not isinstance(data, dict):
        raise _usage(f"{where}: expected a JSON object")
    for key in data:
        lowered = str(key).lower()
        if lowered in DESIGN_ONLY_KEYS:
            raise _usage(
                f"{where}: '{key}' is studio-authored state — open the source in "
                f"avar2-studio and edit it there. The build config cannot change "
                f"control axes, grades, Kinetikern values or instance coordinates."
            )
        if key not in ALLOWED_KEYS:
            raise _usage(
                f"{where}: unknown key '{key}' "
                f"(allowed: {', '.join(sorted(ALLOWED_KEYS))})"
            )

    fmt = data.get("format", BUILD_CONFIG_FORMAT)
    if fmt != BUILD_CONFIG_FORMAT:
        raise _usage(f"{where}: format must be '{BUILD_CONFIG_FORMAT}', got {fmt!r}")
    version = data.get("format_version", BUILD_CONFIG_VERSION)
    if version != BUILD_CONFIG_VERSION:
        raise _usage(f"{where}: unsupported format_version {version!r} "
                     f"(this build reads {BUILD_CONFIG_VERSION})")

    axes = data.get("axes") or {}
    if not isinstance(axes, dict):
        raise _usage(f"{where}: 'axes' must be an object")
    for key in axes:
        if key != "hide":
            raise _usage(f"{where}: 'axes' only takes 'hide' (got '{key}') — "
                         f"the declared defaults come from "
                         f"<stem>-axis-metadata.json")
    hide = axes.get("hide") or []
    if not isinstance(hide, list) or not all(isinstance(t, str) for t in hide):
        raise _usage(f"{where}: 'axes.hide' must be a list of fvar tags")

    default = data.get("default") or None
    if default is not None:
        if not isinstance(default, dict):
            raise _usage(f"{where}: 'default' must be an object of tag: value")
        cleaned_default = {}
        for tag, value in default.items():
            try:
                cleaned_default[str(tag)] = float(value)
            except (TypeError, ValueError) as exc:
                raise _usage(
                    f"{where}: 'default.{tag}' is not a number: {value!r}"
                ) from exc
        default = cleaned_default

    transforms = data.get("transforms")
    if transforms is not None:
        if not isinstance(transforms, list):
            raise _usage(f"{where}: 'transforms' must be a list")
        from .transforms import registry
        registry.discover()
        try:
            transforms = registry.validate(transforms)
        except ValueError as exc:
            raise _usage(f"{where}: transforms: {exc}") from exc

    instances = data.get("instances") or {}
    if not isinstance(instances, dict):
        raise _usage(f"{where}: 'instances' must be an object")
    for key in instances:
        if key not in ("fix", "stat"):
            raise _usage(f"{where}: 'instances' only takes 'fix' and 'stat' "
                         f"(got '{key}')")
    instances = {k: bool(instances.get(k, False)) for k in ("fix", "stat")}

    formats = data.get("formats") or ["ttf"]
    if not isinstance(formats, list) or not formats:
        raise _usage(f"{where}: 'formats' must be a non-empty list")
    for fmt_name in formats:
        if fmt_name not in ("ttf", "woff2"):
            raise _usage(f"{where}: unsupported format {fmt_name!r} "
                         f"(ttf, woff2)")

    return {
        "format": BUILD_CONFIG_FORMAT,
        "format_version": BUILD_CONFIG_VERSION,
        "axes": {"hide": list(hide)},
        "default": default,
        "transforms": transforms,
        "instances": instances,
        "formats": list(formats),
    }


def load(path: Path) -> Dict:
    """Read + validate a build config file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _usage(f"could not read build config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise _usage(f"build config {path} is not valid JSON: {exc}") from exc
    return validate(data, where=path.name)


def transform_entries(cfg: Dict) -> Optional[List[Dict]]:
    """The entries to use for this run, with ``instances.fix/stat`` folded in.

    ``instances`` maps onto the two gftools transforms the studio already runs
    (``fix_instances`` / ``gen_stat``), so there is one mechanism, not two.
    """
    transforms = cfg.get("transforms")
    instances = cfg.get("instances") or {}
    if transforms is None and not any(instances.values()):
        return None
    entries = [dict(e) for e in (transforms or [])]
    by_type = {e.get("type"): e for e in entries}
    for transform_id, wanted in (("fix_instances", instances.get("fix")),
                                 ("gen_stat", instances.get("stat"))):
        if not wanted:
            continue
        existing = by_type.get(transform_id)
        if existing is not None:
            existing["enabled"] = True
        else:
            entries.append({"type": transform_id, "enabled": True, "params": {}})
    return entries


def from_sidecars(source: Path) -> Dict:
    """The config ``--write-config`` writes: what the studio currently has.

    Reads the committed sidecars only (never writes them): the transforms
    sidecar supplies the entries, the metadata the declared defaults, and the
    mappings CSV/live state supply nothing design-authored — ``default`` and
    ``axes.hide`` are the designer's choices to fill in.
    """
    from .transforms import config as _tx_config
    entries = _tx_config.entries(source)
    keep = [e for e in entries if e["type"] not in ("fix_instances", "gen_stat")]
    instances = {
        "fix": any(e["type"] == "fix_instances" and e["enabled"] for e in entries),
        "stat": any(e["type"] == "gen_stat" and e["enabled"] for e in entries),
    }
    return {
        "format": BUILD_CONFIG_FORMAT,
        "format_version": BUILD_CONFIG_VERSION,
        "axes": {"hide": []},
        "default": None,
        "transforms": keep,
        "instances": instances,
        "formats": ["ttf", "woff2"],
    }


def write(path: Path, cfg: Dict) -> Path:
    path = Path(path)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path
