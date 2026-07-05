"""Post-build transforms framework.

A *transform* is a VF→VF step that runs after the font compiles (e.g. SPAC
injects a spacing axis). Available transforms are discovered from built-ins
+ ``~/.avar2-studio/transforms/``; which are *enabled* (and their params) is
per-project state in a ``<basename>-transforms.json`` sidecar.

Public API used by the server:
    discover()                       — populate the registry (call at startup)
    available(source)                — UI list (registered × saved state)
    active(source)                   — ordered (transform, params) for the build
    set_active(source, entries)      — validate + persist, returns UI list
    sidecar_path_for(source)         — path of the per-project config

Public API used by user scripts:
    Transform, TransformSpec, ParamSpec, BuildContext
"""

from __future__ import annotations

from .base import BuildContext, ParamSpec, Transform, TransformSpec
from .config import sidecar_path_for
from .registry import (
    REGISTRY,
    active,
    available,
    discover,
    get,
    register,
    set_active,
    user_transforms_dir,
)

__all__ = [
    "Transform",
    "TransformSpec",
    "ParamSpec",
    "BuildContext",
    "REGISTRY",
    "discover",
    "available",
    "active",
    "set_active",
    "get",
    "register",
    "sidecar_path_for",
    "user_transforms_dir",
]
