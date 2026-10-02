//! Oracle comparison for `round_corners_source`: run the Rust engine on
//! the CrispyMini example source and compare the transformed source
//! against the desktop Python engine (corner_rounding.round_font) run
//! with identical parameters — node for node, both in bake mode (with a
//! per-master override) and in ROND-axis mode.
//!
//! Prerequisites:
//!   - python with glyphsLib + avar2_studio: $AVAR2_PYTHON
//!     (default: the repo .venv)

use std::path::PathBuf;
use std::process::Command;

const PARAMS_BAKE: &str = r#"{"outer_pct":20.0,"inner_pct":5.0,"outer_xtra_pct":3.0,"inner_xtra_pct":1.0,"outer_min":2.0,"inner_min":1.0,"master_overrides":{"XTRA3330-XOPQ2-YOPQ2":{"outer":150.0}}}"#;
const PARAMS_AXIS: &str = r#"{"outer_pct":20.0,"inner_pct":5.0,"outer_xtra_pct":3.0,"inner_xtra_pct":1.0,"outer_min":2.0,"inner_min":1.0,"rounding_axis":true,"axis_max":100.0}"#;

#[test]
fn round_corners_matches_the_desktop_engine() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source_path = manifest.join("../../examples/crispy-mini/sources/CrispyMini.glyphs");
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());

    let source = std::fs::read_to_string(&source_path).expect("read CrispyMini");

    let bake = fontc_web::round_corners_source(source.clone(), PARAMS_BAKE, None)
        .expect("rust bake round");
    let axis = fontc_web::round_corners_source(source, PARAMS_AXIS, None)
        .expect("rust axis round");
    std::fs::write("/tmp/round-bake-rs.glyphs", &bake).expect("write bake output");
    std::fs::write("/tmp/round-axis-rs.glyphs", &axis).expect("write axis output");
    std::fs::write("/tmp/round-bake-params.json", PARAMS_BAKE).expect("write params");
    std::fs::write("/tmp/round-axis-params.json", PARAMS_AXIS).expect("write params");

    let comparator = manifest.join("spike/compare_round_corners.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&source_path)
        .arg("/tmp/round-bake-rs.glyphs")
        .arg("/tmp/round-bake-params.json")
        .arg("/tmp/round-axis-rs.glyphs")
        .arg("/tmp/round-axis-params.json")
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed");
}
