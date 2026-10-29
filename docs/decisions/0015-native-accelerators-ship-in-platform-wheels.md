# 0015 — The native accelerators ship inside the platform wheels

- **Status:** Accepted (T3.9, user decision 2026-10-17; built in `7c74265`, 2026-10-18)
- **Date:** 2026-10-18
- **Area:** `hatch_build.py`, `.github/workflows/release.yml`,
  `src/avar2_studio/kinetikern/`, `src/avar2_studio/transforms/builtin_spac_widthaware.py`,
  `src/avar2_studio/doctor.py`

## Context

Two accelerators are Rust, built with cargo, and gitignored:

- **`spac-inject`** (from `wasm/fontc-web`, the same SPAC engine the static demo
  runs) splices spacing tuples with write-fonts' compact `gvar` encoding. The
  fontTools fallback re-encodes the whole table and overflows `gvar`'s 64 KB
  per-glyph header ceiling on twin-heavy fonts (slant × rounding × grade), so
  the fallback is a correctness limit, not a taste preference.
- **`libkinetikern2`** (from `native/kinetikern2`) is the per-style spacing
  engine. There is **no pure-Python fallback**: without the dylib Kinetikern
  cannot run at all.

Both were reachable only through the environment overrides `AVAR2_SPAC_INJECT`
and `AVAR2_KK2_DYLIB` pointing at a local cargo build. An installed wheel
therefore had neither: SPAC silently degraded to the fontTools path and
Kinetikern was simply unavailable, with no supported way for a user to obtain
either — the accelerators were a source-checkout privilege.

## Decision

**The wheels carry the binaries.** `release.yml`'s platform matrix builds both
with cargo and stages them into `src/avar2_studio/bin/`;
`hatch_build.py`'s `NativeAcceleratorsHook` force-includes that directory as
`avar2_studio/bin/` and tags the wheel `py3-none-<plat>`, non-pure, so pip
installs it into platlib where the lookup already looks.

**The discovery order is fixed and identical in both modules:**

1. the environment override (`AVAR2_SPAC_INJECT`, `AVAR2_KK2_DYLIB`), used when
   the named file exists;
2. the wheel-carried copy at `avar2_studio/bin/<binary>`;
3. the repo build (`wasm/fontc-web/target/release/spac-inject`,
   `native/kinetikern2/target/release/libkinetikern2.{dylib,so}`), for a source
   checkout.

A tree without binaries still builds the pure `py3-none-any` wheel: SPAC falls
back to fontTools, Kinetikern reports itself unavailable. The force-include
lives in the build hook rather than in `pyproject.toml` because hatchling raises
`FileNotFoundError` for a missing force-include source, and `bin/` is absent on
a clean checkout.

## Consequences

- A release produces one wheel per platform instead of one universal wheel;
  installs from git stay pure-Python until `cargo build` has run locally.
- `avar2-studio doctor` gained `check_spac_injector` and
  `check_kinetikern_engine`, which report the path and how it was found (`via
  $AVAR2_…` when the override is in play) and name a broken override instead of
  silently falling through. `doctor --strict` fails when either is missing, so
  CI can assert a fresh install is complete — `release.yml` does that on the
  built wheel.
- The overrides stay first in the order: they are the escape hatch for testing
  a locally rebuilt binary.
- Kinetikern availability is now a packaging property rather than a user setup
  task, which is why its absence is an error in the paths that need it and not
  a skip.

## Where it lives

`hatch_build.py` (`NativeAcceleratorsHook.initialize`); `release.yml`'s
`dist-platform-*` job (cargo builds, staging into `src/avar2_studio/bin/`,
`doctor --strict` on the installed wheel); `kinetikern/__init__.py::engine_path`;
`transforms/builtin_spac_widthaware.py::_native_injector`;
`doctor.py::check_spac_injector` / `check_kinetikern_engine`. Tests:
`tests/test_kinetikern.py`, `tests/test_kinetikern_inject.py`,
`tests/test_spac_fallback_log.py`, `tests/test_instance_spac_persistence.py`.
