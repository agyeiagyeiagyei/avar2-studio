//! Oracle for `pin_corner`: pinning (47, 700, 300) with scaffold
//! (47, 350, 255) on crispy-test must hold the ghost corner up
//! (darkness there ≈ the scaffold's) WITHOUT touching the default, and
//! WITHOUT bleeding onto the mastered corner (1665, 700, 300) — the
//! property that makes this a master add, not a tent injection.
//! Ground truth: Pillow/FreeType darkness on the before/after fonts.

use std::path::Path;
use std::process::Command;

#[test]
fn pin_holds_corner_without_bleeding() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());
    let source_path = manifest.join("../../frontend/e2e/fixtures/Crispy-test.glyphs");
    let source = std::fs::read_to_string(&source_path).expect("read crispy-test fixture");
    let before = fontc_web::compile_glyphs(source).expect("compile");

    let request = serde_json::json!({
        "corner": {"XTRA": 47.0, "XOPQ": 700.0, "YOPQ": 300.0},
        "scaffold": {"XTRA": 47.0, "XOPQ": 350.0, "YOPQ": 255.0},
    });
    let after = fontc_web::pin_corner(before.clone(), &request.to_string()).expect("pin_corner");
    assert_ne!(before, after, "pin must change the font");

    std::fs::write("/tmp/pin-before.ttf", &before).expect("write before");
    std::fs::write("/tmp/pin-after.ttf", &after).expect("write after");

    let comparator = manifest.join("spike/compare_pin.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg("/tmp/pin-before.ttf")
        .arg("/tmp/pin-after.ttf")
        .status()
        .expect("run comparator");
    assert!(status.success(), "pin oracle comparison failed");
}
