//! Correction layers in `apply_control_axes`: a brace layer carrying a
//! `target` renders the glyph as if it sat at that parametric point.
//!
//! Three invariants are checked against fontTools, on the Crispy demo font
//! whose wght 900->1000 ramp runs from XTRA 1571 / XOPQ 700 / YOPQ 275 to
//! XTRA 47 / XOPQ 1462 / YOPQ 275:
//!
//!   1. APPLIED   - at the authored corner with the axis at max, a covered
//!                  glyph equals the glyph instanced at the target point.
//!   2. SCOPED    - a glyph the axis does not cover is untouched.
//!   3. PINNED    - away from the authored corner the correction fades out.
//!                  This is the one that needs the cancel tuple: the corner
//!                  sits at XTRA 47, the default master's own XTRA, so the
//!                  axis is omitted from the tuple and would otherwise leave
//!                  the correction applying at every width.
//!
//! Font: $CORRECTION_FONT (default the Crispy demo2 build). Python with
//! fontTools: $AVAR2_PYTHON.

use std::process::Command;

/// Lowercase `n` and `o` corrected at the ramp endpoint to render as if at
/// wght 990 (XTRA 199 / XOPQ 1386). `H` is deliberately NOT covered.
const CORRECTION_JSON: &str = r#"[{"tag":"lcad","name":"Lowercase adjust","min":0.0,"default":0.0,"max":100.0,"layers":[
{"glyph":"n","location":{"XTRA":47.0,"XOPQ":1462.0,"YOPQ":275.0,"lcad":100.0},"target":{"XTRA":199.0,"XOPQ":1386.0}},
{"glyph":"o","location":{"XTRA":47.0,"XOPQ":1462.0,"YOPQ":275.0,"lcad":100.0},"target":{"XTRA":199.0,"XOPQ":1386.0}}
]}]"#;

const CHECK_PY: &str = r#"
import json, sys
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.recordingPen import RecordingPen

corrected, base = sys.argv[1], sys.argv[2]

def pts(path, loc, name):
    f = instantiateVariableFont(TTFont(path), loc, inplace=False)
    pen = RecordingPen()
    f.getGlyphSet()[name].draw(pen)
    return [p for _, args in pen.value for p in args]

def dev(a, b):
    if len(a) != len(b):
        return float("inf")
    return max(max(abs(p[0]-q[0]), abs(p[1]-q[1])) for p, q in zip(a, b))

CORNER = {"XTRA": 47, "XOPQ": 1462, "YOPQ": 275}
TARGET = {"XTRA": 199, "XOPQ": 1386, "YOPQ": 275}
FAR    = {"XTRA": 1571, "XOPQ": 700, "YOPQ": 275}   # the wght-900 end
WIDE   = {"XTRA": 1715, "XOPQ": 1462, "YOPQ": 275}  # opposite corner

out = {"applied": {}, "scoped": {}, "pinned": {}}
for g in ("n", "o"):
    on  = pts(corrected, {**CORNER, "lcad": 100}, g)
    tgt = pts(base, TARGET, g)
    out["applied"][g] = dev(on, tgt)
    for label, loc in (("far", FAR), ("wide", WIDE)):
        a = pts(corrected, {**loc, "lcad": 100}, g)
        b = pts(corrected, {**loc, "lcad": 0}, g)
        out["pinned"][f"{g}@{label}"] = dev(a, b)
for g in ("H", "A"):
    a = pts(corrected, {**CORNER, "lcad": 100}, g)
    b = pts(corrected, {**CORNER, "lcad": 0}, g)
    out["scoped"][g] = dev(a, b)
print(json.dumps(out))
"#;

#[test]
fn correction_layers_render_at_their_target() {
    let font_path = std::env::var("CORRECTION_FONT").unwrap_or_else(|_| {
        "/private/tmp/claude-501/-Users-agyei-Documents-Crispy/\
         6f7067c1-8fc9-43cd-a8d7-e9eea399785b/scratchpad/demo2-build/font.ttf"
            .replace(char::is_whitespace, "")
    });
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());
    if !std::path::Path::new(&font_path).exists() {
        eprintln!("skipping: no font at {font_path} (set CORRECTION_FONT)");
        return;
    }

    let font_bytes = std::fs::read(&font_path).expect("read demo font");
    let corrected = fontc_web::apply_control_axes(font_bytes, CORRECTION_JSON)
        .expect("apply_control_axes with a correction target");
    std::fs::write("/tmp/correction-out.ttf", &corrected).expect("write corrected font");
    std::fs::write("/tmp/correction-check.py", CHECK_PY).expect("write checker");

    let out = Command::new(&python)
        .args(["/tmp/correction-check.py", "/tmp/correction-out.ttf", &font_path])
        .output()
        .expect("run fontTools checker");
    assert!(
        out.status.success(),
        "checker failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let report: serde_json::Value =
        serde_json::from_slice(&out.stdout).expect("checker emitted json");

    // 1. The correction lands exactly on the target (integer-rounding slack).
    for g in ["n", "o"] {
        let d = report["applied"][g].as_f64().unwrap();
        assert!(d <= 2.0, "'{g}' should equal the glyph at its target, off by {d} units");
    }
    // 2. Uncovered glyphs are untouched.
    for g in ["H", "A"] {
        let d = report["scoped"][g].as_f64().unwrap();
        assert!(d == 0.0, "'{g}' is not covered but moved {d} units");
    }
    // 3. The correction is pinned: it fades to nothing away from its corner.
    //    Without the cancel tuple these run into the hundreds of units.
    for key in ["n@far", "o@far", "n@wide", "o@wide"] {
        let d = report["pinned"][key].as_f64().unwrap();
        assert!(d <= 40.0, "correction leaked {d} units at {key}");
    }
}
