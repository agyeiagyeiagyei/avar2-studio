"""Control axes — sidecar JSON I/O.

A **control axis** is one the designer declares in the studio (not in
the source file) with their own min/max/default. The declaration
lives in a sibling JSON file, ``<basename>-control.json``, parallel
to ``<basename>-avar.csv``.

Schema (versioned)::

    {
      "version": 1,
      "axes": [
        {
          "tag": "crbr",
          "display_name": "Crossbar",
          "default": 0,
          "min": -100,
          "max": 100,
          "layers": [                          // flat list, one per brace layer
            {"glyph": "e", "location": {"crbr": -100}},
            {"glyph": "e", "location": {"crbr": 100}},
            {"glyph": "f", "location": {"crbr": 100, "XOPQ": 78}}
          ]
        }
      ]
    }

``layers`` is a flat per-axis list of ``{glyph, location}`` — NOT a
per-glyph object. ``location`` is sparse (only pinned axes) and keyed
by axis tag. Coverage is derived from the unique glyph names in
``layers``; it is not stored. (Legacy ``coverage`` +
``extra_locations`` keys are migrated into ``layers`` on load by
``_normalise`` and never re-emitted.)

Outline storage (model α) is wired end to end: the schema carries an
``outline`` value-dump per layer, ``regenerate_shadow`` restores a stored
outline ahead of the prior-shadow copy and any seed, and
``capture_outlines`` (shadow → sidecar) runs on config export, so a
drawing survives ``.avar2-studio/`` being wiped and travels inside a
config bundle. See docs/guides/secondary-parametric-axes.md and docs/SIDECARS.md.
"""

import copy as copy
import csv as csv
import functools as functools
import json as json
import shutil as shutil
from pathlib import Path as Path
from typing import Dict as Dict
from typing import List as List
from typing import Optional as Optional

from .. import sidecar_io as sidecar_io
from ..studio_log import get_logger as get_logger
from . import schema
from .naming import _fmt_coord as _fmt_coord
from .naming import _non_parametric_indices as _non_parametric_indices
from .naming import corner_name_maps as corner_name_maps
from .naming import corner_name_resolver as corner_name_resolver
from .outlines import _geometry_sig as _geometry_sig
from .outlines import _outline_to_layer_data as _outline_to_layer_data
from .outlines import _parts_to_outline as _parts_to_outline
from .outlines import _paths_sig as _paths_sig
from .outlines import glyph_masters_sig as glyph_masters_sig
from .schema import _INSTANCE_VALUES_LOCK as _INSTANCE_VALUES_LOCK
from .schema import _KNOWN_AXIS_KEYS as _KNOWN_AXIS_KEYS
from .schema import _KNOWN_LAYER_KEYS as _KNOWN_LAYER_KEYS
from .schema import _KNOWN_TOP_KEYS as _KNOWN_TOP_KEYS
from .schema import _SCHEMA_VERSION as _SCHEMA_VERSION
from .schema import SidecarCorruptError as SidecarCorruptError
from .schema import SidecarError as SidecarError
from .schema import SidecarVersionError as SidecarVersionError
from .schema import _empty as _empty
from .schema import _layer_key as _layer_key
from .schema import _locked as _locked
from .schema import _normalise as _normalise
from .schema import _normalise_layers as _normalise_layers
from .schema import _normalise_one as _normalise_one
from .schema import _save as _save
from .schema import _validate_tag as _validate_tag
from .schema import add_axis as add_axis
from .schema import apply_layer_delta as apply_layer_delta
from .schema import find_axis as find_axis
from .schema import instance_values as instance_values
from .schema import list_axes as list_axes
from .schema import load as load
from .schema import load_errors as load_errors
from .schema import remove_axis as remove_axis
from .schema import remove_instance_values as remove_instance_values
from .schema import rename_instance_values as rename_instance_values
from .schema import reseed_layers as reseed_layers
from .schema import save_all as save_all
from .schema import set_instance_values as set_instance_values
from .schema import set_layers as set_layers
from .schema import sidecar_path_for as sidecar_path_for
from .schema import update_axis as update_axis
from .shadow_designspace import (
    _regenerate_shadow_designspace as _regenerate_shadow_designspace,
)
from .shadow_designspace import _studio_ufo_slug as _studio_ufo_slug
from .shadow_glyphs import _extract_brace_outlines as _extract_brace_outlines
from .shadow_glyphs import _restamp_seeds as _restamp_seeds
from .shadow_glyphs import capture_outlines as capture_outlines
from .shadow_glyphs import regenerate_shadow as regenerate_shadow
from .shadow_glyphs import remove_shadow as remove_shadow
from .shadow_glyphs import shadow_dir_for as shadow_dir_for
from .shadow_glyphs import shadow_exists as shadow_exists
from .shadow_glyphs import shadow_path_for as shadow_path_for

log = get_logger(__name__)

# The old module's one annotated module-level name, re-bound to the same
# dict (keeps __annotations__ — and therefore dir() — shaped as before).
_LOAD_ERRORS: Dict[str, str] = schema._LOAD_ERRORS

# Importing the submodules set them as attributes of this package; drop
# those handles so the namespace matches the old flat module exactly.
# The submodules stay in sys.modules and every re-export above (including
# the privates tests monkeypatch) keeps resolving.
for _sub in ("schema", "naming", "outlines", "shadow_glyphs", "shadow_designspace"):
    globals().pop(_sub, None)
del _sub
