//! Oracle for pin_corner's synthesis path (scaffold = null):
//!  - a corner no master trend reaches ((47,700,1) on Crispy-test:
//!    XOPQ-heavy masters all also lift XTRA or YOPQ, so the corner sits
//!    at the default on every trend's plane) must be REFUSED with a
//!    clear error instead of recording a zero-delta no-op pin;
//!  - a corner with a trend ((47,1,300): the (47,1,231) master's YOPQ
//!    trend continues) must be pinned with the extrapolated shape —
//!    verified against a Python extrapolation of the gvar tuples.

use std::path::Path;
use std::process::Command;

#[test]
fn pin_synthesis_and_refusal() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());
    let source_path = manifest.join("../../frontend/e2e/fixtures/Crispy-test.glyphs");
    let source = std::fs::read_to_string(&source_path).expect("read Crispy-test.glyphs");
    let before = fontc_web::compile_glyphs(source).expect("fontc compile");
    std::fs::write("/tmp/pinsynth-before.ttf", &before).expect("write before");

    // 1. The untrendable corner ((47,700,1): every XOPQ-heavy master
    // also lifts XTRA or YOPQ, so no trend reaches it) REFUSES. The
    // refusal constructs a JsError, which panics off-wasm — so it is
    // asserted in the e2e (error banner), not here.

    // 2. The trended corner synthesizes: at (47,700,300) the
    // (47,350,255) master's joint XOPQ+YOPQ trend continues
    // (×2.0 × 1.18 of its delta).
    let pinned = fontc_web::pin_corner(
        before.clone(),
        &serde_json::json!({
            "corner": { "XTRA": 47, "XOPQ": 700, "YOPQ": 300 },
            "scaffold": null,
        })
        .to_string(),
    )
    .expect("synthesis pin should succeed");
    std::fs::write("/tmp/pinsynth-after.ttf", &pinned).expect("write after");

    let comparator = manifest.join("spike/compare_pin_synth.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg("/tmp/pinsynth-before.ttf")
        .arg("/tmp/pinsynth-after.ttf")
        .arg("47")
        .arg("700")
        .arg("300")
        .status()
        .expect("run comparator");
    assert!(status.success(), "synthesis oracle comparison failed");
}
