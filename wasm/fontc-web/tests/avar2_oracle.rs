//! Oracle comparison for `add_avar2`: run it on the CrispyMini spike
//! font + the studio's CrispyMini-avar.csv and structurally compare the
//! result against the gftools `gen_avar2_mapping` oracle with fontTools.
//!
//! Fixtures come from tests/common (generated from examples/crispy-mini;
//! $AVAR2_SPIKE_FONT / $AVAR2_CSV / $AVAR2_ORACLE / $AVAR2_PYTHON override).

use std::path::Path;
use std::process::Command;

mod common;

#[test]
fn avar2_matches_gftools_oracle() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let font_path = common::spike_font();
    let csv_path = common::mappings_csv();
    let oracle_path = common::oracle_font(&font_path, &csv_path);
    let python = common::python();

    let font_bytes = std::fs::read(&font_path).expect("read spike font");
    let csv = std::fs::read_to_string(&csv_path).expect("read mappings csv");
    let out = fontc_web::add_avar2(font_bytes, &csv, None, None).expect("add_avar2");

    std::fs::create_dir_all(concat!(env!("CARGO_MANIFEST_DIR"), "/target/tmp")).expect("create target/tmp");
    let rust_out = format!("{}/target/tmp/av2-rust.ttf", env!("CARGO_MANIFEST_DIR"));
    std::fs::write(&rust_out, &out).expect("write rust output");

    let comparator = manifest.join("spike/compare_avar2.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&oracle_path)
        .arg(rust_out)
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed");
}
