//! Oracle for the fixer transforms: `fix_unhinted` (gasp+prep bytes)
//! and `fix_fvar_instances` (GF-spec instance list) must match gftools
//! exactly on the WasmTest variable font (wght-only fvar, no instances).

use std::path::Path;
use std::process::Command;

#[test]
fn fixers_match_gftools_oracle() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| {
        let venv = concat!(env!("CARGO_MANIFEST_DIR"), "/../../.venv/bin/python");
        if std::path::Path::new(venv).exists() { venv.to_string() } else { "python3".to_string() }
    });
    let font_path = manifest.join("../../frontend/e2e/fixtures/WasmTest-VF.ttf");
    let before = std::fs::read(&font_path).expect("read WasmTest-VF.ttf");

    let transforms = serde_json::json!([
        { "type": "fix_unhinted", "enabled": true, "params": {} },
        { "type": "fix_instances", "enabled": true, "params": {} },
    ]);
    let after = fontc_web::apply_transforms(before.clone(), &transforms.to_string(), "")
        .expect("apply transforms");

    std::fs::create_dir_all(concat!(env!("CARGO_MANIFEST_DIR"), "/target/tmp")).expect("create target/tmp");
    std::fs::write(format!("{}/target/tmp/fix-before.ttf", env!("CARGO_MANIFEST_DIR")), &before).expect("write before");
    std::fs::write(format!("{}/target/tmp/fix-after.ttf", env!("CARGO_MANIFEST_DIR")), &after).expect("write after");
    let comparator = manifest.join("spike/compare_fix.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(format!("{}/target/tmp/fix-before.ttf", env!("CARGO_MANIFEST_DIR")))
        .arg(format!("{}/target/tmp/fix-after.ttf", env!("CARGO_MANIFEST_DIR")))
        .status()
        .expect("run comparator");
    assert!(status.success(), "fix oracle comparison failed");

    // Idempotence: a second pass must not change the bytes.
    let twice = fontc_web::apply_transforms(after.clone(), &transforms.to_string(), "")
        .expect("apply transforms (second pass)");
    assert_eq!(after, twice, "fixer transforms must be idempotent");
}

/// The italic doubling: on a font with a slnt axis, fix_instances must
/// produce gftools' Roman + Italic instance sets (Italic pinned at the
/// axis minimum, "Regular Italic" → "Italic") — compared against real
/// gftools on a slanted WasmTest build.
#[test]
fn fix_instances_doubles_like_gftools_on_a_slnt_font() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| {
        let venv = concat!(env!("CARGO_MANIFEST_DIR"), "/../../.venv/bin/python");
        if std::path::Path::new(venv).exists() { venv.to_string() } else { "python3".to_string() }
    });
    let source = std::fs::read_to_string(manifest.join("../../frontend/e2e/fixtures/WasmTest.glyphs"))
        .expect("read WasmTest.glyphs");
    let slanted = fontc_web::slant_source(source, r#"{}"#).expect("slant");
    let before = fontc_web::compile_glyphs(slanted).expect("compile slanted WasmTest");

    let transforms = serde_json::json!([
        { "type": "fix_unhinted", "enabled": true, "params": {} },
        { "type": "fix_instances", "enabled": true, "params": {} },
    ]);
    let after = fontc_web::apply_transforms(before.clone(), &transforms.to_string(), "")
        .expect("apply transforms");

    std::fs::create_dir_all(concat!(env!("CARGO_MANIFEST_DIR"), "/target/tmp")).expect("create target/tmp");
    std::fs::write(format!("{}/target/tmp/fix-slnt-before.ttf", env!("CARGO_MANIFEST_DIR")), &before).expect("write before");
    std::fs::write(format!("{}/target/tmp/fix-slnt-after.ttf", env!("CARGO_MANIFEST_DIR")), &after).expect("write after");
    let comparator = manifest.join("spike/compare_fix.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(format!("{}/target/tmp/fix-slnt-before.ttf", env!("CARGO_MANIFEST_DIR")))
        .arg(format!("{}/target/tmp/fix-slnt-after.ttf", env!("CARGO_MANIFEST_DIR")))
        .status()
        .expect("run comparator");
    assert!(status.success(), "slnt fix oracle comparison failed");
}
