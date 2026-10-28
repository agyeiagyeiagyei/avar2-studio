"""Console entry point for avar2-studio.

Dispatches between:

  - ``avar2-studio doctor``  → environment check (no Glyphs file required)
  - ``avar2-studio build /path/to/MyFont.glyphs --out DIR``  → headless build
  - ``avar2-studio install-glyphs-plugins``  → link the Glyphs 3 plugins into Glyphs
  - ``avar2-studio /path/to/MyFont.glyphs [server args…]``  → run the server

Everything that isn't a known subcommand is forwarded to ``server.main()``
so the existing argparse contract continues to work.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_HELP = """\
Usage:
  avar2-studio /path/to/MyFont.glyphs [options]
  avar2-studio doctor
  avar2-studio --help

Subcommands:
  doctor                  Run environment checks (fontc, gftools, frontend bundle, …)
                         --strict exits 1 when a headless-build prerequisite is missing
  build                   Build the font once, headless, and copy it to --out (for CI)
  install-glyphs-plugins  Link the bundled Glyphs 3 plugins into Glyphs (--uninstall removes them)

Run ``avar2-studio /path/to/MyFont.glyphs --help`` to see server options.
"""


def main() -> None:
    argv = sys.argv[1:]

    if argv and argv[0] == "doctor":
        from . import doctor
        doctor.main(argv[1:])
        return

    if argv and argv[0] == "build":
        sys.exit(_build(argv[1:]))

    if argv and argv[0] == "install-glyphs-plugins":
        from . import glyphs_install
        sys.exit(glyphs_install.main(argv[1:]))

    if argv and argv[0] in ("-h", "--help") and len(argv) == 1:
        print(_HELP)
        return

    # Default: dispatch to the server's main(). It reads sys.argv itself.
    from . import server
    server.main()


def _axis_metadata_paths(source: Path):
    """Both homes for the declared axis defaults (C1).

    ``<stem>-axis-metadata.json`` beside the source is the canonical,
    COMMITTED location. ``.avar2-studio/axis-metadata.json`` is the pre-C1
    location: tool-managed and gitignored, so it only helps a checkout that
    already ran the studio.
    """
    return (
        source.parent / f"{source.stem}-axis-metadata.json",
        source.parent / ".avar2-studio" / "axis-metadata.json",
    )


def _axis_metadata_problem(source: Path):
    """None when the declared axis defaults ship with the source, else the
    message to fail with.

    Without this file the traditional axes' declared default (opsz 144 on
    Crispy) is not in the build inputs at all, so each one falls back to its
    column minimum and every named style lands off its mapped value (62 u
    measured). In CI that is a silent wrong font, not a warning.
    """
    sibling, legacy = _axis_metadata_paths(source)
    if sibling.exists() or legacy.exists():
        return None
    return (f"axis metadata not found: neither {sibling.name} nor "
            f".avar2-studio/{legacy.name} exists beside {source.name}. It holds "
            f"the declared axis defaults (opsz 144 etc.); without them named "
            f"styles land off their mapped values. Open the font in "
            f"avar2-studio once and commit {sibling.name} (or copy it from the "
            f"studio's .avar2-studio/axis-metadata.json).")


def _build(argv) -> int:
    """``avar2-studio build SOURCE --out DIR``: one headless build.

    Runs exactly the pipeline the server runs on load — sidecar bootstrap,
    control-axis shadow, grade braces, then the avar2 build — but with no web
    server, no file watcher and no Fontra. Meant for CI, where the old
    Makefile flow used to live: the studio owns the build now, so the build
    it ships is the build CI checks. Exit status is the build status.
    """
    import argparse
    import shutil
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="avar2-studio build")
    ap.add_argument("source", type=Path, help=".glyphs / .designspace source")
    ap.add_argument("--out", type=Path, required=True,
                    help="directory the built font is copied into (created)")
    ap.add_argument("--workdir", type=Path, default=None,
                    help="put the tool workdir (config, shadow, build output, "
                         "relocation memo) under DIR instead of beside the source, "
                         "so a CI checkout stays untouched")
    ap.add_argument("--hide-axes", default=None,
                    help="comma-separated fvar tags to flag HIDDEN (font pickers "
                         "stop surfacing them)")
    ap.add_argument("--default", dest="default_location", default=None,
                    help="resting style as tag=value[,tag=value] (e.g. "
                         "wght=400,opsz=14); the export is re-origined there")
    ap.add_argument("--format", dest="formats", action="append",
                    choices=("ttf", "woff2"), default=None,
                    help="output format, repeatable (default: ttf)")
    ap.add_argument("--config", dest="build_config", type=Path, default=None,
                    help="the committed build config (default: <stem>-build.json "
                         "beside the source, when present)")
    ap.add_argument("--write-config", action="store_true",
                    help="write <stem>-build.json from the current sidecars and "
                         "exit (no build)")
    ap.add_argument("--csv", type=Path, default=None,
                    help="avar2 mappings CSV (default: <stem>-avar.csv beside the source)")
    args = ap.parse_args(argv)

    if args.csv is not None:
        # Dead override: _apply_source_path resets CSV_PATH and the build
        # always reads the sibling <stem>-avar.csv. Say so, don't pretend.
        print("error: --csv is no longer supported — the build reads "
              "<stem>-avar.csv next to the source file.", file=sys.stderr)
        return 2

    meta_problem = _axis_metadata_problem(args.source)
    if meta_problem:
        print(f"error: {meta_problem}", file=sys.stderr)
        return 2

    from . import build_config as _build_config
    config_path = args.build_config or _build_config.path_for(args.source)
    if args.write_config:
        # From the current sidecars: the entries the studio already has, and
        # the two gftools steps folded into `instances`. `default` and
        # `axes.hide` are the designer's to fill in.
        try:
            written = _build_config.write(config_path,
                                          _build_config.from_sidecars(args.source))
        except OSError as exc:
            print(f"error: could not write {config_path}: {exc}", file=sys.stderr)
            return 2
        print(written)
        return 0
    cfg = None
    if config_path.exists():
        try:
            cfg = _build_config.load(config_path)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    elif args.build_config is not None:
        print(f"error: build config not found: {config_path}", file=sys.stderr)
        return 2

    try:
        # Flags override the config file (C4 vs C7).
        hidden_axes = (_parse_hide_axes(args.hide_axes) if args.hide_axes
                       else list((cfg or {}).get("axes", {}).get("hide", [])))
        default_location = (_parse_default_location(args.default_location)
                            if args.default_location
                            else (cfg or {}).get("default"))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    formats = []
    for fmt in (args.formats or (cfg or {}).get("formats") or ["ttf"]):
        if fmt not in formats:
            formats.append(fmt)
    from . import server, state
    if cfg is not None:
        # Design-authored state is read-only for the build; the config's
        # transform entries replace the committed sidecar FOR THIS RUN only.
        from .transforms import config as _tx_config
        _tx_config.set_override(_build_config.transform_entries(cfg))
    if args.workdir is not None:
        # Before _apply_source_path: it resolves the workdir from this.
        state.WORKDIR_OVERRIDE = args.workdir.resolve()
    try:
        server._apply_source_path(args.source)
    except Exception as exc:
        print(f"error: could not load {args.source}: {exc}", file=sys.stderr)
        return 2

    # A source that exists but does not parse is a LOAD error (exit 2), not a
    # build failure: say so here, with the parse message, instead of letting a
    # worker burn a build and report the generic "source cannot be read"
    # (P2). The cached loader means the build reuses this parse.
    if state.GLYPHS_PATH is not None:
        try:
            from . import source_font as _source_font
            _source_font.load_source_cached(state.GLYPHS_PATH)
        except Exception as exc:
            flat = " | ".join(ln for ln in str(exc).splitlines() if ln.strip())
            print(f"error: {args.source.name} could not be read: {flat}", file=sys.stderr)
            # _apply_source_path queues a build on load; this source can never
            # build, so don't leave a worker running behind an exit-2 CLI.
            state.JOBS.cancel_all()
            return 2

    # The build runs in a worker subprocess (docs/decisions/0003-worker-subprocess-finish-then-rebuild.md);
    # wait for the queue to drain before reading the outcome globals.
    server.JOBS.wait_for_idle()

    failures = _failure_reasons(state)
    if failures:
        for line in failures:
            print(f"error: {line}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    if not hidden_axes and not default_location and formats == ["ttf"]:
        # Plain build: the served font IS the product.
        dest = args.out / state.VARIABLE_FONT_PATH.name
        shutil.copy2(state.VARIABLE_FONT_PATH, dest)
        print(dest)
        return 0

    # Export options: same pipeline the studio's export dialog runs
    # (pipeline._export_pipeline) on the font just built — no second pipeline.
    from . import pipeline as _pipeline
    scratch = Path(tempfile.mkdtemp(prefix="export-",
                                    dir=str(_pipeline._get_preview_dir())))
    written = []
    try:
        for fmt in formats:
            try:
                produced = _pipeline._export_pipeline(
                    state.VARIABLE_FONT_PATH, hidden_axes, default_location,
                    scratch / fmt, format=fmt)
            except Exception as exc:
                print(f"error: export failed ({fmt}): {exc}", file=sys.stderr)
                return 1
            dest = args.out / Path(produced).name
            shutil.copy2(str(produced), str(dest))
            written.append(dest)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    for dest in written:
        print(dest)
    return 0


def _parse_hide_axes(value):
    """``--hide-axes XOPQ,XTRA`` → ["XOPQ", "XTRA"] (empty list when unset)."""
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _parse_default_location(value):
    """``--default wght=400,opsz=14`` → {"wght": 400.0, "opsz": 14.0}.

    Raises ValueError so the caller can answer exit 2 (usage): a typo here
    used to reach the exporter as an empty location and rebuild at the wrong
    origin.
    """
    if not value:
        return None
    out = {}
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        tag, sep, raw = part.partition("=")
        if not sep or not tag.strip():
            raise ValueError(f"--default expects tag=value, got {part!r}")
        try:
            out[tag.strip()] = float(raw)
        except ValueError as exc:
            raise ValueError(
                f"--default value for {tag.strip()!r} is not a number: {raw!r}"
            ) from exc
    if not out:
        raise ValueError("--default needs at least one tag=value")
    return out


def _failure_reasons(state_module) -> list:
    """Every reason this build must not ship, one entry per line.

    A ``degraded`` build (origin relocation failed, S1(b)) used to exit 1 as
    "build failed: unknown" because ``LAST_BUILD_ERROR`` is empty for it —
    the relocation message lives in its own global. Nothing recorded may be
    left out: in CI the exit status is the only thing anyone reads
    (findings.md, phase C item C3).
    """
    reasons = []
    status = state_module.LAST_BUILD_STATUS
    if not state_module.VARIABLE_FONT_PATH:
        reasons.append(
            f"build failed: {state_module.LAST_BUILD_ERROR or status or 'unknown'}"
        )
    elif status not in (None, "ok", "degraded"):
        reasons.append(
            f"build status {status}: {state_module.LAST_BUILD_ERROR or 'no reason recorded'}"
        )
    if status == "degraded" and not state_module.LAST_ORIGIN_RELOCATION_ERROR:
        # Degraded with nothing recorded: still not shippable, and still
        # better than a silent 0.
        reasons.append("build degraded: no reason recorded")
    if state_module.LAST_AVAR2_ERROR:
        # The plain-VF fallback built, but the avar2 mapping did not. In CI
        # that is a failure, not a warning — the mapped font IS the product.
        reasons.append(
            f"avar2 build failed (plain fallback built instead): {state_module.LAST_AVAR2_ERROR}"
        )
    if state_module.LAST_ORIGIN_RELOCATION_ERROR:
        # Degraded: the font built, but without the mapping the source
        # authored. This is the case that used to read "unknown".
        reasons.append(
            f"origin relocation failed (degraded build — the avar2 mapping is not "
            f"the authored one): {state_module.LAST_ORIGIN_RELOCATION_ERROR}"
        )
    if state_module.LAST_GRADE_ERROR:
        # Grade is declared in the sidecar but its braces were not generated:
        # the font built, without its GRAD axis. Passing that off as success
        # is how a missing axis reaches a release.
        reasons.append(
            f"grade generation failed (the font has no GRAD axis): {state_module.LAST_GRADE_ERROR}"
        )
    if state_module.LAST_SOURCE_TRANSFORM_ERROR:
        # An enabled source-stage transform (round_corners) did not run: the
        # font built from the un-transformed source.
        reasons.append(
            f"source transform failed (the font built without it): "
            f"{state_module.LAST_SOURCE_TRANSFORM_ERROR}"
        )
    return reasons


if __name__ == "__main__":
    main()
