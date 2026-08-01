//! Oracle comparison for `regen_stat`: run it on the CrispyMini spike
//! font (XTRA/XOPQ/YOPQ only), on the avar2-patched font (opsz/wght/wdth
//! added), and on the already-STAT'd output again (old-STAT cleanup
//! path), and structurally compare each result against the
//! `axisregistry.build_stat(ttFont, [])` oracle via spike/compare_stat.py.
//!
//! Prerequisites (paths overridable via env):
//!   - spike font:  $AVAR2_SPIKE_FONT  (default /tmp/fontc-wasm-spike.ttf)
//!   - mappings:    $AVAR2_CSV         (default the studio's CrispyMini-avar.csv)
//!   - python with fontTools + axisregistry: $AVAR2_PYTHON
//!     (default the avar2-studio venv)

use std::path::Path;
use std::process::Command;

fn python() -> String {
    std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string())
}

fn compare(manifest: &Path, input_ttf: &str, candidate: &[u8], out_path: &str) {
    std::fs::write(out_path, candidate).expect("write candidate");
    let comparator = manifest.join("spike/compare_stat.py");
    let status = Command::new(python())
        .arg(comparator)
        .arg(input_ttf)
        .arg(out_path)
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed for {out_path}");
}

#[test]
fn stat_matches_axisregistry_oracle() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let font_path = std::env::var("AVAR2_SPIKE_FONT")
        .unwrap_or_else(|_| "/tmp/fontc-wasm-spike.ttf".to_string());
    let csv_path = std::env::var("AVAR2_CSV").unwrap_or_else(|_| {
        manifest
            .join("../../examples/crispy-mini/sources/CrispyMini-avar.csv")
            .to_string_lossy()
        .into_owned()
    });

    let font_bytes = std::fs::read(&font_path).expect("read spike font");

    // 1. The spike font as compiled (XTRA/XOPQ/YOPQ, no STAT).
    let out = fontc_web::regen_stat(font_bytes.clone()).expect("regen_stat spike");
    compare(manifest, &font_path, &out, "/tmp/stat-rust-spike.ttf");

    // 2. The avar2-patched font (opsz/wght/wdth added by the pipeline).
    let csv = std::fs::read_to_string(&csv_path).expect("read mappings csv");
    let av2 = fontc_web::add_avar2(font_bytes, &csv, None, None).expect("add_avar2");
    let av2_path = "/tmp/stat-av2-input.ttf";
    std::fs::write(av2_path, &av2).expect("write avar2 input");
    let out = fontc_web::regen_stat(av2).expect("regen_stat avar2");
    compare(manifest, av2_path, &out, "/tmp/stat-rust-av2.ttf");

    // 3. Idempotency: regen again on the STAT'd output (exercises the
    // old-STAT removal + name-record cleanup path).
    let twice_path = "/tmp/stat-rust-av2-once.ttf";
    std::fs::write(twice_path, &out).expect("write regen input");
    let out2 = fontc_web::regen_stat(out).expect("regen_stat twice");
    compare(manifest, twice_path, &out2, "/tmp/stat-rust-av2-twice.ttf");
}
