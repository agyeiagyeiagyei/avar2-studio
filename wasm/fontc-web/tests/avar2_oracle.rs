//! Oracle comparison for `add_avar2`: run it on the CrispyMini spike
//! font + the studio's CrispyMini-avar.csv and structurally compare the
//! result against the gftools `gen_avar2_mapping` oracle with fontTools.
//!
//! Prerequisites (paths overridable via env):
//!   - spike font:  $AVAR2_SPIKE_FONT  (default /tmp/fontc-wasm-spike.ttf)
//!   - mappings:    $AVAR2_CSV         (default the studio's CrispyMini-avar.csv)
//!   - oracle TTF:  $AVAR2_ORACLE      (default /tmp/av2-oracle.ttf),
//!     built by `spike/build_oracle.py`
//!   - python with fontTools: $AVAR2_PYTHON (default the avar2-studio venv)

use std::path::Path;
use std::process::Command;

#[test]
fn avar2_matches_gftools_oracle() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let font_path = std::env::var("AVAR2_SPIKE_FONT")
        .unwrap_or_else(|_| "/tmp/fontc-wasm-spike.ttf".to_string());
    let csv_path = std::env::var("AVAR2_CSV").unwrap_or_else(|_| {
        manifest
            .join("../../examples/crispy-mini/sources/CrispyMini-avar.csv")
            .to_string_lossy()
        .into_owned()
    });
    let oracle_path =
        std::env::var("AVAR2_ORACLE").unwrap_or_else(|_| "/tmp/av2-oracle.ttf".to_string());
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());

    let font_bytes = std::fs::read(&font_path).expect("read spike font");
    let csv = std::fs::read_to_string(&csv_path).expect("read mappings csv");
    let out = fontc_web::add_avar2(font_bytes, &csv, None, None).expect("add_avar2");

    let rust_out = "/tmp/av2-rust.ttf";
    std::fs::write(rust_out, &out).expect("write rust output");

    let comparator = manifest.join("spike/compare_avar2.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&oracle_path)
        .arg(rust_out)
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed");
}
