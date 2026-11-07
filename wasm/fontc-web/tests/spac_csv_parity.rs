//! Parity for the SPAC injector's JSON contract (audit M7 / T3.5): the
//! mappings CSV is parsed Python-side (csv_io.read_csv_spac_overrides)
//! and per-instance SPAC values ride the transforms JSON
//! (`params.instance_spac`) — the injector never opens the CSV. The
//! same CSV (quoted comma in an instance name, a blank SPAC cell) must
//! pin the same per-instance values through both halves.
//!
//! The Python half (tests/test_spac_csv_parity.py) pins csv_io's parse
//! and its carriage into the payload the transform writes; here the
//! injector's output is compared against csv_io's parse by
//! spike/spac_csv_parity.py.

use std::process::Command;

mod common;

/// The shared CSV: a quoted comma in an instance name (the M7 case —
/// the old Rust split-on-',' reader lost the row), a blank SPAC cell
/// (pins nothing), and a plain row.
const PARITY_CSV: &str =
    "Instance Name,XTRA,SPAC\n\"Narrow, Thin\",94.0,10.0\nTall,50.0,\nUltra Wide Thin 144,2895.4,-15.0\n";

#[test]
fn spac_overrides_parity_with_csv_io() {
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let tmp = common::tmp_dir();
    let csv_path = tmp.join("spac-parity-avar.csv");
    std::fs::write(&csv_path, PARITY_CSV).expect("write parity csv");

    // Python (csv_io) parses the CSV and mints the instanced fixture:
    // one fvar instance per CSV row name, at the axis defaults.
    let spike = common::spike_font();
    let instanced_path = tmp.join("spac-parity-instanced.ttf");
    let script = manifest.join("spike/spac_csv_parity.py");
    let output = Command::new(common::python())
        .arg(&script)
        .arg(&spike)
        .arg(&instanced_path)
        .arg(&csv_path)
        .output()
        .expect("run spac_csv_parity.py (mint)");
    assert!(
        output.status.success(),
        "spac_csv_parity.py mint failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let parsed: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("overrides JSON");
    let overrides = parsed.get("overrides").expect("overrides key");
    // csv_io's reading: the quoted comma name survives whole; the blank
    // cell pins nothing.
    assert_eq!(
        overrides.get("Narrow, Thin").and_then(|v| v.as_f64()),
        Some(10.0)
    );
    assert_eq!(
        overrides.get("Ultra Wide Thin 144").and_then(|v| v.as_f64()),
        Some(-15.0)
    );
    assert!(overrides.get("Tall").is_none(), "blank SPAC cell must not pin");

    // The injector applies the JSON the studio would write. The CSV is
    // still passed as the (ignored) third argument with values that
    // CONTRADICT the JSON — pinning must follow the JSON.
    let transforms = serde_json::json!([{
        "type": "spac_widthaware",
        "enabled": true,
        "params": {"min": -20, "max": 40, "bias": 1.0, "scale": 1.25,
                   "instance_spac": overrides},
    }]);
    let out = fontc_web::apply_transforms(
        std::fs::read(&instanced_path).expect("read instanced font"),
        &transforms.to_string(),
        "Instance Name,SPAC\nTall,999.0\n",
    )
    .expect("apply transforms");
    let out_path = tmp.join("spac-parity-out.ttf");
    std::fs::write(&out_path, &out).expect("write injector output");

    // The comparator asserts the output's per-instance SPAC coordinates
    // equal csv_io's parse (blank cells → the axis default).
    let status = Command::new(common::python())
        .arg(&script)
        .arg(&spike)
        .arg(&instanced_path)
        .arg(&csv_path)
        .arg(&out_path)
        .status()
        .expect("run spac_csv_parity.py (compare)");
    assert!(status.success(), "injector pinning diverged from csv_io's parse");
}
