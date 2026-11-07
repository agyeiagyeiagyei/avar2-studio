# Kinetikern2 engine (vendored)

The Rust engine of [Kinetikern2](https://github.com/bghryct/kinetikern2) by
Mirko Velimirovic, MIT (see LICENSE here), copied from its `engine/` folder at
upstream commit `7c42a12` (UPSTREAM_COMMIT) without changes. It spaces a font
from a kinetic equilibrium over glyph triplets; the studio's Kinetikern tab
runs its Pass 1 (sidebearings) on instances of the built variable font.

Build the library the studio loads (`src/avar2_studio/kinetikern/` finds it
at `target/release/libkinetikern2.dylib`, or wherever $AVAR2_KK2_DYLIB points):

    cd native/kinetikern2 && MACOSX_DEPLOYMENT_TARGET=11.0 cargo build --release

Needs Rust 1.74+. The ctypes bridge in `src/avar2_studio/kinetikern/bridge.py`
is the upstream plugin's `kk2_bridge.py` at the same commit; keep the two in
step (the bridge checks the ABI version and struct sizes on load).
