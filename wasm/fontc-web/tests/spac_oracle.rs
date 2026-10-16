//! Oracle comparison for `apply_transforms` (SPAC injection): run both
//! variants on the CrispyMini spike font and compare per-glyph advances
//! at SPAC min/default/max against the REAL transforms — the studio's
//! `WidthAwareSpacTransform` and gftools `gen-spac` via `SpacTransform`
//! (spike/compare_spac.py).
//!
//! Fixtures come from tests/common (generated from examples/crispy-mini;
//! $AVAR2_SPIKE_FONT / $AVAR2_CSV / $AVAR2_PYTHON override).

use std::path::Path;
use std::process::Command;

mod common;

/// The e2e bundle's exact width-aware parameter set.
const TRANSFORMS_WA: &str = r#"[{"type":"spac_widthaware","enabled":true,"params":{"min":-20,"max":40,"bias":1.0,"scale":1.25}}]"#;
/// Uniform gftools SPAC, same range.
const TRANSFORMS_UNIFORM: &str =
    r#"[{"type":"spac","enabled":true,"params":{"min":-20,"max":40}}]"#;
/// Both variants present but disabled: a no-op.
const TRANSFORMS_DISABLED: &str = r#"[{"type":"spac_widthaware","enabled":false,"params":{"min":-20,"max":40,"bias":1.0,"scale":1.25}},{"type":"spac","enabled":false,"params":{"min":-20,"max":40}}]"#;
/// Both SPAC injectors enabled: rejected (one injector per axis).
const TRANSFORMS_BOTH: &str = r#"[{"type":"spac","enabled":true,"params":{"min":-20,"max":40}},{"type":"spac_widthaware","enabled":true,"params":{"min":-20,"max":40}}]"#;

/// avar2 CSV with a SPAC column: per-instance SPAC overrides for two of
/// CrispyMini's instances (values inside the -20..40 range).
const OVERRIDE_CSV: &str =
    "Instance Name,XTRA,SPAC\nNarrow Thin 144,94.0,10.0\nUltra Wide Thin 144,2895.4,-15.0\n";

#[test]
fn spac_matches_studio_oracle() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let font_path = common::spike_font();
    let csv_path = common::mappings_csv();
    let python = common::python();

    let font_bytes = std::fs::read(&font_path).expect("read spike font");
    let csv = std::fs::read_to_string(&csv_path).expect("read mappings csv");

    // Disabled entries are a no-op (byte-identical passthrough).
    let out = fontc_web::apply_transforms(font_bytes.clone(), TRANSFORMS_DISABLED, &csv)
        .expect("disabled transforms");
    assert_eq!(out, font_bytes, "disabled transforms must not touch the font");

    // Both SPAC injectors enabled at once: rejected, not a corrupt font.
    // (JsError can't be constructed off-wasm — the error path panics in
    // native tests — so assert via catch_unwind; the eprintln from
    // err() above shows the message.)
    let bytes = font_bytes.clone();
    let rejected = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _ = fontc_web::apply_transforms(bytes, TRANSFORMS_BOTH, &csv);
    }));
    assert!(rejected.is_err(), "enabling both SPAC injectors must fail");

    let wa = fontc_web::apply_transforms(font_bytes.clone(), TRANSFORMS_WA, &csv)
        .expect("width-aware SPAC");
    let uni = fontc_web::apply_transforms(font_bytes.clone(), TRANSFORMS_UNIFORM, &csv)
        .expect("uniform SPAC");

    // The compiled font has no fvar instances (CrispyMini.glyphs
    // declares none), so the per-instance override path needs a font
    // that HAS instances: synthesize one, then apply with the plain
    // CSV (pins 0.0) and the SPAC-column CSV (pins the overrides).
    let tmp = common::tmp_dir();
    let instanced_path = tmp.join("spac-instanced-input.ttf");
    let maker = manifest.join("spike/make_instanced_font.py");
    let status = Command::new(&python)
        .arg(maker)
        .arg(&font_path)
        .arg(&instanced_path)
        .status()
        .expect("run instanced-font fixture builder");
    assert!(status.success(), "instanced-font fixture build failed");
    let instanced = std::fs::read(&instanced_path).expect("read instanced font");
    let wa_inst_plain = fontc_web::apply_transforms(instanced.clone(), TRANSFORMS_WA, &csv)
        .expect("width-aware SPAC on instanced font");
    let wa_inst_ovr = fontc_web::apply_transforms(instanced.clone(), TRANSFORMS_WA, OVERRIDE_CSV)
        .expect("width-aware SPAC with instance overrides");

    let wa_path = tmp.join("spac-wasm-widthaware.ttf");
    let uni_path = tmp.join("spac-wasm-uniform.ttf");
    let inst_plain_path = tmp.join("spac-wasm-inst-plain.ttf");
    let inst_ovr_path = tmp.join("spac-wasm-inst-override.ttf");
    let override_csv_path = tmp.join("spac-override-avar.csv");
    std::fs::write(&wa_path, &wa).expect("write width-aware output");
    std::fs::write(&uni_path, &uni).expect("write uniform output");
    std::fs::write(&inst_plain_path, &wa_inst_plain).expect("write instanced plain output");
    std::fs::write(&inst_ovr_path, &wa_inst_ovr).expect("write instanced override output");
    // The oracle reads the override CSV as a source sidecar
    // (`<stem>-avar.csv`); the wasm call takes it directly.
    std::fs::write(&override_csv_path, OVERRIDE_CSV).expect("write override csv");

    let comparator = manifest.join("spike/compare_spac.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&font_path)
        .arg(&wa_path)
        .arg(&uni_path)
        .arg(&inst_plain_path)
        .arg(&inst_ovr_path)
        .arg(&instanced_path)
        .arg(&override_csv_path)
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed");
}
