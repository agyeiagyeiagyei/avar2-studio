//! Oracle comparison for `slant_source`: run the Rust engine on the
//! CrispyMini example source and compare the transformed source
//! against the desktop Python engine (slanting.slant_font) run with
//! identical parameters — node for node, components and anchors
//! included. Two parameter sets cover the mean-advance rule alone and
//! a per-master angle override.
//!
//! Prerequisites:
//!   - python with glyphsLib + avar2_studio: $AVAR2_PYTHON
//!     (default: the repo .venv)

use std::path::PathBuf;
use std::process::Command;
mod common;

const PARAMS_PLAIN: &str = r#"{"narrow_angle":11.0,"wide_angle":6.0}"#;
const PARAMS_OVERRIDE: &str = r#"{"narrow_angle":12.0,"wide_angle":4.0,"master_angles":{"XTRA94-XOPQ2-YOPQ2":20.0}}"#;

#[test]
fn slant_matches_the_desktop_engine() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source_path = manifest.join("../../examples/crispy-mini/sources/CrispyMini.glyphs");
    let python = common::python();
    let tmp = common::tmp_dir();

    let source = std::fs::read_to_string(&source_path).expect("read CrispyMini");

    let plain = fontc_web::slant_source(source.clone(), PARAMS_PLAIN).expect("rust plain slant");
    let over = fontc_web::slant_source(source, PARAMS_OVERRIDE).expect("rust override slant");
    std::fs::write(tmp.join("slant-plain-rs.glyphs"), &plain).expect("write plain output");
    std::fs::write(tmp.join("slant-override-rs.glyphs"), &over).expect("write override output");
    std::fs::write(tmp.join("slant-plain-params.json"), PARAMS_PLAIN).expect("write params");
    std::fs::write(tmp.join("slant-override-params.json"), PARAMS_OVERRIDE).expect("write params");

    let comparator = manifest.join("spike/compare_slant.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&source_path)
        .arg(tmp.join("slant-plain-rs.glyphs"))
        .arg(tmp.join("slant-plain-params.json"))
        .arg(tmp.join("slant-override-rs.glyphs"))
        .arg(tmp.join("slant-override-params.json"))
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed");
}
