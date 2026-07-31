//! Oracle comparison for `apply_control_axes` / `apply_grade`: run them
//! on the CrispyMini spike font and compare the injected brace geometry
//! against fontTools' own instancer (spike/compare_braces.py).
//!
//! Three outputs are checked:
//!   - control-only:  spike → apply_control_axes
//!   - grade-only:    spike → apply_grade
//!   - full pipeline: spike → add_avar2 → control → grade (composition
//!     with the avar2 table present)
//!
//! Prerequisites (paths overridable via env, same defaults as
//! avar2_oracle.rs):
//!   - spike font:  $AVAR2_SPIKE_FONT  (default /tmp/fontc-wasm-spike.ttf)
//!   - mappings:    $AVAR2_CSV         (default the studio's CrispyMini-avar.csv)
//!   - python with fontTools + avar2_studio: $AVAR2_PYTHON

use std::path::Path;
use std::process::Command;

/// One control axis with a single computed brace: glyph `e` at
/// XTRA=1000, engaged at crbr=100 (edge-default axis).
const CONTROL_JSON: &str = r#"[{"tag":"crbr","name":"Crossbar","min":0.0,"default":0.0,"max":100.0,"layers":[{"glyph":"e","location":{"XTRA":1000.0}}]}]"#;

/// Grade enabled with one graded instance (a heavy one, so the effect
/// is large, at a mid-XTRA location so the dark/light advances differ
/// from the base advance and the equalisation shift is non-zero),
/// pct 0.3.
const GRADE_JSON: &str = r#"{"version":1,"enabled":true,"default_pct":0.25,"instances":[{"name":"Narrow Heavy 12","pct":0.3}]}"#;
const COORDS_JSON: &str =
    r#"{"Narrow Heavy 12":{"XTRA":222.8,"XOPQ":279.6,"YOPQ":250.2}}"#;

#[test]
fn braces_match_fonttools_oracle() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let font_path = std::env::var("AVAR2_SPIKE_FONT")
        .unwrap_or_else(|_| "/tmp/fontc-wasm-spike.ttf".to_string());
    let csv_path = std::env::var("AVAR2_CSV").unwrap_or_else(|_| {
        manifest
            .join("../../examples/crispy-mini/sources/CrispyMini-avar.csv")
            .to_string_lossy()
        .into_owned()
    });
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());

    let font_bytes = std::fs::read(&font_path).expect("read spike font");
    let csv = std::fs::read_to_string(&csv_path).expect("read mappings csv");

    let control = fontc_web::apply_control_axes(font_bytes.clone(), CONTROL_JSON)
        .expect("apply_control_axes");
    let grade =
        fontc_web::apply_grade(font_bytes.clone(), GRADE_JSON, COORDS_JSON).expect("apply_grade");
    let full = fontc_web::add_avar2(font_bytes.clone(), &csv, None).expect("add_avar2");
    let full = fontc_web::apply_control_axes(full, CONTROL_JSON).expect("pipeline control");
    let full = fontc_web::apply_grade(full, GRADE_JSON, COORDS_JSON).expect("pipeline grade");

    std::fs::write("/tmp/braces-control.ttf", &control).expect("write control output");
    std::fs::write("/tmp/braces-grade.ttf", &grade).expect("write grade output");
    std::fs::write("/tmp/braces-full.ttf", &full).expect("write full output");
    std::fs::write("/tmp/braces-control.json", CONTROL_JSON).expect("write control json");
    std::fs::write("/tmp/braces-grade.json", GRADE_JSON).expect("write grade json");
    std::fs::write("/tmp/braces-coords.json", COORDS_JSON).expect("write coords json");

    let comparator = manifest.join("spike/compare_braces.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&font_path)
        .arg("/tmp/braces-control.ttf")
        .arg("/tmp/braces-grade.ttf")
        .arg("/tmp/braces-full.ttf")
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed");
}
