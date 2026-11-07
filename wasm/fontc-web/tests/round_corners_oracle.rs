//! Oracle comparison for `round_corners_source`: run the Rust engine on
//! the CrispyMini example source and compare the transformed source
//! against the desktop Python engine (corner_rounding.round_font) run
//! with identical parameters — node for node. Rounding is ALWAYS the
//! ROND axis now; the two parameter sets cover the plain formula and a
//! per-master override.
//!
//! Prerequisites:
//!   - python with glyphsLib + avar2_studio: $AVAR2_PYTHON
//!     (default: the repo .venv)

use std::path::PathBuf;
use std::process::Command;
mod common;

const PARAMS_OVERRIDE: &str = r#"{"outer_pct":20.0,"inner_pct":5.0,"outer_xtra_pct":3.0,"inner_xtra_pct":1.0,"outer_min":2.0,"inner_min":1.0,"master_overrides":{"XTRA3330-XOPQ2-YOPQ2":{"outer":150.0}}}"#;
const PARAMS_PLAIN: &str = r#"{"outer_pct":20.0,"inner_pct":5.0,"outer_xtra_pct":3.0,"inner_xtra_pct":1.0,"outer_min":2.0,"inner_min":1.0}"#;

#[test]
fn round_corners_matches_the_desktop_engine() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source_path = manifest.join("../../examples/crispy-mini/sources/CrispyMini.glyphs");
    let python = common::python();
    let tmp = common::tmp_dir();

    let source = std::fs::read_to_string(&source_path).expect("read CrispyMini");

    let bake = fontc_web::round_corners_source(source.clone(), PARAMS_OVERRIDE, None)
        .expect("rust override round");
    let axis = fontc_web::round_corners_source(source, PARAMS_PLAIN, None)
        .expect("rust plain round");
    std::fs::write(tmp.join("round-bake-rs.glyphs"), &bake).expect("write bake output");
    std::fs::write(tmp.join("round-axis-rs.glyphs"), &axis).expect("write axis output");
    std::fs::write(tmp.join("round-bake-params.json"), PARAMS_OVERRIDE).expect("write params");
    std::fs::write(tmp.join("round-axis-params.json"), PARAMS_PLAIN).expect("write params");

    let comparator = manifest.join("spike/compare_round_corners.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&source_path)
        .arg(tmp.join("round-bake-rs.glyphs"))
        .arg(tmp.join("round-bake-params.json"))
        .arg(tmp.join("round-axis-rs.glyphs"))
        .arg(tmp.join("round-axis-params.json"))
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed");
}

/// Guardrail: a source without XOPQ/XTRA must be refused with a clear
/// message, not silently left sharp (the inert-transform bug class).
#[test]
fn round_corners_refuses_a_source_without_parametric_axes() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source = std::fs::read_to_string(manifest.join("../../frontend/e2e/fixtures/WasmTest.glyphs"))
        .expect("read WasmTest.glyphs");
    let msg = fontc_web::round_corners_source_result(&source, PARAMS_PLAIN, None)
        .expect_err("wght-only source must be refused");
    assert!(msg.contains("XOPQ"), "unhelpful message: {msg}");
}

/// Overlap-formed counters (Crispy's b/n/q pattern: a single
/// self-crossing contour, a folded counter path): classification follows
/// the rendered fill, not contour identity. CrispyMini has no such
/// glyph, so the main oracle cannot catch drift here — this one can.
const PARAMS_OVERRIDE_MINI: &str = r#"{"outer_pct":20.0,"inner_pct":5.0,"outer_xtra_pct":3.0,"inner_xtra_pct":1.0,"outer_min":2.0,"inner_min":1.0,"master_overrides":{"Bold":{"outer":150.0}}}"#;

#[test]
fn round_corners_matches_the_desktop_engine_on_folded_counters() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source_path = manifest.join("tests/fixtures/FoldedMini.glyphs");
    let python = common::python();
    let tmp = common::tmp_dir();

    let source = std::fs::read_to_string(&source_path).expect("read FoldedMini");

    let bake = fontc_web::round_corners_source(source.clone(), PARAMS_OVERRIDE_MINI, None)
        .expect("rust override round");
    let axis = fontc_web::round_corners_source(source, PARAMS_PLAIN, None)
        .expect("rust plain round");
    std::fs::write(tmp.join("round-fold-bake-rs.glyphs"), &bake).expect("write bake output");
    std::fs::write(tmp.join("round-fold-axis-rs.glyphs"), &axis).expect("write axis output");
    std::fs::write(tmp.join("round-fold-bake-params.json"), PARAMS_OVERRIDE_MINI).expect("write params");
    std::fs::write(tmp.join("round-fold-axis-params.json"), PARAMS_PLAIN).expect("write params");

    let comparator = manifest.join("spike/compare_round_corners.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&source_path)
        .arg(tmp.join("round-fold-bake-rs.glyphs"))
        .arg(tmp.join("round-fold-bake-params.json"))
        .arg(tmp.join("round-fold-axis-rs.glyphs"))
        .arg(tmp.join("round-fold-axis-params.json"))
        .status()
        .expect("run comparator");
    assert!(status.success(), "folded-counter oracle comparison failed");
}
