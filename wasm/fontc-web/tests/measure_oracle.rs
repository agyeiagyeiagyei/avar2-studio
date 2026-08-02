//! Oracle comparison for `measure_at`: outline area must track Pillow
//! stem darkness (the coverage spike's metric) on crispy-test's two
//! signature sweeps — the YOPQ rise-then-collapse and the inert XOPQ
//! sweep. Compares argmax, post-peak collapse, and relative inertness
//! (not absolute values: area ≠ pixel darkness, monotonicity is what
//! the audit consumes).
//!
//! Prereqs: python with fontTools + Pillow at $AVAR2_PYTHON.

use std::path::Path;
use std::process::Command;

#[test]
fn measure_tracks_pillow_darkness() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());
    let source_path = manifest.join("../../frontend/e2e/fixtures/Crispy-test.glyphs");
    let source = std::fs::read_to_string(&source_path).expect("read crispy-test fixture");
    let font_bytes = fontc_web::compile_glyphs(source).expect("compile");
    std::fs::write("/tmp/measure-font.ttf", &font_bytes).expect("write font");

    // 7-step sweeps, mirroring spike/corner_audit.py.
    let steps = |lo: f64, hi: f64| (0..7).map(|i| lo + (hi - lo) * i as f64 / 6.0).collect::<Vec<_>>();
    let mut locations: Vec<serde_json::Value> = Vec::new();
    let mut sweeps: Vec<(&str, Vec<serde_json::Value>)> = Vec::new();
    for (name, axis, fixed, lo, hi) in [
        ("yopq_sweep", "YOPQ", ("XTRA", 47.0), 1.0, 300.0),
        ("xopq_sweep", "XOPQ", ("XTRA", 47.0), 1.0, 700.0),
    ] {
        let mut pts = Vec::new();
        for v in steps(lo, hi) {
            let mut loc = serde_json::Map::new();
            loc.insert(axis.to_string(), v.into());
            loc.insert(fixed.0.to_string(), fixed.1.into());
            loc.insert(
                if axis == "YOPQ" { "XOPQ".into() } else { "YOPQ".into() },
                1.0.into(),
            );
            pts.push(serde_json::Value::Object(loc));
        }
        sweeps.push((name, pts));
    }
    let mut sweep_ranges = Vec::new();
    for (name, pts) in &sweeps {
        let start = locations.len();
        locations.extend(pts.iter().cloned());
        sweep_ranges.push((name.to_string(), start, locations.len()));
    }

    let request = serde_json::json!({
        "glyphs": ["a", "e", "o", "g"],
        "locations": locations,
    });
    let areas = fontc_web::measure_at(font_bytes, request.to_string()).expect("measure_at");
    assert_eq!(areas.len(), locations.len());
    std::fs::write(
        "/tmp/measure-areas.json",
        serde_json::json!({
            "sweeps": sweep_ranges,
            "areas": areas,
            "locations": locations,
        })
        .to_string(),
    )
    .expect("write areas");

    let comparator = manifest.join("spike/compare_measure.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg("/tmp/measure-font.ttf")
        .arg("/tmp/measure-areas.json")
        .status()
        .expect("run comparator");
    assert!(status.success(), "measure oracle comparison failed");
}
