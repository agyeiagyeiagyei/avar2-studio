//! Oracle comparisons for what the width-aware SPAC injector works FROM,
//! before any rounding can hide a difference (spac_oracle.rs compares the
//! fonts it produces):
//!
//! - what it MEASURES on the built font — the normalized corners, the ink
//!   widths (fontTools' BoundsPen over the instanced glyph set), their
//!   means, the advances, the model's supports — against the reference's
//!   own values, as exact floats (spike/compare_spac_masters.py --trace);
//! - the master locations the static app reads from a `.glyphs` source
//!   (`fontc_web::master_locations`) against the ones the studio's
//!   pipeline reads (`pipeline._master_locations`).

use std::path::Path;
use std::process::Command;

mod common;

fn compare_trace(label: &str, input: &Path, transforms: &str) {
    let tmp = common::tmp_dir();
    let trace = fontc_web::spac_widthaware_trace(&std::fs::read(input).expect("read input font"), transforms)
        .expect("width-aware SPAC trace");
    let trace_path = tmp.join(format!("spac-trace-{label}.json"));
    let transforms_path = tmp.join(format!("spac-trace-{label}-transforms.json"));
    std::fs::write(&trace_path, trace).expect("write trace");
    std::fs::write(&transforms_path, transforms).expect("write transforms json");
    let comparator = Path::new(env!("CARGO_MANIFEST_DIR")).join("spike/compare_spac_masters.py");
    let status = Command::new(common::python())
        .arg(comparator)
        .arg(input)
        .arg("--trace")
        .arg(&trace_path)
        .arg(&transforms_path)
        .status()
        .expect("run comparator");
    assert!(status.success(), "the injector's measurements differ from the reference's ({label})");
}

fn pipeline_master_locations(source: &Path) -> String {
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("spike/master_locations.py");
    let output = Command::new(common::python())
        .arg(script)
        .arg(source)
        .output()
        .expect("run spike/master_locations.py");
    assert!(
        output.status.success(),
        "spike/master_locations.py failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).expect("master locations JSON").trim().to_string()
}

#[test]
fn widthaware_measurements_match_reference() {
    // The synthetic fixture with its own master locations: fractional
    // coordinates, curve extremes, avar, an interior master.
    for (label, hvar) in [("fixture", true), ("fixture-nohvar", false)] {
        let (font, locations) = common::spac_masters_fixture(&format!("trace-{label}"), hvar);
        let transforms = format!(
            r#"[{{"type":"spac_widthaware","enabled":true,"params":{{"min":-100,"max":100,"master_locations":{locations}}}}}]"#
        );
        compare_trace(label, &font, &transforms);
    }
    // The default between the masters: an extra corner, but not a master.
    let (font, locations) = common::spac_masters_relocated("trace-moved", &["XTRA=300", "XOPQ=60"]);
    let transforms = format!(
        r#"[{{"type":"spac_widthaware","enabled":true,"params":{{"min":-100,"max":100,"master_locations":{locations}}}}}]"#
    );
    compare_trace("moved", &font, &transforms);
    // CrispyMini (a real fontc build): with the pipeline's master
    // locations, and with none — the corners inferred from gvar, brace
    // layers included, so most of them fall between masters.
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let spike = common::spike_font();
    let locations =
        pipeline_master_locations(&manifest.join("../../examples/crispy-mini/sources/CrispyMini.glyphs"));
    let given = format!(
        r#"[{{"type":"spac_widthaware","enabled":true,"params":{{"min":-100,"max":100,"master_locations":{locations}}}}}]"#
    );
    compare_trace("crispymini", &spike, &given);
    compare_trace(
        "crispymini-inferred",
        &spike,
        r#"[{"type":"spac_widthaware","enabled":true,"params":{"min":-100,"max":100}}]"#,
    );
}

/// The static app reads the master locations from the source with
/// `fontc_web::master_locations`; the desktop with the pipeline. Same
/// source, same list — values and key order.
#[test]
fn master_locations_match_pipeline() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    for source in [
        "../../examples/crispy-mini/sources/CrispyMini.glyphs",
        "tests/fixtures/FoldedMini.glyphs",
        "tests/fixtures/StrandedMini.glyphs",
    ] {
        let path = manifest.join(source);
        let text = std::fs::read_to_string(&path).expect("read source");
        let mine = fontc_web::master_locations(text).expect("master locations");
        let theirs = pipeline_master_locations(&path);
        // Compared as text once both are re-serialized without sorting
        // keys: an object's key order is part of the contract.
        let normalize = |json: &str| {
            let output = Command::new(common::python())
                .args(["-c", "import json,sys; print(json.dumps(json.loads(sys.argv[1])))", json])
                .output()
                .expect("normalize JSON");
            assert!(output.status.success(), "bad JSON: {json}");
            String::from_utf8(output.stdout).expect("utf-8")
        };
        assert_eq!(normalize(&mine), normalize(&theirs), "master locations differ for {source}");
        println!("{source}: {}", normalize(&mine).trim());
    }
}
