//! Oracle comparison for `apply_transforms` (SPAC injection): run both
//! variants on the CrispyMini spike font and compare per-glyph advances
//! at SPAC min/default/max against the REAL transforms — the studio's
//! `WidthAwareSpacTransform` and gftools `gen-spac` via `SpacTransform`
//! (spike/compare_spac.py).
//!
//! Fixtures come from tests/common (generated from examples/crispy-mini;
//! $AVAR2_SPIKE_FONT / $AVAR2_CSV / $AVAR2_PYTHON override).
//!
//! The width-aware amount is computed per MASTER (E2i-c), so the two
//! `…_at_the_masters` tests give both injectors the same explicit master
//! locations and compare at every master corner
//! (spike/compare_spac_masters.py): on CrispyMini with the locations the
//! studio's pipeline reads from its source, and on a synthetic font
//! (spike/make_spac_masters_fixture.py) built to take every branch of the
//! rule. A constant amount per glyph fails both.

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
/// CrispyMini's instances (values inside the -20..40 range). Feeds the
/// PYTHON oracle (its source sidecar); the wasm call gets the same
/// values via the transforms JSON (TRANSFORMS_WA_OVR) — the CSV text is
/// never read Rust-side.
const OVERRIDE_CSV: &str =
    "Instance Name,XTRA,SPAC\nNarrow Thin 144,94.0,10.0\nUltra Wide Thin 144,2895.4,-15.0\n";
/// Width-aware, with OVERRIDE_CSV's SPAC column carried as
/// `params.instance_spac` (parsed Python-side via csv_io — the contract
/// since the CSV parse moved out of the injector).
const TRANSFORMS_WA_OVR: &str = r#"[{"type":"spac_widthaware","enabled":true,"params":{"min":-20,"max":40,"bias":1.0,"scale":1.25,"instance_spac":{"Narrow Thin 144":10.0,"Ultra Wide Thin 144":-15.0}}}]"#;

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
    // CSV (pins 0.0) and the SPAC-column overrides via the JSON.
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
    let wa_inst_ovr = fontc_web::apply_transforms(instanced.clone(), TRANSFORMS_WA_OVR, "")
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
    // (`<stem>-avar.csv`), parsing it Python-side (csv_io) exactly like
    // the studio does; the wasm call got the same values via the JSON.
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

/// Width-aware with the caller's master locations, as the studio's
/// pipeline (and the static app) sends it.
fn transforms_with_masters(params: &str, locations_json: &str) -> String {
    format!(
        r#"[{{"type":"spac_widthaware","enabled":true,"params":{{{params},"master_locations":{locations_json}}}}}]"#
    )
}

/// Inject with the Rust engine and hold the result against the real
/// transform at every master corner.
fn compare_at_the_masters(label: &str, input: &Path, transforms: &str) {
    let out = fontc_web::apply_transforms(std::fs::read(input).expect("read input font"), transforms, "")
        .expect("width-aware SPAC with master locations");
    let out_path = common::tmp_dir().join(format!("spac-masters-{label}.ttf"));
    std::fs::write(&out_path, &out).expect("write injector output");
    compare_output_at_the_masters(label, input, &out_path, transforms);
}

/// Hold an injected font against the real transform (its fontTools path)
/// at every master corner.
fn compare_output_at_the_masters(label: &str, input: &Path, out_path: &Path, transforms: &str) {
    let transforms_path = common::tmp_dir().join(format!("spac-masters-{label}.json"));
    std::fs::write(&transforms_path, transforms).expect("write transforms json");
    let comparator = Path::new(env!("CARGO_MANIFEST_DIR")).join("spike/compare_spac_masters.py");
    let status = Command::new(common::python())
        .arg(comparator)
        .arg(input)
        .arg(out_path)
        .arg(&transforms_path)
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison at the masters failed ({label})");
}

/// The desktop path: the studio's transform hands the font to the native
/// `spac-inject` (this crate's binary), master locations in the payload,
/// and the result must be the one its own fontTools path produces.
#[test]
fn native_injector_matches_studio_oracle_at_the_masters() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (font, locations) = common::spac_masters_fixture("native", true);
    let transforms = transforms_with_masters(
        r#""min":-100,"max":100,"bias":1.0,"scale":1.25"#,
        &locations,
    );
    let tmp = common::tmp_dir();
    let transforms_path = tmp.join("spac-native-transforms.json");
    let out_path = tmp.join("spac-native-out.ttf");
    std::fs::write(&transforms_path, &transforms).expect("write transforms json");
    let status = Command::new(common::python())
        .arg(manifest.join("spike/native_spac.py"))
        .arg(&font)
        .arg(&out_path)
        .arg(&transforms_path)
        .env("AVAR2_SPAC_INJECT", env!("CARGO_BIN_EXE_spac-inject"))
        .status()
        .expect("run spike/native_spac.py");
    assert!(status.success(), "the studio did not use the native injector");
    compare_output_at_the_masters("native", &font, &out_path, &transforms);
}

/// CrispyMini with the master locations the studio's own pipeline reads
/// from the source — the real path, end to end.
#[test]
fn spac_matches_studio_oracle_at_the_masters() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let output = Command::new(common::python())
        .arg(manifest.join("spike/master_locations.py"))
        .arg(manifest.join("../../examples/crispy-mini/sources/CrispyMini.glyphs"))
        .output()
        .expect("run spike/master_locations.py");
    assert!(
        output.status.success(),
        "spike/master_locations.py failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let locations = String::from_utf8(output.stdout).expect("master locations JSON");
    assert!(locations.trim().starts_with("[{}"), "the pipeline found no masters: {locations}");
    let transforms = transforms_with_masters(
        r#""min":-100,"max":100,"bias":1.0,"scale":1.25"#,
        locations.trim(),
    );
    let spike = common::spike_font();
    compare_at_the_masters("crispymini", &spike, &transforms);

    // The static app's order: the avar2 mappings first, SPAC after. The
    // font then carries an avar 2 table, whose axis count must still be
    // fvar's once the SPAC axis is in (FreeType drops avar otherwise, and
    // wght/wdth/opsz with it).
    let csv = std::fs::read_to_string(common::mappings_csv()).expect("read mappings csv");
    let mapped = fontc_web::add_avar2(std::fs::read(&spike).expect("read spike font"), &csv, None, None)
        .expect("add_avar2");
    let mapped_path = common::tmp_dir().join("spac-masters-avar2-input.ttf");
    std::fs::write(&mapped_path, &mapped).expect("write avar2 input");
    compare_at_the_masters("crispymini-avar2", &mapped_path, &transforms);
}

/// The synthetic multi-master font: a negative side, an interior master
/// behind avar, a location between masters, the floor, a zero advance,
/// curve extremes, composites — with HVAR and without, and off the
/// default bias.
/// The same fixture resting elsewhere, on a master and between masters. The
/// default is then an extra corner of the model that must stay out of what a
/// SPAC unit is measured against (user report, 2026-11-07).
#[test]
fn spac_matches_studio_oracle_when_the_default_moves() {
    let params = r#""min":-100,"max":100,"bias":1.0,"scale":1.25"#;
    for (label, moves) in [
        ("moved-master", &["XTRA=500", "XOPQ=110"][..]),
        ("moved-interior", &["XTRA=300", "XOPQ=60"][..]),
    ] {
        let (font, locations) = common::spac_masters_relocated(label, moves);
        compare_at_the_masters(label, &font, &transforms_with_masters(params, &locations));
    }
}

#[test]
fn spac_matches_studio_oracle_on_the_masters_fixture() {
    for (label, hvar, params) in [
        ("fixture", true, r#""min":-100,"max":100,"bias":1.0,"scale":1.25"#),
        ("fixture-nohvar", false, r#""min":-100,"max":100,"bias":1.0,"scale":1.25"#),
        ("fixture-bias", true, r#""min":-60,"max":40,"bias":1.5,"scale":0.8"#),
    ] {
        let (font, locations) = common::spac_masters_fixture(label, hvar);
        compare_at_the_masters(label, &font, &transforms_with_masters(params, &locations));
    }
}
