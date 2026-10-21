"""The build/export pipeline, extracted from ``server.py`` (audit §3 step c).

Everything the build worker runs lives here, Flask-free, so a worker
subprocess never constructs the app or starts the supervisor thread
(docs/decisions/0003-worker-subprocess-finish-then-rebuild.md):
``_resolve_active_source`` → config regen → fontc → fix/STAT/avar2/fvar
(``_build_in_process``) → the post-build transform chain for builds;
``_export_pipeline`` for exports; ``_apply_source_path`` to point the
shared ``state`` globals at a (new) source. server.py imports these names
back for its routes, the watcher and the job queue; the only queue
touchpoint here is ``state.JOBS`` (set by server.py at import time).
"""

import contextlib
import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from fontTools.ttLib import TTFont

from . import control_axes as _control_axes
from . import csv_io as _csv_io
from . import grade as _grade
from . import grade_shadow as _grade_shadow
from . import kinetikern as _kinetikern
from . import sidecar_io as _sidecar_io
from . import source_font as _source_font
from . import state
from . import transforms as _transforms
from .studio_log import get_logger

log = get_logger(__name__)

# Registered-axis conventions, used wherever a declared axis has no
# explicit default: the /api/avar2/axes response AND the compiled-axis
# ranges handed to the gen-avar2 shim. One table so the sliders and
# the built fvar can never disagree about where "default" is.
TRADITIONAL_AXIS_DEFAULTS = {
    "wght": 400.0,
    "wdth": 100.0,
    "opsz": 72.0,
    "cntr": 0.0,
    "slnt": 0.0,
    "ital": 0.0,
    "grad": 0.0,
    "spac": 0.0,
}

# Serializes _resolve_active_source: it rewrites the shadow + transformed
# copy and re-points GLYPHS_PATH, and is called from request threads, the
# debounce timer, the watchdog thread and the job supervisor — two
# overlapping resolves interleave those writes (measured in the T0.7
# verifier round). Never taken while holding SIDECAR_LOCK, and nothing
# holding it may take SIDECAR_LOCK (the config import nests
# SIDECAR→RESOLVE; keep that the only order).
_RESOLVE_LOCK = threading.RLock()

# Watchdog suppression window: while time.time() < this value, the file
# watcher ignores source-file modifications. Set around SERVER-INITIATED
# writes (instance rename/update/delete, shadow regen) — those changes
# already carry their own rebuild, and letting the watcher fire on them
# produced a redundant CSV sync plus a second full build per edit (the
# "saved, saved, rebuilt, rebuilt" sequence). External saves from
# Glyphs.app outside the window still sync + rebuild as before.
_SUPPRESS_WATCHDOG_SECONDS = 2.5   # outlast the server's own shadow write (~1s), no more
# The one server path that writes the designer's ORIGINAL is the instance-edit
# round-trip through Glyphs.app (save / close / reopen via AppleScript), which
# can take several seconds. It gets its own window so a designer's save is
# never held hostage to the shadow-regen window above: the two files are
# written by different things and must be suppressed for different reasons.
_SUPPRESS_ORIGINAL_SECONDS = 6.0
# mtime watermarks, armed at the same sites as the windows above. The time
# window alone fails when the watchdog handler is BUSY (a multi-second
# resolve): the self-write's event is processed after the window expired
# and re-fires the handler, which resolved again and re-queued the build —
# the T0.7 round-1 storm. The watermark suppresses an event whenever the
# file's mtime is not newer than the last server-initiated write, no matter
# how late the event is processed; a designer's save always re-writes the
# file, so its mtime is newer and the event goes through.


def _suppress_watcher(original: bool = False) -> None:
    """Mark a server-initiated write for watcher suppression: the time
    window covers the in-flight write (mtime still moving), the watermark
    covers events processed late. Arm AFTER the write when possible."""
    now = time.time()
    if original:
        state._SUPPRESS_ORIGINAL_UNTIL = now + _SUPPRESS_ORIGINAL_SECONDS
        state._SELF_WRITE_ORIGINAL_WATERMARK = now
    else:
        state._SUPPRESS_WATCHDOG_UNTIL = now + _SUPPRESS_WATCHDOG_SECONDS
        state._SELF_WRITE_WATERMARK = now


@contextlib.contextmanager
def _gc_paused():
    """Pause CPython's cyclic GC for a build-critical section: glyphsLib
    churns millions of temporaries and on a grown heap the collector
    dominated the build. Never force a collect afterwards — it wedged a
    build for 30+ minutes. See
    docs/decisions/0003-worker-subprocess-finish-then-rebuild.md."""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        # No forced collect here (see the docstring); incremental GC
        # drains the garbage without blocking the world.


def _build_context():
    """Read-only build state handed to each transform's apply()."""
    return _transforms.BuildContext(
        build_dir=state.BUILD_DIR,
        source_path=state.ORIGINAL_PATH,
        glyphs_path=state.GLYPHS_PATH,
        family=(state.GLYPHS_PATH.stem if state.GLYPHS_PATH else "font"),
        log=lambda m: log.info(f"[transform] {m}"),
    )


def _strip_injected_axes(vf_path: Path, tags: set) -> Path:
    """Remove axes a transform is about to inject, so re-running is a no-op.

    Pins each named axis to its default and instances it away, leaving the
    outlines exactly where the default sat. Returns the original path
    untouched when there is nothing to strip, so the common case costs one
    fvar read.
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    font = TTFont(str(vf_path))
    if "fvar" not in font:
        return vf_path
    present = [a.axisTag for a in font["fvar"].axes]
    doomed = [t for t in tags if t in present]
    if not doomed:
        return vf_path
    location = {t: font["fvar"].axes[present.index(t)].defaultValue for t in doomed}
    inst = instantiateVariableFont(font, location, inplace=False)
    out = Path(vf_path)
    inst.save(str(out))
    log.info(f"transforms: stripped {len(doomed)} previously-injected "
        f"{'axis' if len(doomed) == 1 else 'axes'} ({', '.join(doomed)}) before re-applying")
    return out


def _apply_transform_chain(vf_path):
    """Run the project's enabled post-build transforms (e.g. SPAC) over a
    freshly-compiled VF, in order, and return the final font path. Applied at
    the single point where each build promotes its output to
    ``VARIABLE_FONT_PATH``, so it covers every build trigger. A transform
    that raises — or returns a path that doesn't exist — is logged and
    skipped, and the chain keeps the last GOOD font, so the preview degrades
    gracefully instead of pointing VARIABLE_FONT_PATH at a missing file.
    Per-transform failures are recorded in ``LAST_TRANSFORM_ERROR`` so the UI
    can surface an enabled-but-failing transform instead of a silent no-op."""
    state.LAST_TRANSFORM_ERROR = None
    if vf_path is None or state.ORIGINAL_PATH is None:
        return vf_path
    _transforms.discover()
    try:
        chain = _transforms.active(state.ORIGINAL_PATH)
    except Exception as e:
        log.info(f"transforms: could not resolve active chain: {e}")
        return vf_path
    out = Path(vf_path)
    errors = []
    # A transform that injects an axis appends it unconditionally, so running
    # the chain over a font that already carries that axis stacks a duplicate.
    # The chain is not always handed a fresh compile — the plain-VF fallback
    # runs it over whatever build_variable_font returned, so a run of failed
    # builds could pile up several SPAC axes (which makes an invalid font). Pin any previously-injected axis out first, so applying
    # the chain twice lands in the same place as applying it once.
    # Only FONT-stage injectors (SPAC) — a source-stage transform's axis
    # (round_corners' ROND) is built into the compile itself; stripping
    # it here would tear the axis out of every build.
    # Ordering and budget: docs/ARCHITECTURE.md §4; Kinetikern before SPAC:
    # docs/decisions/0004-kinetikern-font-stage-phantom-tuples.md.
    injected_tags = {
        t.spec.injected_axis_tag for t, _ in chain
        if getattr(t.spec, "injected_axis_tag", None)
        and getattr(t.spec, "stage", "font") == "font"
    }
    if injected_tags:
        try:
            out = Path(_strip_injected_axes(out, injected_tags))
        except Exception as e:
            log.info(f"transforms: could not strip prior injected axes: {e}")
    # Kinetikern's per-instance spacing goes in first, straight after the
    # compile and the default relocation: phantom-point tuples on the
    # parametric axes, which the SPAC injector then re-encodes with the
    # rest (one small tuple per instance per glyph; a brace in the source
    # would be shredded by the relocation into dozens). It strips its own
    # earlier tuples first, so a re-run of the chain lands in the same place.
    try:
        out = Path(_kinetikern.inject.apply_to_font(
            out, state.ORIGINAL_PATH, log=lambda m: log.info(m)))
    except Exception as e:
        log.info(f"kinetikern: injection failed: {e}")
        errors.append(f"Kinetikern: {e}")
    # Source-stage transforms' font-stage apply() (round_corners' ROND
    # instance stamp) runs LAST: fix_instances regenerates the instance
    # list, and a stamp applied before it would be thrown away.
    chain = sorted(chain, key=lambda tp: getattr(tp[0].spec, "stage", "font") == "source")
    for transform, params in chain:
        try:
            result = transform.apply(out, params, _build_context())
            # Only adopt a returned path that actually exists — a transform
            # (esp. a user script) that returns a bad/unwritten path must not
            # overwrite the last-good font and blank the preview.
            if result is not None and Path(result).exists():
                out = Path(result)
            elif result is not None:
                raise RuntimeError(f"returned a path that does not exist: {result}")
        except Exception as e:
            log.info(f"transform '{transform.spec.id}' failed: {e}")
            errors.append(f"{transform.spec.name}: {e}")
    if errors:
        state.LAST_TRANSFORM_ERROR = "; ".join(errors)
    return _stamp_control_instance_values(out)


def _stamp_control_instance_values(vf_path):
    """Pin per-instance CONTROL-axis values (control sidecar
    ``instance_values``) onto matching fvar named instances — the
    control-axis analogue of SPAC's ``_instance_spac_overrides``.
    Best-effort by name: studio-only rows have no fvar instance to
    stamp (same as SPAC); the UI previews them via the hydrated
    coordinates instead. Runs after the transform chain because
    fix_instances regenerates the instance list."""
    if vf_path is None or state.ORIGINAL_PATH is None:
        return vf_path
    try:
        values = _control_axes.instance_values(state.ORIGINAL_PATH)
    except Exception:
        values = {}
    if not values:
        return vf_path
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(str(vf_path))
        if "fvar" not in font:
            font.close()
            return vf_path
        fvar = font["fvar"]
        axis_by_tag = {a.axisTag: a for a in fvar.axes}
        name_table = font["name"]
        stamped = 0
        for inst in fvar.instances:
            iname = name_table.getDebugName(inst.subfamilyNameID) or ""
            for tag, per_name in values.items():
                axis = axis_by_tag.get(tag)
                if axis is None or iname not in per_name:
                    continue
                v = min(axis.maxValue, max(axis.minValue, float(per_name[iname])))
                inst.coordinates[tag] = v
                stamped += 1
        if stamped:
            font.save(str(vf_path))
            log.info(f"control axes: stamped {stamped} per-instance value(s) "
                  f"onto fvar instances")
        font.close()
    except Exception as exc:
        log.info(f"control axes: instance stamping failed: {exc}")
    return vf_path


def _resolve_active_source() -> Optional[Path]:
    """Re-derive the active build source (``GLYPHS_PATH``) from the CURRENT
    control-axis + grade state, without triggering a build. Sets and returns
    the global.

    The build reads a **shadow** of the original whenever a studio feature adds
    per-glyph deltas the original lacks — control-axis brace layers and/or grade
    braces — otherwise it reads the untouched original. Both features compose
    onto the SAME shadow file (grade braces layered on top of the control
    shadow), so this is the single authority on which source the build reads.

    Sharing this everywhere ``GLYPHS_PATH`` is (re)pointed — load, control-axis
    edits, grade edits — is what makes grade (and authored brace layers) survive
    a restart or an unrelated rebuild. Previously the grade composition lived
    ONLY on the grade-edit path, so a plain reload silently dropped the GRAD
    axis from the built font.

    Serialized on ``_RESOLVE_LOCK``: callers arrive from request threads, the
    debounce timer, the watchdog thread and the job supervisor, and two
    overlapping resolves interleave the shadow/transformed writes.
    """
    with _RESOLVE_LOCK, _gc_paused():
        if state.ORIGINAL_PATH is None:
            return state.GLYPHS_PATH
        state.LAST_GRADE_ERROR = None
        state.LAST_SOURCE_TRANSFORM_ERROR = None

        # 1) control-axis shadow (brace layers). regenerate_shadow returns None
        #    when the sidecar declares no control axes; a shadow with an axis but no
        #    layers compiles to the original's fvar, so we only *use* it when some
        #    axis actually has layers.
        shadow = None
        try:
            shadow = _control_axes.regenerate_shadow(state.ORIGINAL_PATH)
            # Regenerating rewrites the watched source file — the caller triggers
            # the build itself, so suppress the watcher for it.
            _suppress_watcher()
        except Exception as exc:
            log.warning(f"Warning: shadow regeneration failed: {exc}")
        try:
            any_layers = any(ax.get("layers") for ax in _control_axes.list_axes(state.ORIGINAL_PATH))
        except Exception:
            any_layers = False

        # 2) grade braces, composed onto the SAME shadow. fresh_shadow only when
        #    there's no control shadow to build on, so re-runs never stack braces.
        any_grades = False
        try:
            if _grade.list_graded_instances(state.ORIGINAL_PATH):
                grade_shadow = _grade_shadow.apply_grades(
                    state.ORIGINAL_PATH, _grade_instance_coords(), fresh_shadow=(shadow is None)
                )
                if grade_shadow is not None:
                    shadow = grade_shadow
                    any_grades = True
                    _suppress_watcher()
        except Exception as exc:
            state.LAST_GRADE_ERROR = str(exc)
            log.warning(f"Warning: grade shadow generation failed: {exc}")

        # 3) source-stage transforms (round_corners), over a BUILD COPY of the
        #    composed shadow (or the original), so the control and grade braces
        #    generated above go through them together with the masters. Never
        #    the canonical shadow itself: the shadow is regen's STATE, and its
        #    preserve-what-the-designer-drew pass would capture a transformed
        #    brace as a hand drawing and leak it into later untransformed
        #    builds (see docs/ARCHITECTURE.md §3). A failure
        #    keeps the un-transformed source and is surfaced — a silently
        #    sharp font must not pass as ok.
        any_source = False
        src_chain = []
        try:
            _transforms.discover()
            src_chain = _transforms.active(state.ORIGINAL_PATH, stage="source")
            # Deterministic order regardless of sidecar entry order: slant
            # (10) before rounding (50), so rounds land on slanted drawings.
            src_chain.sort(key=lambda tp: getattr(tp[0].spec, "source_priority", 100))
        except Exception as exc:
            log.info(f"transforms: could not resolve source-stage chain: {exc}")
        if src_chain and state.ORIGINAL_PATH.suffix.lower() == ".glyphs":
            current = None
            try:
                base = shadow if shadow is not None else state.ORIGINAL_PATH
                transformed = (_control_axes.shadow_dir_for(state.ORIGINAL_PATH)
                               / "transformed" / state.ORIGINAL_PATH.name)
                transformed.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(base, transformed)
                from glyphsLib import GSFont as _GSFont
                font = _GSFont(str(transformed))
                ctx = _build_context()
                for current, params in src_chain:
                    current.apply_to_source(font, params, ctx)
                from .source_font import save_font_atomically as _save_atomically
                _save_atomically(font, transformed)
                # Re-arm the watcher suppression for OUR transformed-copy
                # write: the window set after the shadow regen above can
                # expire during a multi-second transform pass, and an
                # unsuppressed self-write re-fires the watcher as an external
                # save → resolve → write → … (the T0.7 storm).
                _suppress_watcher()
                shadow = transformed
                any_source = True
            except Exception as exc:
                name = current.spec.id if current is not None else "source-stage transforms"
                state.LAST_SOURCE_TRANSFORM_ERROR = f"{name}: {exc}"
                log.warning(f"Warning: source transform failed, building without it: "
                      f"{state.LAST_SOURCE_TRANSFORM_ERROR}")

        state.GLYPHS_PATH = shadow if (shadow is not None and (any_layers or any_grades or any_source)) \
            else state.ORIGINAL_PATH
        return state.GLYPHS_PATH


def _run_build():
    """One build pass: try the avar2 build first so the preview reflects the
    actual avar2 table the browser will apply. Falls back to a plain
    variable-font build if the avar2 build fails (e.g. CSV is mid-edit,
    gftools is missing, or the user hasn't authored mappings yet) so the rows
    view always has *some* font to show.
    """

    # A source that no longer parses is a HARD build failure. Both paths
    # below can look successful from a stale snapshot/artifact, which used
    # to publish a degraded font and report "ok" — leaving the build gate
    # open while every mutating route 500s on the same parse error. Fail
    # here, before either builder runs, so the last-good font keeps
    # serving and LAST_BUILD_STATUS closes the gate.
    try:
        # Cached loader: memoized by (mtime, size), so a saved/edited source
        # is re-read (the watcher path always touches the file) while a
        # rebuild of unchanged bytes costs nothing. The font object is not
        # mutated here — this is a read-only probe.
        _source_font.load_source_cached(state.GLYPHS_PATH)
    except Exception as exc:
        flat = " | ".join(ln for ln in str(exc).splitlines() if ln.strip())
        log.info(f"Build input cannot be read: {flat}")
        _record_build_failure({
            "success": False,
            "error": "The source file cannot be read",
            "details": flat[-350:],
        })
        return False

    # Try the avar2 build first. _perform_avar2_build manages BUILDING itself.
    # GC paused for the whole pass: the avar2 pipeline (update_config,
    # the instancer relocation over a ~9MB gvar, fix/STAT) churns as many
    # temporaries as the source chain and showed the same
    # gc_collect_main-dominated profile once the heap had grown.
    # Cleared on every build: a stale relocation error would keep the studio
    # reporting "degraded" after the ORIGIN row was put back.
    state.LAST_ORIGIN_RELOCATION_ERROR = None
    state.LAST_ORIGIN_RELOCATION_MEMO = None
    state.LAST_ORIGIN_AUTHORED_AVAR = None
    with _gc_paused():
        avar2_result = _perform_avar2_build()
    if avar2_result.get("success"):
        state.LAST_AVAR2_ERROR = None
        log.info(f"Avar2 font built: {avar2_result['font_path']}")
        return True

    # Record WHY, so /api/health can surface that the served font is
    # the plain fallback (no avar2 table, no mapped axes) — otherwise
    # everything reports "ok" while the preview quietly loses its
    # user-facing axes.
    _avar2_details = str(avar2_result.get("details") or "").strip()
    state.LAST_AVAR2_ERROR = (
        f"{avar2_result.get('error')}"
        + (f": …{_avar2_details[-400:]}" if _avar2_details else "")
    )
    log.info(f"Avar2 build skipped/failed ({avar2_result.get('error')}); "
        f"falling back to plain variable-font build.")

    state.BUILDING = True
    try:
        log.info(f"Building font from {state.GLYPHS_PATH}...")
        state.VARIABLE_FONT_PATH = _apply_transform_chain(
            build_variable_font(state.GLYPHS_PATH, state.BUILD_DIR, use_fontc=state.USE_FONTC)
        )
        state.LAST_BUILD_TIME = time.time()
        # The fallback succeeded — a working font is being served, so the
        # stale-banner state from the avar2 failure has to be cleared,
        # otherwise the UI keeps showing "Build failed" indefinitely.
        state.LAST_BUILD_STATUS = "ok"
        state.LAST_BUILD_ERROR = None
        log.info(f"Font built successfully: {state.VARIABLE_FONT_PATH}")
        return True
    except Exception as e:
        log.info(f"Build failed: {e}")
        # BOTH paths failed — the avar2 attempt and the plain fallback.
        # Report the compiler error (the actionable one: the source
        # itself can't compile) with the avar2 failure as context,
        # instead of leaving only the avar2 error in the banner. The
        # compiler output is multiline; collapse it so the banner stays
        # one line, and keep the tail where the actual error lives.
        flat = " | ".join(ln for ln in str(e).splitlines() if ln.strip())
        _record_build_failure({
            "success": False,
            "error": "Font build failed",
            "details": f"…{flat[-350:]} (avar2 build also failed: {avar2_result.get('error')})",
        })
        return False
    finally:
        state.BUILDING = False


def _record_build_failure(result: Dict) -> Dict:
    """Stamp the global last-build status as failed and return the dict."""
    state.LAST_BUILD_STATUS = "failed"
    msg = result.get("error") or "build failed"
    detail = result.get("details")
    state.LAST_BUILD_ERROR = f"{msg}: {detail}" if detail else msg
    return result


def _builder_env(default_overrides: Optional[Dict[str, float]] = None) -> Dict[str, str]:
    """Environment for the gftools-builder subprocess.

    Resolves compilers against THIS python env first (ninja steps call
    gftools-* scripts from PATH), with build/_shims/ in front so our
    patched gftools-gen-avar2 shadows the venv console script (fvar
    axis dedup + declared ranges + export rebasing — see the shim).

    Also hands the studio's declared axis ranges to the shim: created
    axes (opsz/wght…) get their declared min/DEFAULT/max instead of
    upstream's range-from-in-values with default=min — default=min
    makes the lowest-mapped instance the un-remappable avar2 origin.
    ``default_overrides`` ({fvar tag: value}) replaces specific axes'
    DEFAULT — the export-with-default build uses it to make the
    requested combination the compiled origin.
    """
    build_env = os.environ.copy()
    shims_dir = Path(__file__).parent / "build" / "_shims"
    build_env["PATH"] = (
        str(shims_dir)
        + os.pathsep
        + str(Path(sys.executable).parent)
        + os.pathsep
        + build_env.get("PATH", "")
    )
    _ranges = _declared_axis_ranges(default_overrides)
    if _ranges:
        build_env["AVAR2_STUDIO_AXIS_RANGES"] = json.dumps(_ranges)
    return build_env


def _declared_axis_ranges(default_overrides: Optional[Dict[str, float]] = None) -> Dict[str, list]:
    """{fvar tag: [min, default, max]} for the studio's CREATED axes.

    Upstream gen-avar2 derives a created axis's range purely from the
    mapping's in: values and sets default=min, which makes the lowest-mapped
    instance the un-remappable origin of the space. These declared triples
    put the default where the designer said it is. Shared by the subprocess
    shim (via env) and the in-process build (directly).
    """
    key = _declared_ranges_cache_key(default_overrides)
    cached = state._DECLARED_RANGES_CACHE.get(key) if key is not None else None
    if cached is not None:
        return {t: list(v) for t, v in cached.items()}
    try:
        _meta = _load_axis_metadata() or {}
    except Exception:
        return {}
    _ranges: Dict[str, list] = {}
    for _col, _m in _meta.items():
        _tag = (_m.get("registered_tag") or "").strip()
        if not _tag or _m.get("is_parametric"):
            continue
        _lo, _hi = _m.get("min"), _m.get("max")
        if _lo is None or _hi is None:
            continue
        # Same fallback chain as /api/avar2/axes: explicit metadata default,
        # else the registered-axis convention (wght 400, opsz 72, …), else the
        # minimum — clamped into range so the triple is never rejected.
        _d = _m.get("default")
        if _d is None:
            _d = TRADITIONAL_AXIS_DEFAULTS.get(_tag.lower(), _lo)
        _d = max(float(_lo), min(float(_hi), float(_d)))
        _ranges[_tag] = [float(_lo), _d, float(_hi)]
    if default_overrides:
        for _tag, _val in default_overrides.items():
            if _tag in _ranges:
                _lo, _, _hi = _ranges[_tag]
                _ranges[_tag] = [_lo, max(_lo, min(_hi, float(_val))), _hi]
    if key is not None:
        state._DECLARED_RANGES_CACHE.clear()
        state._DECLARED_RANGES_CACHE[key] = _ranges
    return _ranges


def _declared_ranges_cache_key(default_overrides) -> Optional[tuple]:
    """Version key for the memo, or None when an input has no on-disk
    version (no source bound yet, metadata path unset): a None part would
    make every such call share one key and hand back a stale result —
    so those calls compute without caching."""
    parts = []
    for p in (_get_avar2_metadata_path(), state.GLYPHS_PATH):
        try:
            st = Path(p).stat()
        except (OSError, TypeError):
            return None
        parts.append((str(p), st.st_mtime_ns, st.st_size))
    ov = tuple(sorted((str(k), float(v)) for k, v in (default_overrides or {}).items()))
    return (tuple(parts), ov)



# gvar stores each glyph's tuple headers before its data behind a u16
# offset. Relocating the default to an interior point splits every region
# around the new origin, and a twin-heavy font (slant × rounding masters)
# can push its heaviest glyphs past that 64 KB block — fontTools then fails
# the save with a bare "int too big to convert". The native SPAC injector
# already prunes sub-visible splinter tuples for the same reason; this is
# the Python-side equivalent for the relocation step.
GVAR_PRUNE_TOL = 2.0   # font units: a tuple moving nothing more than this is sub-visible


def _tuple_max_delta(tv) -> float:
    return max((abs(x) + abs(y) for c in tv.coordinates if c is not None for x, y in [c]), default=0.0)


def _fit_gvar_headers(font, tol: float = GVAR_PRUNE_TOL, log=None, headroom: int = 64) -> dict:
    """Make every glyph's gvar header block fit the u16 offset, dropping its
    smallest tuples (max |delta| ≤ ``tol``) only where it overflows.
    Returns {glyph: tuples removed}. Raises RuntimeError with a readable
    message when a glyph cannot be made to fit without dropping visible
    movement.

    fontTools packs offsetToData = 4 + Σ(tuple header bytes) as a u16; a
    tuple's header is 4 bytes + its private peak (2 per axis unless the
    peak is a shared tuple) + its intermediate region (4 per axis when
    present). Only the headers count — the delta data lives past the
    offset — so the budget is summed per tuple instead of recompiling
    the glyph after every removal (quadratic on twin-heavy glyphs)."""
    from fontTools.ttLib.tables._g_v_a_r import compileGlyph_
    gvar = font["gvar"]
    axis_tags = [a.axisTag for a in font["fvar"].axes]
    limit = 0xFFFF - headroom

    def header_bytes(tv) -> int:
        # Upper bound: every peak counted as private. The shared-peak set
        # is recomputed by fontTools at save time from whatever tuples
        # remain, so a peak that is shared now may be private after the
        # prune (and grow by 2 bytes per axis); budgeting without sharing
        # cannot be beaten by that.
        coord = tv.compileCoord(axis_tags)
        inter = tv.compileIntermediateCoord(axis_tags)
        return 4 + len(coord) + (len(inter) if inter else 0)

    removed = {}
    for name, variations in gvar.variations.items():
        sizes = [header_bytes(tv) for tv in variations]
        total = 4 + sum(sizes)
        if total <= limit:
            continue
        order = sorted(range(len(variations)), key=lambda i: _tuple_max_delta(variations[i]))
        drop = set()
        for i in order:
            if total <= limit:
                break
            if _tuple_max_delta(variations[i]) > tol:
                break
            drop.add(i)
            total -= sizes[i]
        if total > limit:
            raise RuntimeError(
                f"glyph '{name}' has too many variation tuples for OpenType's per-glyph gvar "
                f"limit after relocating the default ({len(variations)} tuples; dropping the "
                f"{len(drop)} sub-visible ones was not enough). Export at the resting style, or "
                f"reduce twin masters (disable slant or rounding) for this export.")
        variations[:] = [tv for i, tv in enumerate(variations) if i not in drop]
        removed[name] = len(drop)
        # one real compile as the safety net (headers are what overflow; the
        # delta data past the offset never does)
        compileGlyph_(2, variations, 0, axis_tags, {}, optimizeSize=False)
    if removed and log:
        log(f"gvar: pruned {sum(removed.values())} sub-visible tuple(s) (≤{tol:g} u) across "
            f"{len(removed)} glyph(s) so the relocated font fits the per-glyph header limit")
    return removed


RELOCATION_MEMO_DIRNAME = ".relocation-memo"
RELOCATION_MEMO_KEEP = 2   # relocated fonts are ~10 MB each on a twin-heavy source


def _font_content_digest(path: Path) -> str:
    """sha256 over the font's table data, excluding ``head``.

    ``head.modified`` is a timestamp, so hashing the file bytes would miss on
    every rebuild of an unchanged source. Everything relocation reads —
    ``glyf``/``gvar``/``fvar``/``HVAR`` — is covered, and so is everything the
    fit prunes.
    """
    digest = hashlib.sha256()
    font = TTFont(str(path))
    try:
        for tag in sorted(t for t in font.keys() if t not in ("GlyphOrder", "head")):
            digest.update(tag.encode("ascii"))
            digest.update(font.getTableData(tag))
    finally:
        font.close()
    return digest.hexdigest()


def _relocation_memo_key(font_path: Path, limits: Dict[str, tuple]) -> str:
    """Identity of a relocation request: the compiled font's content, the
    exact (min, new default, max) window per axis, and the pruning tolerance
    (a different tolerance prunes different tuples, so it is part of the key)."""
    payload = json.dumps({
        "font": _font_content_digest(font_path),
        "limits": {tag: [float(lo), float(default), float(hi)]
                   for tag, (lo, default, hi) in sorted(limits.items())},
        "tol": GVAR_PRUNE_TOL,
        "format": 1,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _memo_lookup(memo_dir: Path, key: str):
    """``("font", path)``, ``("error", message)`` or ``None``."""
    font_path = memo_dir / f"{key}.ttf"
    if font_path.exists():
        return ("font", font_path)
    error_path = memo_dir / f"{key}.json"
    if error_path.exists():
        try:
            payload = json.loads(error_path.read_text(encoding="utf-8"))
            return ("error", payload["error"], payload.get("authored_avar"))
        except (OSError, ValueError, KeyError):
            return None   # a half-written entry is a miss, never an invented error
    return None


def _memo_store(memo_dir: Path, key: str, source: Optional[Path],
                error: Optional[str], extra: Optional[Dict] = None) -> None:
    """Record the outcome for ``key`` atomically, then evict the oldest.

    A failure's payload also carries the authored-mapping table the degraded
    oracle needs, so a memo hit does not silently lose it.
    """
    memo_dir.mkdir(parents=True, exist_ok=True)
    try:
        if error is None and source is not None:
            tmp = memo_dir / f".{key}.ttf.tmp"
            shutil.copyfile(source, tmp)
            os.replace(tmp, memo_dir / f"{key}.ttf")
        else:
            tmp = memo_dir / f".{key}.json.tmp"
            tmp.write_text(json.dumps({"error": error,
                                       "authored_avar": (extra or {}).get("authored_avar")}),
                           encoding="utf-8")
            os.replace(tmp, memo_dir / f"{key}.json")
    except OSError as exc:
        # A memo that cannot be written costs the next build its shortcut;
        # it must never cost this one its font.
        log.info("  relocation: could not memoise this attempt: %s", exc)
        return
    _memo_evict(memo_dir)


def _memo_evict(memo_dir: Path, keep: int = RELOCATION_MEMO_KEEP) -> None:
    for suffix in (".ttf", ".json"):
        entries = sorted(memo_dir.glob(f"*{suffix}"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in entries[keep:]:
            try:
                stale.unlink()
            except OSError as exc:
                log.info("  relocation: could not evict %s: %s", stale.name, exc)


def _build_in_process(config_path: Path, workdir: Path, fontc_path: str,
                      default_overrides: Optional[Dict[str, float]] = None,
                      relocate_default: Optional[bool] = None,
                      relocate_target: Optional[Dict[str, float]] = None,
                      relocation_report: Optional[Dict[str, Optional[str]]] = None,
                      relocation_memo_dir: Optional[Path] = None) -> Path:
    """The gftools-builder pipeline, without gftools-builder.

    Profiling a rebuild put fontc at 0.06s and ``gftools builder`` at 9.5s:
    after the compile, ninja spawned four separate Python processes (fix,
    STAT, avar2, fvar instances), each paying interpreter start-up and a
    fontTools import, serially. None of that was compilation. This runs the
    same four library functions the CLIs wrap, in one process, and hands back
    the font gftools would have written — same name, same tables — so
    everything downstream (transform chain, promotion to VARIABLE_FONT_PATH)
    is untouched.

    Fidelity, step by step, each mirroring the CLI it replaces:
      - fontc gets the flags gftools rewrites from the recipe's fontmake
        filters (flatten components, decompose transformed components).
      - fix_font returns a deepcopy; it is saved and re-read so every later
        step sees exactly what the CLI chain would have loaded from disk.
      - gen_avar2's gen_fvar_axes is patched with the same fix build/_shims
        applies: drop in: axes the font already has (the upstream membership
        bug that duplicated every parametric axis) and give created axes their
        declared min/default/max.
      - Per-font sections are passed as LISTS, not filename-keyed dicts, so
        nothing reaches for TTFont.reader.file.name.
    """
    import yaml
    from fontTools.ttLib import TTFont as _TTFont
    from gftools.fix import fix_font
    from gftools.scripts import gen_avar2 as _gen_avar2
    from gftools.scripts.gen_fvar_instances import gen_fvar_instances
    from gftools.stat import gen_stat_tables_from_config

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources = config.get("sources") or []
    if not sources:
        raise RuntimeError("config has no sources")
    source = Path(sources[0])
    if not source.is_absolute():
        source = (config_path.parent / source).resolve()

    # The target filename is the key every per-font section shares. gftools
    # derives it from family name + source axes; reading it off the config is
    # exact by construction and avoids re-implementing the naming.
    keys = [k for k in (config.get("avar2") or {}) if "SC[" not in k] \
        or [k for k in (config.get("stat") or {}) if "SC[" not in k]
    if not keys:
        raise RuntimeError("config has no avar2/stat entry to name the font from")
    name = keys[0]
    out_dir = workdir.parent / "fonts" / "variable"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name

    # 0.5. Default-origin master: a mapping row whose in: sits entirely at
    # the user-axis defaults declares the resting appearance, which avar2
    # cannot remap; step 3.5 relocates the fvar defaults to its out: before
    # gen-avar2. See docs/decisions/0001-build-time-default-relocation.md
    # (why relocation, not an inserted master: 0007) and
    # tests/test_avar2_default_relocation.py.
    avar2_cfg_early = (config.get("avar2") or {}).get(name)
    compile_source = source
    default_rows = []
    if avar2_cfg_early:
        declared_for_defaults = _declared_axis_ranges(default_overrides)

        def _is_default_row(m):
            ins = m.get("in") or {}
            if not ins:
                return False
            for tag, v in ins.items():
                trip = declared_for_defaults.get(tag)
                if not trip:
                    return False
                try:
                    if abs(float(v) - float(trip[1])) > 1e-6:
                        return False
                except (TypeError, ValueError):
                    return False
            return True

        default_rows = [m for m in avar2_cfg_early if _is_default_row(m)]
        # Loud warning for the "resting dip": rows map user-axis styles
        # but none sits at the axis defaults, so nothing anchors the
        # resting state — the font rests at the compiled origin, and the
        # default location pops toward the nearest mapped stop the
        # moment a slider moves. Only meaningful when declared defaults
        # exist and at least one row maps a declared axis.
        state.LAST_ORIGIN_WARNING = None
        maps_declared_axis = any(
            any(tag in declared_for_defaults for tag in (m.get("in") or {}))
            for m in avar2_cfg_early)
        if declared_for_defaults and maps_declared_axis and not default_rows:
            defaults_desc = ", ".join(
                f"{tag} {trip[1]:g}" for tag, trip in sorted(declared_for_defaults.items()))
            state.LAST_ORIGIN_WARNING = (
                f"No instance row sits at the axis defaults ({defaults_desc}) — the "
                "font rests at the compiled origin, not at any authored style. Add a "
                "row at the defaults (it becomes the ORIGIN row), or move an axis "
                "default onto a mapped stop.")
        if len(default_rows) > 1:
            # The config generator's effective-location dedupe normally
            # collapses these (keeping the first, warning by instance
            # name); this belt covers hand-edited configs — two rows
            # both claiming the resting state must fail loudly, not
            # silently pick one.
            raise RuntimeError(
                "multiple mapping rows sit at the axis defaults — only one may "
                "define the resting state; their out locations: "
                + "; ".join(str(m.get("out")) for m in default_rows))

    # 1. compile — the only step that was ever fast
    cmd = [fontc_path, "-o", str(out), str(compile_source),
           "--flatten-components", "--decompose-transformed-components"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(workdir))
    if res.returncode != 0 or not out.exists():
        raise RuntimeError("fontc failed: " + (res.stderr or res.stdout or "no output")[-1000:])

    # 2. fix  (gftools-fix-font -o out in)
    font = _TTFont(str(out))
    font = fix_font(font)
    font.save(str(out))
    font.close()

    # 3. STAT  (gftools-gen-stat --inplace --src <stat section>)
    stat_cfg = (config.get("stat") or {}).get(name)
    if stat_cfg:
        font = _TTFont(str(out))
        gen_stat_tables_from_config(stat_cfg, [font])
        font.save(str(out))
        font.close()

    # 3.5. Default relocation (see step 0.5): move the parametric fvar
    # defaults to the default row's out location on the avar-free font —
    # the row then compiles as a true identity in step 4 and every other
    # row anchors to the real resting state. ON by default: with the ORIGIN
    # row dropped every tent is zero-anchored at the input default and the
    # neighbourhood collapses toward the compiled corner (the valley).
    # AVAR2_RELOCATE_BUILD_DEFAULT=0 restores the small-but-valleyed font.
    # An explicit ``relocate_default`` parameter wins over the env; an
    # explicit ``relocate_target`` (parametric tags → exact location) wins
    # over the default row's out: — the set-default export passes P (the
    # requested style's authored parametric location) so the shipped font
    # rests there losslessly (decisions/0001+0007).
    if relocate_default is None:
        relocate_default = os.environ.get(
            "AVAR2_RELOCATE_BUILD_DEFAULT", "1").lower() in ("1", "true", "yes")
    relocate_at_build = relocate_default
    target = relocate_target if relocate_target is not None else (
        (default_rows[0].get("out") or {}) if (avar2_cfg_early and default_rows) else {})
    relocation_error = None
    if relocate_at_build and target:
        from fontTools.varLib import instancer as _instancer
        font = _TTFont(str(out))
        fvar_by_tag = {a.axisTag: a for a in font["fvar"].axes}
        limits = {}
        for tag, v in target.items():
            ax = fvar_by_tag.get(tag)
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if ax is None or abs(v - ax.defaultValue) < 1e-6:
                continue
            if not (ax.minValue <= v <= ax.maxValue):
                raise RuntimeError(
                    f"default-location mapping row puts {tag} at {v}, outside "
                    f"its range [{ax.minValue}, {ax.maxValue}]")
            limits[tag] = (ax.minValue, v, ax.maxValue)
        memo_dir = (Path(relocation_memo_dir) if relocation_memo_dir
                    else out.parent / RELOCATION_MEMO_DIRNAME)
        memo_key = None
        memo = None
        if limits:
            # Relocating the origin is the expensive step (~27 s on Crispy, and
            # it densifies gvar ~17x), and its result depends only on the
            # compiled font's content and the target window. So keep the result:
            # a CSV row edit, a mapping change, or any other config touch that
            # leaves the compile alone then costs nothing. Successes AND
            # failures are memoised — the failure path is the one the designer
            # hits repeatedly while walking the ORIGIN row back.
            memo_key = _relocation_memo_key(out, limits)
            memo = _memo_lookup(memo_dir, memo_key)
            if relocation_report is not None:
                relocation_report["memo"] = "hit" if memo is not None else "miss"
            if memo is not None and memo[0] == "font":
                font.close()
                font = None
                shutil.copyfile(memo[1], out)
                log.info(
                    "  relocation: memo hit — reusing the relocated font for %s "
                    "(%d KB); instancer and header-fit skipped", target,
                    memo[1].stat().st_size // 1024)
            elif memo is not None:
                font.close()
                font = None
                relocation_error = memo[1]
                relocate_at_build = False
                if relocation_report is not None:
                    relocation_report["authored_avar"] = memo[2]
                log.info(
                    "  relocation: memo hit — the cached failure still stands for "
                    "%s, instancer skipped: %s", target, relocation_error)
            else:
                # A relocation that cannot fit gvar's per-glyph header ceiling is
                # NOT a reason to lose the avar2 font: keep the un-relocated build
                # (all axes, avar2 table, the compiled default) and report it, so
                # the preview keeps its sliders and the ORIGIN row can be put
                # back. Nothing was written yet — the instancer mutates in memory
                # and the save happens after the fit.
                try:
                    _instancer.instantiateVariableFont(font, limits, inplace=True,
                                                       updateFontNames=False)
                    _fit_gvar_headers(font, log=log.info)
                except Exception as exc:  # reported, never swallowed
                    relocation_error = " ".join(str(exc).split())
                    log.warning(
                        "  relocation: cannot move the default to %s — keeping the "
                        "un-relocated avar2 font: %s", target, relocation_error)
                    font.close()
                    relocate_at_build = False
                    font = None
                    # The served table is anchored on the compiled origin now,
                    # so /api/mapped-location needs the authored one (S1(c)).
                    try:
                        authored = _authored_mapping_table(out, avar2_cfg_early,
                                                           target)
                    except Exception as exc:  # never fail a build over this
                        authored = None
                        log.warning("  relocation: could not model the authored "
                                    "mapping for the degraded preview: %s", exc)
                    if relocation_report is not None:
                        relocation_report["authored_avar"] = authored
                    _memo_store(memo_dir, memo_key, None, relocation_error,
                                {"authored_avar": authored})
        if font is not None:
            font.save(str(out))
            font.close()
            if memo is None and memo_key is not None:
                _memo_store(memo_dir, memo_key, out, None)

    # 4. avar2  (gftools-gen-avar2 --inplace in <avar2 section>), shim applied
    avar2_cfg = avar2_cfg_early
    if not relocate_at_build and default_rows:
        # The default-located row cannot be stored (it is the VarStore
        # base); drop it so the other stops stay exact and the preview
        # rests at the compiled origin. See
        # docs/decisions/0001-build-time-default-relocation.md.
        dropped = {id(m) for m in default_rows}
        avar2_cfg = [m for m in avar2_cfg_early if id(m) not in dropped]
        log.info("  avar2: default-location row(s) excluded from the preview "
              "table (build-time relocation off); mapped stops stay exact, "
              "the preview rests at the compiled origin")
        # (global LAST_ORIGIN_WARNING is declared earlier in this function.)
        state.LAST_ORIGIN_WARNING = (
            "Build-time default relocation is off: the preview font rests at "
            "the compiled origin, not at the ORIGIN row's style. Every mapped "
            "stop is exact; use the export's \u201cset default\u201d to ship a font "
            "resting on the ORIGIN row.")
    if avar2_cfg:
        declared = _declared_axis_ranges(default_overrides)
        _orig = _gen_avar2.gen_fvar_axes

        def _fixed_gen_fvar_axes(font, mapping):
            existing = {str(a.axisTag) for a in font["fvar"].axes}
            filtered = [{**m, "in": {k: v for k, v in (m.get("in") or {}).items() if k not in existing}}
                        for m in mapping]
            result = _orig(font, filtered)
            for a in font["fvar"].axes:
                tag = str(a.axisTag)
                if tag in existing or tag not in declared:
                    continue
                try:
                    lo, d, hi = (float(v) for v in declared[tag])
                except (TypeError, ValueError):
                    continue
                if lo <= d <= hi:
                    a.minValue, a.defaultValue, a.maxValue = lo, d, hi
            return result

        _gen_avar2.gen_fvar_axes = _fixed_gen_fvar_axes
        try:
            font = _TTFont(str(out))
            _gen_avar2.gen_avar2_mapping(font, avar2_cfg)
            font.save(str(out))
            font.close()
        finally:
            _gen_avar2.gen_fvar_axes = _orig

    # 5. fvar instances  (gftools-gen-fvar-instances --inplace in <section>)
    # gen_fvar_instances print()s a "missing axis, using default" line per
    # instance per axis. The subprocess swallowed those; in-process they
    # would flood the server log on every rebuild, so swallow them here too.
    inst_cfg = (config.get("fvarInstances") or {}).get(name)
    if inst_cfg:
        import contextlib
        import io
        font = _TTFont(str(out))
        with contextlib.redirect_stdout(io.StringIO()):
            gen_fvar_instances(font, inst_cfg)
        font.save(str(out))
        font.close()

    if relocation_report is not None:
        relocation_report["error"] = relocation_error
    return out


def _build_status(relocation_error: Optional[str]) -> str:
    """The status a *successful* build reports.

    "degraded" when the avar2 font was served but its default could not be
    relocated onto the ORIGIN row — the preview keeps every axis and the
    avar2 table, but it does not rest at the ORIGIN row's style. Deliberately
    not "failed": that closes the build gate and would stop the designer
    nudging the ORIGIN row back (round-11 review, S1(b)).
    """
    return "degraded" if relocation_error else "ok"


def _perform_avar2_build() -> Dict:
    """Build the avar2 variable font in-process.

    Returns a dict with ``success: bool`` and either ``font_path`` (on success)
    or ``error`` + ``details`` (on failure). Designed to be called by both the
    Flask endpoint and the watchdog-triggered rebuild path, so build-on-save
    doesn't have to round-trip through HTTP.

    On failure ``VARIABLE_FONT_PATH`` is left untouched, so ``/api/font`` keeps
    serving the last-good font and the UI can show a stale-build banner driven
    by ``LAST_BUILD_STATUS`` in ``/api/health``.

    Sets the BUILDING flag for its lifetime; raises nothing — all failures
    come back as a dict so callers can surface them however they want.
    """

    if state.BUILDING:
        return {"success": False, "error": "Build already in progress"}

    preview_csv = _get_preview_csv_path()
    if not preview_csv or not preview_csv.exists():
        return _record_build_failure({"success": False, "error": "Preview CSV not found"})

    avar2_font_dir = _get_avar2_font_dir()
    if not avar2_font_dir:
        return _record_build_failure({"success": False, "error": "Could not create avar2 font directory"})

    state.BUILDING = True
    try:
        preview_config_path = _get_preview_config_path()
        if not preview_config_path or not preview_config_path.exists():
            return _record_build_failure({"success": False, "error": "Preview config not found", "details": str(preview_config_path)})
        config_to_update = preview_config_path

        workdir = _get_preview_dir()
        try:
            from .build import config_generator
        except ImportError as e:
            # Should never happen in a normal install — fall through to
            # the caller's plain-VF fallback instead of letting the
            # ImportError escape and break the whole startup build.
            return _record_build_failure({
                "success": False,
                "error": "avar2 config_generator module not importable",
                "details": str(e),
            })
        try:
            config_generator.update_config(csv_path=preview_csv, config_path=config_to_update, backup=False, source_path=state.GLYPHS_PATH)
        except Exception as e:
            return _record_build_failure({"success": False, "error": "Failed to update config", "details": str(e)})

        build_env = _builder_env()

        fontc_path = shutil.which("fontc", path=build_env["PATH"])
        if not fontc_path:
            return _record_build_failure({"success": False, "error": "fontc not found in PATH"})

        # Same steps gftools-builder would run, in this process. The subprocess
        # builder is kept behind AVAR2_STUDIO_GFTOOLS_BUILDER=1 as a safety valve
        # for a build the in-process path gets wrong — set it and compare.
        if os.environ.get("AVAR2_STUDIO_GFTOOLS_BUILDER") == "1":
            builder_cmd = ["gftools", "builder", "--experimental-fontc", fontc_path, str(config_to_update.resolve())]
            result = subprocess.run(builder_cmd, capture_output=True, text=True, cwd=str(workdir), env=build_env)
            if result.returncode != 0:
                return _record_build_failure({
                    "success": False,
                    "error": "Font build failed",
                    # TAIL, not head: builder stderr opens with pages of
                    # glyphsLib INFO noise; the actual error is at the end.
                    "details": (result.stderr or result.stdout or "No error details")[-1000:],
                })
        else:
            try:
                _report: Dict[str, Optional[str]] = {}
                _build_in_process(config_to_update, workdir, fontc_path,
                                  relocation_report=_report,
                                  relocation_memo_dir=state.RELOCATION_MEMO_DIR)
            except Exception as e:
                return _record_build_failure({
                    "success": False,
                    "error": "Font build failed",
                    "details": str(e)[-1000:],
                })

            class _R:  # the post-build code reads .stdout for its error text
                stdout = ""
            result = _R()
            if _report.get("error"):
                state.LAST_ORIGIN_RELOCATION_ERROR = _report["error"]
            state.LAST_ORIGIN_RELOCATION_MEMO = _report.get("memo")
            state.LAST_ORIGIN_AUTHORED_AVAR = _report.get("authored_avar")
        project_fonts_dir = workdir.parent / "fonts" / "variable"
        produced = sorted(project_fonts_dir.glob("*.ttf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not produced:
            return _record_build_failure({
                "success": False,
                "error": "Built font not found",
                "details": f"No .ttf in {project_fonts_dir}. gftools output: {(result.stdout or '')[:500]}",
            })
        built_font = produced[0]

        avar2_font_dir.mkdir(parents=True, exist_ok=True)
        font_file = avar2_font_dir / built_font.name
        shutil.move(str(built_font), str(font_file))

        # Sanity: a compiled font must never carry duplicate fvar tags.
        # gen-avar2's axis-append bug produced exactly that (each
        # parametric axis twice → deltas apply twice → glyphs render
        # wide, looks like a phantom spacing transform). Fail the avar2
        # build loudly so the plain-VF fallback serves instead of a
        # corrupted font.
        try:
            _tags = [a.axisTag for a in TTFont(str(font_file))["fvar"].axes]
        except Exception:
            _tags = []
        _dupes = sorted({t for t in _tags if _tags.count(t) > 1})
        if _dupes:
            return _record_build_failure({
                "success": False,
                "error": "avar2 build produced duplicate fvar axes",
                "details": f"duplicated tags: {', '.join(_dupes)} — "
                           "gen-avar2 appended in: axes that already exist "
                           "(shim missing or bypassed?)",
            })

        try:
            if project_fonts_dir.exists() and not any(project_fonts_dir.iterdir()):
                project_fonts_dir.rmdir()
            parent_fonts = project_fonts_dir.parent
            if parent_fonts.exists() and not any(parent_fonts.iterdir()):
                parent_fonts.rmdir()
        except OSError:
            pass

        state.VARIABLE_FONT_PATH = _apply_transform_chain(font_file)
        state.LAST_BUILD_TIME = time.time()
        state.LAST_BUILD_ERROR = None
        state.LAST_BUILD_STATUS = _build_status(state.LAST_ORIGIN_RELOCATION_ERROR)
        if state.LAST_ORIGIN_RELOCATION_ERROR:
            # The avar2 font is served and every axis is there; the avar2 step
            # itself did not fail.
            state.LAST_AVAR2_ERROR = None
        return {"success": True, "font_path": str(state.VARIABLE_FONT_PATH),
                "origin_relocation_error": state.LAST_ORIGIN_RELOCATION_ERROR,
                "origin_relocation_memo": state.LAST_ORIGIN_RELOCATION_MEMO,
                "origin_authored_avar": state.LAST_ORIGIN_AUTHORED_AVAR}

    finally:
        state.BUILDING = False


def _pick_variable_font(output_dir: Path) -> Path:
    """The variable font fontmake/fontc just wrote under ``output_dir``.

    ``rglob`` because fontmake may nest it. The relocation memo is skipped:
    it is a tree of relocated avar2 fonts from earlier compiles sitting in
    the same build dir, and a plain build must never hand one back as its own
    output (round-11 review). Newest wins when there is more than one.
    """
    candidates = [p for p in Path(output_dir).rglob("*.ttf")
                  if RELOCATION_MEMO_DIRNAME not in p.parts]
    if not candidates:
        raise RuntimeError(f"No variable font found in {output_dir}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def build_variable_font(glyphs_path: Path, output_dir: Path, use_fontc: bool = True) -> Path:
    """
    Build variable font from Glyphs file using fontc (with fontmake fallback).

    Args:
        glyphs_path: Path to Glyphs file
        output_dir: Directory to output the font
        use_fontc: If True, try fontc first, fallback to fontmake on failure

    Returns path to the built variable font TTF.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Try fontc first if enabled
    if use_fontc:
        # Use fontc from PATH (pip-installed)
        fontc_cmd = shutil.which("fontc")
        if fontc_cmd:
            # Output filename derives from the .glyphs filename so the tool
            # works on any font, not just Crispy.
            output_file = output_dir / f"{glyphs_path.stem}-VF.ttf"
            cmd = [
                fontc_cmd,
                "--output-file", str(output_file),
                str(glyphs_path)
            ]

            log.info(f"Building variable font with fontc: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=glyphs_path.parent
            )

            if result.returncode == 0 and output_file.exists():
                log.info("✅ fontc compilation successful")
                return output_file
            else:
                error_msg = result.stderr or result.stdout or "Unknown error"
                log.info(f"fontc failed, falling back to fontmake: {error_msg}")
        else:
            log.info("fontc not found in PATH, using fontmake")

    # Fallback to fontmake
    cmd = [
        "fontmake",
        "-o", "variable",
        "-g", str(glyphs_path),
        "--output-dir", str(output_dir)
    ]

    log.info(f"Building variable font with fontmake: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=glyphs_path.parent
    )

    if result.returncode != 0:
        error_msg = result.stderr or result.stdout or "Unknown error"
        log.info(f"fontmake failed with exit code {result.returncode}")
        log.info(f"fontmake stderr: {error_msg}")
        if result.stdout:
            log.info(f"fontmake stdout: {result.stdout}")
        raise RuntimeError(f"fontmake failed: {error_msg}")

    return _pick_variable_font(output_dir)


# Guards state._MAPPED_FONT_CACHE.
_MAPPED_FONT_LOCK = threading.Lock()


def _authored_mapping_table(font_path: Path, avar2_cfg: List[Dict],
                            target: Dict[str, float]) -> Optional[Dict]:
    """The mapping the AUTHORED rows describe, with the parametric defaults
    moved onto the ORIGIN row's out — i.e. the avar table the font would carry
    if the relocation had been possible.

    A degraded build's real table is generated around the compiled origin and
    cannot express the default row at all (ADR 0001), so
    /api/mapped-location would echo the compiled origin exactly where the
    ORIGIN row should be — the one place the designer looks first. This needs
    only ``fvar`` to build: no gvar, milliseconds. Returns
    ``{axes: {tag: [min, default, max]}, segments: {tag: [[from, to]…]}}``.
    """
    from gftools.scripts import gen_avar2 as _gen_avar2

    font = TTFont(str(font_path))
    try:
        axes_by_tag = {a.axisTag: a for a in font["fvar"].axes}
        for tag, value in (target or {}).items():
            axis = axes_by_tag.get(tag)
            if axis is None:
                continue
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if axis.minValue <= v <= axis.maxValue:
                axis.defaultValue = v
        # The build patches gen_fvar_axes so the mapping's in: axes (which all
        # exist in the built font) cannot be created a second time; mirror it.
        original_axes = _gen_avar2.gen_fvar_axes

        def _no_new_axes(font_, mapping):
            existing = {a.axisTag for a in font_["fvar"].axes}
            filtered = [{**m, "in": {k: v for k, v in (m.get("in") or {}).items()
                                     if k not in existing}}
                        for m in mapping]
            return original_axes(font_, filtered)

        _gen_avar2.gen_fvar_axes = _no_new_axes
        try:
            _gen_avar2.gen_avar2_mapping(font, avar2_cfg)
        finally:
            _gen_avar2.gen_fvar_axes = original_axes
        avar = font.get("avar")
        if avar is None:
            return None
        return {
            "axes": {a.axisTag: [float(a.minValue), float(a.defaultValue),
                                 float(a.maxValue)]
                     for a in font["fvar"].axes},
            # fontTools stores an axis's segment map as a FLAT list of
            # from/to pairs ([from0, to0, from1, to1, …]); accept explicit
            # pairs too so the shape cannot silently change under us.
            "segments": {tag: _segment_pairs(segs)
                         for tag, segs in (avar.segments or {}).items()},
        }
    finally:
        font.close()


def _segment_pairs(segs) -> List[List[float]]:
    """Normalise an avar axis's segment map to ``[[from, to], …]``."""
    values = list(segs or [])
    if values and isinstance(values[0], (list, tuple)):
        return [[float(a), float(b)] for a, b in values]
    flat = [float(v) for v in values]
    return [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]


def _piecewise_map(value: float, segments) -> float:
    """One avar axis's segment map at ``value`` (identity outside the map)."""
    prev_from = prev_to = 0.0
    for start, end in segments:
        if value == start:
            return end
        if value < start:
            if start == prev_from:
                return end
            return prev_to + (end - prev_to) * (value - prev_from) / (start - prev_from)
        prev_from, prev_to = start, end
    return value


def _evaluate_authored_mapped_location(coords: Dict, data: Dict) -> Dict[str, float]:
    """Evaluate the AUTHORED mapping (see ``_authored_mapping_table``)."""
    from fontTools.varLib.models import normalizeLocation

    axes = {tag: tuple(triple) for tag, triple in data["axes"].items()}
    loc = {}
    for tag, (lo, default, hi) in axes.items():
        try:
            v = float(coords.get(tag, default))
        except (TypeError, ValueError):
            v = default
        loc[tag] = max(lo, min(hi, v))
    norm = normalizeLocation(loc, axes)
    mapped = {tag: _piecewise_map(norm.get(tag, 0.0), segs)
              for tag, segs in data.get("segments", {}).items()}
    out = {}
    for tag, (lo, default, hi) in axes.items():
        n = mapped.get(tag, norm.get(tag, 0.0))
        out[tag] = round(default + n * ((hi - default) if n > 0 else (default - lo)), 2)
    return out


def _evaluate_mapped_location(coords: Dict) -> Dict[str, float]:
    """Evaluate the built font's avar table at a user-space location;
    returns every fvar axis's effective post-mapping value in user
    space. Shared by /api/mapped-location (slider reflection) and
    /api/export-font (default relocation).

    Under a degraded build (the default could not be relocated) the built
    table is anchored on the compiled origin, so the AUTHORED mapping is
    evaluated instead — that is what the designer authored and what the
    Preview pins to (S1(c), round-12 review)."""
    from fontTools.varLib.models import normalizeLocation

    authored = state.LAST_ORIGIN_AUTHORED_AVAR
    if authored and state.LAST_ORIGIN_RELOCATION_ERROR:
        return _evaluate_authored_mapped_location(coords, authored)

    with _MAPPED_FONT_LOCK:
        key = (str(state.VARIABLE_FONT_PATH), state.VARIABLE_FONT_PATH.stat().st_mtime)
        if state._MAPPED_FONT_CACHE["key"] != key:
            state._MAPPED_FONT_CACHE["font"] = TTFont(str(state.VARIABLE_FONT_PATH), lazy=True)
            state._MAPPED_FONT_CACHE["key"] = key
        tt = state._MAPPED_FONT_CACHE["font"]

        axes = {
            a.axisTag: (a.minValue, a.defaultValue, a.maxValue)
            for a in tt["fvar"].axes
        }
        loc = {}
        for tag, (lo, d, hi) in axes.items():
            try:
                v = float(coords.get(tag, d))
            except (TypeError, ValueError):
                v = d
            loc[tag] = max(lo, min(hi, v))
        norm = normalizeLocation(loc, axes)
        avar = tt.get("avar")
        renorm = avar.renormalizeLocation(norm, tt) if avar is not None else norm

    def _denorm(tag, n):
        lo, d, hi = axes[tag]
        return d + n * ((hi - d) if n > 0 else (d - lo))

    # Axes absent from the renormalized dict sit at their default
    # (normalized 0) — emit them too so the caller needn't guess.
    return {tag: round(_denorm(tag, renorm.get(tag, 0.0)), 2) for tag in axes}


def _apply_hidden_axes(path: Path, hidden_tags) -> None:
    """Set the fvar HIDDEN flag (0x0001) on the given axes so end-user
    font pickers don't surface them. In place; unknown tags ignored."""
    tags = {str(t) for t in (hidden_tags or [])}
    if not tags:
        return
    tt = TTFont(str(path))
    changed = False
    for a in tt["fvar"].axes:
        if str(a.axisTag) in tags and not (a.flags & 0x0001):
            a.flags |= 0x0001
            changed = True
    if changed:
        tt.save(str(path))


def _save_woff2(font_path, out_path):
    """Lossless TTF→WOFF2 (fontTools Brotli flavor)."""
    font = TTFont(str(font_path))
    font.flavor = "woff2"
    font.save(str(out_path))
    font.close()
    return Path(out_path)


def _export_pipeline(font_path, hidden_axes, default_location, export_dir,
                     format="ttf"):
    """The export build, caller-agnostic: re-origin at ``default_location``
    if given, post-transform, flag hidden axes. Returns the artifact path.
    Runs in the build worker (queued export jobs); ``font_path`` is the
    current built font, read-only. ``export_dir`` is the job's own dir.
    ``format`` "woff2" converts the result losslessly.
    """
    font_path = Path(font_path)
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    if not default_location:
        out_path = export_dir / font_path.name
        shutil.copy2(str(font_path), str(out_path))
    else:
        tt = TTFont(str(font_path), lazy=True)
        fvar_tags = {str(a.axisTag) for a in tt["fvar"].axes}
        requested = {}
        for t, v in default_location.items():
            if str(t) in fvar_tags:
                try:
                    requested[str(t)] = float(v)
                except (TypeError, ValueError):
                    pass
        if not requested:
            raise ValueError("default_location has no known axis values")
        tt.close()

        # Where the combination lands in the design space — the
        # parametric axes get rebased there so the new origin
        # renders this exact master.
        # The parametric location the export will REST at — the new
        # default master gets interpolated exactly here.
        # An exact authored row (same axis set, equal values) wins over
        # the built table: with relocation off the preview table cannot
        # express the ORIGIN row. In-between locations still evaluate the
        # built table. See docs/decisions/0007-export-set-default-relocation-direction.md.
        mapped = None
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load(Path(_get_preview_config_path()).read_text()) or {}
            av = cfg.get("avar2")
            rows = av if isinstance(av, list) else (next(iter(av.values())) if isinstance(av, dict) and av else [])
            req_keys = {str(k).lower() for k in requested}
            for m in rows or []:
                ins = m.get("in") or {}
                # Same axis SET and equal values — a row that merely
                # omits an axis must not match by default-filling (see
                # docs/decisions/0007-export-set-default-relocation-direction.md).
                if {str(k).lower() for k in ins} != req_keys:
                    continue
                ins_ci = {str(k).lower(): float(v) for k, v in ins.items()}
                if all(abs(ins_ci[str(k).lower()] - float(v)) < 0.01
                       for k, v in requested.items()):
                    mapped = {str(t): float(v) for t, v in (m.get("out") or {}).items()}
                    break
        except Exception as exc:
            log.info(f"export: authored-row lookup failed ({exc}); using the built table")
        if mapped is None:
            mapped = _evaluate_mapped_location(requested)

        # ADR 0007's method: NO inserted master (its interior drift measured
        # up to 761 u on Crispy — the verifier's T3.8 failure). Build the
        # staged ORIGINAL source exactly like the preview, with step-3.5
        # relocation ON and its target = ``mapped`` (P, exact — the round()
        # here used to re-trigger the densifying rebase, audit §2 H3).
        export_workdir = export_dir / "work"
        export_workdir.mkdir(parents=True, exist_ok=True)
        export_config = export_workdir / "config.yaml"
        shutil.copy2(str(_get_preview_config_path()), str(export_config))
        # The axis metadata must travel with the config:
        # _declared_axis_defaults resolves it as a config
        # sibling, and without it the blank-cell fill silently
        # no-ops — blanks then rebind to the relocated defaults
        # and alias rows ("Locations must be unique").
        _meta_src = _get_avar2_metadata_path()
        if _meta_src and Path(_meta_src).exists():
            # The blank-cell fill reads its defaults from the metadata file,
            # so the copy that travels with the export must carry the
            # REQUESTED defaults — otherwise a blank row aliases onto the
            # requested row ("Locations must be unique").
            import json as _json
            meta = _json.loads(Path(_meta_src).read_text(encoding="utf-8"))
            for _col, m in meta.items():
                tag = (m.get("registered_tag") or "").strip()
                if tag and tag in requested:
                    m["default"] = requested[tag]
            (export_workdir / "axis-metadata.json").write_text(
                _json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        from .build import config_generator as _cfg_gen
        # The export compiles the same source the preview does: the resolved
        # ACTIVE source (control-axis shadow + grade braces + source-stage
        # transforms), which the job queue stages for the worker as
        # ``active-<name>`` and binds to state.GLYPHS_PATH. Building the raw
        # original here dropped every studio axis and transform (verifier
        # round 6).
        _cfg_gen.update_config(
            csv_path=_get_preview_csv_path(),
            config_path=export_config,
            backup=False,
            source_path=state.GLYPHS_PATH,
            fill_in_defaults=True,
        )

        build_env = _builder_env(default_overrides=requested)
        fontc_path = shutil.which("fontc", path=build_env["PATH"])
        if not fontc_path:
            raise RuntimeError("fontc not found in PATH")
        # Same in-process pipeline as the preview build (see
        # _build_in_process); the requested default location rides in
        # as the declared-axis override, exactly as it did via env.
        if os.environ.get("AVAR2_STUDIO_GFTOOLS_BUILDER") == "1":
            builder_cmd = ["gftools", "builder", "--experimental-fontc", fontc_path, str(export_config.resolve())]
            result = subprocess.run(builder_cmd, capture_output=True, text=True, cwd=str(export_workdir), env=build_env)
            if result.returncode != 0:
                raise RuntimeError("Export build failed: %s"
                                   % (result.stderr or result.stdout or "")[-800:])
        else:
            _export_report: Dict[str, Optional[str]] = {}
            _build_in_process(
                export_config, export_workdir, fontc_path,
                default_overrides=requested,
                relocate_default=True,
                relocate_target=mapped,
                relocation_report=_export_report,
                relocation_memo_dir=state.RELOCATION_MEMO_DIR)
            if _export_report.get("error"):
                # An export asked for a specific resting style; shipping the
                # un-relocated build instead would be a lie about the default.
                raise RuntimeError(
                    "Cannot relocate the default to the requested location: "
                    + str(_export_report["error"]))
        produced = []
        for candidate in (export_workdir / "fonts", export_workdir.parent / "fonts", export_dir / "fonts"):
            produced += list(candidate.glob("**/*.ttf")) if candidate.exists() else []
        produced = sorted(set(produced), key=lambda p: p.stat().st_mtime, reverse=True)
        if not produced:
            raise RuntimeError("Export build produced no font")
        out_path = export_dir / produced[0].name
        shutil.move(str(produced[0]), str(out_path))
        # Same post-build transforms (SPAC…) as the preview.
        out_path = Path(_apply_transform_chain(out_path))

    _apply_hidden_axes(out_path, hidden_axes)
    if format == "woff2":
        out_path = _save_woff2(out_path, out_path.with_suffix(".woff2"))
    return out_path


def _get_preview_dir() -> Optional[Path]:
    """Return the per-project working directory.

    avar2-studio writes its tool-managed state into ``$GLYPHS_PARENT/.avar2-studio/``
    next to the .glyphs file — config, axis metadata, and built fonts all live
    here. The dir is created on demand. Designers should gitignore it; the
    sibling ``MyFont-avar.csv`` (outside this dir) is the authored artifact.
    """
    if state.PREVIEW_DIR:
        return state.PREVIEW_DIR

    # Resolve against the ORIGINAL source, never GLYPHS_PATH: once a control
    # axis has brace layers GLYPHS_PATH points at the shadow, and deriving from
    # it nests a second .avar2-studio/ INSIDE the shadow dir.
    base = state.ORIGINAL_PATH or state.GLYPHS_PATH
    if not base:
        return None

    workdir = base.parent / ".avar2-studio"
    workdir.mkdir(parents=True, exist_ok=True)
    state.PREVIEW_DIR = workdir
    return workdir


def _get_preview_csv_path() -> Optional[Path]:
    """Get path to the avar2 CSV (sibling to the .glyphs file).

    e.g. for ``MyFont.glyphs`` the CSV is ``MyFont-avar.csv`` in the same
    directory. The CSV is the designer's authored artifact and is intentionally
    OUTSIDE the .avar2-studio workdir so it gets committed alongside the source.
    """
    if state.PREVIEW_CSV_PATH:
        return state.PREVIEW_CSV_PATH

    # Resolve against the ORIGINAL source, never GLYPHS_PATH: once a control
    # axis has brace layers GLYPHS_PATH points at the shadow, which would put
    # the designer's authored CSV inside .avar2-studio/shadow/ and make every
    # /api/avar2/* lookup miss the real one next to the source.
    base = state.ORIGINAL_PATH or state.GLYPHS_PATH
    if not base:
        return None

    # Locked convention: family name is the source file's stem in both
    # .glyphs and .designspace flows, so the CSV name is deterministic
    # without loading the source.
    family_name = base.stem
    csv_path = base.parent / f"{family_name}-avar.csv"
    state.PREVIEW_CSV_PATH = csv_path
    return csv_path


def _get_preview_config_path() -> Optional[Path]:
    """Get path to the gftools-builder config inside the .avar2-studio workdir."""
    if state.PREVIEW_CONFIG_PATH:
        return state.PREVIEW_CONFIG_PATH

    workdir = _get_preview_dir()
    if not workdir:
        return None

    config_path = workdir / "config.yaml"
    state.PREVIEW_CONFIG_PATH = config_path
    return config_path


def _get_avar2_font_dir() -> Optional[Path]:
    """Get avar2 font directory (collapsed into the shared build dir)."""
    workdir = _get_preview_dir()
    if not workdir:
        return None
    font_dir = workdir / "build"
    font_dir.mkdir(parents=True, exist_ok=True)
    return font_dir


def _get_avar2_metadata_path() -> Optional[Path]:
    """Get path to axis-metadata.json (inside the .avar2-studio workdir)."""
    workdir = _get_preview_dir()
    if not workdir:
        return None
    return workdir / "axis-metadata.json"


# Extent written for an axis whose real range isn't known yet. Parametric
# axes are re-synced from the source's masters on every load, so a placeholder
# there is self-healing; traditional axes have no such anchor, so this value
# is also the marker that lets _repair_placeholder_ranges() spot an entry that
# was never really initialised and derive it properly.
_PLACEHOLDER_MIN, _PLACEHOLDER_MAX = -1000, 1000


def _is_placeholder_range(entry: Dict) -> bool:
    """True while an entry still carries the untouched placeholder extent.

    Used to distinguish "never initialised" from "the designer set this",
    so repairs never clobber a hand-edited range.
    """
    try:
        return (
            float(entry.get("min")) == float(_PLACEHOLDER_MIN)
            and float(entry.get("max")) == float(_PLACEHOLDER_MAX)
        )
    except (TypeError, ValueError):
        return False


def _is_degenerate_range(entry: Dict) -> bool:
    """True if an entry's extent has collapsed to a point.

    Axes carried by VIRTUAL masters (GRAD) or injected post-build (SPAC) have
    no real master spanning them, so the source reports them as [0, 0] — a
    slider with nowhere to go. Treated as repairable, but only when something
    actually defines the axis, so a genuinely unused axis stays as-is.
    """
    try:
        return float(entry.get("min")) >= float(entry.get("max"))
    except (TypeError, ValueError):
        return False


def _derive_traditional_range(tag: str, rows: Optional[List[Dict]] = None,
                              col: Optional[str] = None):
    """Real ``(min, max)`` for a traditional (non-parametric) axis.

    Parametric axes take their extent from the source's masters. Traditional
    axes have no such anchor, so recover it from whichever artifact actually
    defines the axis:

    1. an enabled transform that injects it (e.g. SPAC) — the transform's own
       min/max params, which are what it writes into fvar;
    2. the grade axis — the fixed registered GRAD extent;
    3. otherwise the CSV column's own values, which ARE the mapping corners
       (the same source the generated STAT table already uses).

    Returns ``None`` when nothing defines the axis, leaving the placeholder in
    place rather than inventing a range.
    """
    tag_upper = (tag or "").strip().upper()

    # 1. Transform-injected axes (SPAC): the transform owns the fvar extent.
    if state.ORIGINAL_PATH is not None:
        try:
            _transforms.discover()
            for transform, params in _transforms.active(state.ORIGINAL_PATH):
                if (transform.spec.injected_axis_tag or "").upper() != tag_upper:
                    continue
                lo, hi = params.get("min"), params.get("max")
                if lo is not None and hi is not None and float(lo) < float(hi):
                    return float(lo), float(hi)
        except Exception as exc:
            log.warning(f"Warning: transform range lookup for {tag_upper} failed: {exc}")

    # 2. The grade axis has a fixed registered extent.
    if tag_upper == _grade.GRAD_TAG.upper():
        return float(_grade.GRAD_MIN), float(_grade.GRAD_MAX)

    # 3. Fall back to the CSV column's own spread. Blank cells mean "inherit",
    #    not zero, so they're skipped rather than counted as a corner.
    if col is None:
        return None
    values: List[float] = []
    for row in rows or []:
        raw = row.get(col)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            values.append(float(str(raw).strip()))
        except ValueError:
            continue
    if len(values) >= 2 and min(values) < max(values):
        return min(values), max(values)
    return None


def _repair_placeholder_ranges(metadata: Dict, rows: List[Dict], in_cols: List[str]) -> bool:
    """Re-derive any traditional axis still sitting at the placeholder extent.

    Without this, an axis seeded before its CSV column had values (or before
    its transform was enabled) would keep -1000/1000 forever — and that range
    reaches real fvar, not just the UI. Hand-edited ranges are left alone.
    Returns True if anything changed.
    """
    changed = False
    for key, entry in metadata.items():
        if not isinstance(entry, dict):
            continue
        if not (_is_placeholder_range(entry) or _is_degenerate_range(entry)):
            continue
        # Only axes that are actually a CSV column can be derived from its
        # values; the rest (GRAD, SPAC) fall through to their own definitions.
        col = key if key in in_cols else None
        derived = _derive_traditional_range(
            entry.get("registered_tag") or key, rows, col
        )
        if derived is None:
            continue
        entry["min"], entry["max"] = derived
        # A default carried over from the placeholder era can sit outside the
        # real range (e.g. opsz 72 against a 12..144 axis is fine, but a
        # stale one may not be) — clamp it so fvar stays well-formed.
        if isinstance(entry.get("default"), (int, float)):
            entry["default"] = max(derived[0], min(derived[1], float(entry["default"])))
        changed = True
    return changed


def _load_axis_metadata() -> Dict[str, Dict[str, any]]:
    """Load axis metadata from JSON file. Auto-populates with Glyphs file axes if missing."""
    metadata_path = _get_avar2_metadata_path()
    if not metadata_path:
        return {}

    # Create file with empty dict if it doesn't exist
    if not metadata_path.exists():
        try:
            _sidecar_io.write_json(metadata_path, {})
        except Exception as e:
            log.error(f"Error creating axis metadata file: {e}")
            return {}

    # Load existing metadata
    try:
        with metadata_path.open("r", encoding="utf-8") as f:
            metadata = json.load(f)
    except Exception as e:
        log.error(f"Error loading axis metadata: {e}")
        metadata = {}

    # Get parametric axes from the source file and add to metadata if missing.
    if state.GLYPHS_PATH and state.GLYPHS_PATH.exists():
        try:
            font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
            metadata_updated = False

            for axis in _source_font.get_axes(font):
                axis_tag = axis["tag"]
                axis_tag_upper = axis_tag.upper()
                axis_name = axis.get("name") or axis_tag_upper
                # An axis declared in the source is only truly "parametric"
                # if it has master coverage. Empty axes (declared but with
                # min==max or no master deltas) are avar2 mapping inputs —
                # they should remain editable in the AVAR2 MAPPINGS UI.
                is_parametric = axis.get("has_master_coverage", True)

                if axis_tag_upper not in metadata:
                    # The source's masters already give the real extent — seed
                    # with it rather than a placeholder, so the entry is never
                    # briefly wrong (and stays right even if the re-sync in
                    # /api/avar2/axes doesn't run, e.g. no CSV yet). Axes on
                    # virtual masters report [0,0] here, so fall back to
                    # whatever defines them before the placeholder.
                    axis_min = float(axis.get("min", 0.0))
                    axis_max = float(axis.get("max", 0.0))
                    if axis_min >= axis_max:
                        axis_min, axis_max = _derive_traditional_range(axis_tag) or (
                            _PLACEHOLDER_MIN, _PLACEHOLDER_MAX
                        )
                    metadata[axis_tag_upper] = {
                        "display_name": axis_name,
                        "registered_tag": axis_tag.lower(),
                        "min": axis_min,
                        "max": axis_max,
                        "is_parametric": is_parametric,
                    }
                    metadata_updated = True
                else:
                    # Always re-sync the parametric flag — coverage can
                    # change as masters are added/removed.
                    if metadata[axis_tag_upper].get("is_parametric") != is_parametric:
                        metadata[axis_tag_upper]["is_parametric"] = is_parametric
                        metadata_updated = True
                    if metadata[axis_tag_upper].get("display_name") == axis_tag_upper:
                        metadata[axis_tag_upper]["display_name"] = axis_name
                        metadata_updated = True

            # Save updated metadata if we added any parametric axes
            if metadata_updated:
                _save_axis_metadata(metadata)
        except Exception as e:
            log.error(f"Error reading axes from Glyphs file for metadata: {e}")

    return metadata


def _save_axis_metadata(metadata: Dict[str, Dict[str, any]]) -> bool:
    """Save axis metadata to JSON file. The backup + write are one locked
    unit (the same ``sidecar_io`` lock the request handlers take)."""
    metadata_path = _get_avar2_metadata_path()
    if not metadata_path:
        return False

    with _sidecar_io.LOCK:
        try:
            _csv_io.backup_sidecar(metadata_path)
            _sidecar_io.write_json(metadata_path, metadata)
            return True
        except Exception as e:
            log.error(f"Error saving axis metadata: {e}")
            return False


def _grade_instance_coords():
    """Parametric {XTRA,XOPQ,YOPQ} for EVERY instance — source-defined AND
    studio-only (CSV rows) alike — keyed by name, since a grade can target
    either kind. Source coords win; the CSV supplies studio-only rows (and any
    parametric tag a source instance happens to lack). CrispyMini, for example,
    has zero source instances — all live in the CSV — so reading source alone
    (the original bug) found no coords and every grade was skipped."""
    coords = {}
    try:
        font, _fmt = _source_font.load_source_cached(state.ORIGINAL_PATH)
        for inst in _source_font.get_source_instances(font):
            coords[inst["name"]] = dict(inst.get("coordinates", {}))
    except Exception:
        pass
    try:
        csv_path = _get_preview_csv_path()
        if csv_path and csv_path.exists():
            import csv as _csvmod
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in _csvmod.DictReader(f):
                    name = (row.get("Instance Name") or "").strip()
                    if not name:
                        continue
                    c = coords.setdefault(name, {})
                    for tag in _grade.PARAM_TAGS:
                        if tag in c:
                            continue
                        v = row.get(tag)
                        if v not in (None, ""):
                            try:
                                c[tag] = float(v)
                            except (TypeError, ValueError):
                                pass
    except Exception:
        pass
    return coords


def _initialize_preview_csv_from_glyphs() -> Optional[Path]:
    """Initialize preview CSV from the source file.

    Creates a CSV with ``Instance Name`` and one column per source axis.
    SPAC is deferred from v1 — no SPAC column is added on bootstrap.
    """
    csv_path = _get_preview_csv_path()
    if not csv_path:
        return None

    # If CSV exists, leave it alone.
    if csv_path.exists():
        return csv_path

    if not state.GLYPHS_PATH or not state.GLYPHS_PATH.exists():
        return None

    try:
        import csv
        font, _fmt = _source_font.load_source(state.GLYPHS_PATH)
        # All source axes get a column. SPAC support is deferred — even
        # if the source declares a SPAC axis it's treated as just
        # another parametric column with no special handling.
        parametric_axes = [
            axis["tag"].upper() for axis in _source_font.get_axes(font)
        ]
        source_instances = _source_font.get_source_instances(font)

        # Always create the CSV with the header so studio-only instance
        # creation works against an empty designspace (zero source instances).
        fieldnames = ["Instance Name"] + parametric_axes
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for entry in source_instances:
                row = {"Instance Name": entry["name"]}
                for tag, value in entry["coordinates"].items():
                    row[tag.upper()] = value
                for axis in parametric_axes:
                    row.setdefault(axis, 0)
                writer.writerow(row)

        log.info(f"Initialized preview CSV: {csv_path}")
        return csv_path
    except Exception as e:
        log.error(f"Error initializing preview CSV: {e}")
        import traceback
        traceback.print_exc()

    return None


def _initialize_preview_config_from_glyphs() -> Optional[Path]:
    """
    Initialize preview config.yaml from Glyphs file.
    Creates minimal config with sources, familyName, and fvarInstances from Glyphs.
    Only creates if config doesn't exist.
    """
    config_path = _get_preview_config_path()
    if not config_path:
        return None

    # Don't overwrite existing config — except to repair the empty-
    # fvarInstances case: an instance-less source produced ``ttf: []``,
    # which the gftools builder's schema rejects (failing every avar2
    # build). Delete and regenerate; avar2/STAT merge back on next build.
    if config_path.exists():
        try:
            import yaml
            existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            fvar = existing.get("fvarInstances") or {}
            if fvar and all(not v for v in fvar.values()):
                # Broken either way: an instance-less source shouldn't
                # carry the key at all (the builder's strictyaml schema
                # rejects ``ttf: []``), and an instance-BEARING source
                # should carry its instances — a stale empty list from
                # before they existed fails every avar2 build just the
                # same. Regenerate from the source in both cases.
                config_path.unlink()
            else:
                return config_path
        except Exception:
            return config_path

    if not state.GLYPHS_PATH or not state.GLYPHS_PATH.exists():
        return None

    try:
        import yaml
        font, _fmt = _source_font.load_source(state.GLYPHS_PATH)

        # Locked convention: family name from source file stem.
        family_name = _source_font.get_family_name(font, state.GLYPHS_PATH)

        axes = _source_font.get_axes(font)
        source_instances = _source_font.get_source_instances(font)

        fvar_instances = []
        for entry in source_instances:
            coords = {tag.lower(): float(value) for tag, value in entry["coordinates"].items()}
            if coords:
                fvar_instances.append({
                    "name": entry["name"],
                    "coordinates": coords,
                })

        # Built-font filename encodes axis tags exactly as gftools-builder
        # will produce them — preserve case (lowercase for registered axes,
        # uppercase for custom/parametric).
        parametric_tags = sorted(axis["tag"] for axis in axes if axis.get("tag"))
        if parametric_tags:
            font_filename = f"{family_name}[{','.join(parametric_tags)}].ttf"
        else:
            font_filename = f"{family_name}-VF.ttf"

        # Use absolute path for sources to avoid path issues
        sources_path = str(state.GLYPHS_PATH.resolve())

        # Only emit fvarInstances when the source declares instances.
        # An empty list crashes the gftools builder's strictyaml schema
        # ("ugly disallowed JSONesque flow mapping") — which silently
        # failed the whole avar2 build for instance-less sources, so the
        # preview never got avar2 or its user axes.
        config = {
            "sources": [sources_path],
            "familyName": family_name,
        }
        if fvar_instances:
            config["fvarInstances"] = {font_filename: fvar_instances}

        # Write config
        with _sidecar_io.atomic_writer(config_path) as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        log.info(f"Initialized preview config: {config_path}")
        return config_path
    except Exception as e:
        log.error(f"Error initializing preview config: {e}")
        import traceback
        traceback.print_exc()

    return None


def _stop_fontra() -> None:
    """Kill any running Fontra subprocess. Safe to call repeatedly."""
    if state.FONTRA_PROCESS is None:
        return
    try:
        if state.FONTRA_PROCESS.poll() is None:
            state.FONTRA_PROCESS.terminate()
            try:
                state.FONTRA_PROCESS.wait(timeout=3)
            except Exception:
                state.FONTRA_PROCESS.kill()
    except Exception as exc:
        log.warning(f"Warning: error stopping Fontra: {exc}")
    finally:
        state.FONTRA_PROCESS = None
        state.FONTRA_CONTENT_ROOT = None


def _apply_source_path(path: Path) -> None:
    """Point the running server at ``path`` as the active source. Mutates
    the GLYPHS_PATH / SOURCE_FORMAT / BUILD_DIR / CSV_PATH globals,
    re-bootstraps the CSV + config sidecars, and kicks off an
    auto-build. Raises ValueError / UnsupportedSourceFormat on bad input
    so callers (CLI + /api/load-source) can surface the failure."""

    resolved = path.resolve()
    if not resolved.exists():
        raise ValueError(f"source file not found: {resolved}")
    if resolved.suffix.lower() == ".ufo":
        raise ValueError(
            "avar2-studio requires a .designspace, not an individual UFO master. "
            "Point it at the sibling .designspace file."
        )

    state.SOURCE_FORMAT = _source_font.detect_format(resolved)
    state.ORIGINAL_PATH = resolved
    # _resolve_active_source re-points GLYPHS_PATH at the shadow when a
    # studio feature needs it. See docs/ARCHITECTURE.md §3.
    state.GLYPHS_PATH = resolved

    workdir = state.GLYPHS_PATH.parent / ".avar2-studio"
    state.BUILD_DIR = workdir / "build"
    state.BUILD_DIR.mkdir(parents=True, exist_ok=True)
    # Reset every state global that's keyed to the previous source —
    # otherwise PREVIEW_DIR/PREVIEW_CSV_PATH/PREVIEW_CONFIG_PATH stay
    # pinned to the first load and every subsequent ``/api/avar2/*``
    # call reads the OLD font's mapping CSV + axis metadata. That's
    # how Roboto Delta's XOUC/YOUC/XTUC columns kept showing up after
    # the user swapped to Crispy.
    state.VARIABLE_FONT_PATH = None
    state.CSV_PATH = None
    state.PREVIEW_DIR = None
    state.PREVIEW_CSV_PATH = None
    state.PREVIEW_CONFIG_PATH = None
    state.LAST_BUILD_TIME = None
    state.LAST_BUILD_STATUS = None
    state.LAST_BUILD_ERROR = None
    state.EDITING_INSTANCES = set()

    # Stop any Fontra subprocess pointed at the previous source's
    # shadow. It'll be re-spawned on demand when the user opens a
    # control axis in the editor on the new source.
    _stop_fontra()

    _initialize_preview_csv_from_glyphs()
    _initialize_preview_config_from_glyphs()

    # Re-establish the active build source from the current control-axis AND
    # grade state — a shadow of the original carrying brace layers and/or grade
    # braces, or the original itself. Runs AFTER the CSV init because grade reads
    # per-instance parametric coords from it. This is what makes grade (and
    # authored brace layers) survive a restart: before, only a feature EDIT
    # re-applied them, so a plain reload dropped the GRAD axis (and brace deltas)
    # from the built font until the designer re-saved.
    try:
        _resolve_active_source()
    except Exception as exc:
        log.warning(f"Warning: failed to resolve active build source on load: {exc}")

    # A new source invalidates every queued/running job: cancel them and
    # start clean.
    state.JOBS.cancel_all()

    log.info(f"Auto-building font from {state.GLYPHS_PATH}...")
    try:
        state.JOBS.request_build()
    except Exception as exc:
        # Non-fatal — the frontend can retry via /api/build.
        log.info(f"⚠ Auto-build failed: {exc}")
