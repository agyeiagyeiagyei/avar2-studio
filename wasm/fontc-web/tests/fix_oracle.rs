//! Oracle for the fixer transforms: `fix_unhinted` (gasp+prep bytes)
//! and `fix_fvar_instances` (GF-spec instance list) must match gftools
//! exactly on the WasmTest variable font (wght-only fvar, no instances).

use std::path::Path;
use std::process::Command;

#[test]
fn fixers_match_gftools_oracle() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());
    let font_path = manifest.join("../../frontend/e2e/fixtures/WasmTest-VF.ttf");
    let before = std::fs::read(&font_path).expect("read WasmTest-VF.ttf");

    let transforms = serde_json::json!([
        { "type": "fix_unhinted", "enabled": true, "params": {} },
        { "type": "fix_instances", "enabled": true, "params": {} },
    ]);
    let after = fontc_web::apply_transforms(before.clone(), &transforms.to_string(), "")
        .expect("apply transforms");

    std::fs::write("/tmp/fix-before.ttf", &before).expect("write before");
    std::fs::write("/tmp/fix-after.ttf", &after).expect("write after");
    let comparator = manifest.join("spike/compare_fix.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg("/tmp/fix-before.ttf")
        .arg("/tmp/fix-after.ttf")
        .status()
        .expect("run comparator");
    assert!(status.success(), "fix oracle comparison failed");

    // Idempotence: a second pass must not change the bytes.
    let twice = fontc_web::apply_transforms(after.clone(), &transforms.to_string(), "")
        .expect("apply transforms (second pass)");
    assert_eq!(after, twice, "fixer transforms must be idempotent");
}
