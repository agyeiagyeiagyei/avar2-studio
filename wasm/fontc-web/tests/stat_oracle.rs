//! Oracle comparison for `regen_stat`: run it on the CrispyMini spike
//! font (XTRA/XOPQ/YOPQ only), on the avar2-patched font (opsz/wght/wdth
//! added), and on the already-STAT'd output again (old-STAT cleanup
//! path), and structurally compare each result against the
//! `axisregistry.build_stat(ttFont, [])` oracle via spike/compare_stat.py.
//!
//! Fixtures come from tests/common (generated from examples/crispy-mini;
//! $AVAR2_SPIKE_FONT / $AVAR2_CSV / $AVAR2_PYTHON override).

use std::path::Path;
use std::process::Command;

mod common;

fn compare(manifest: &Path, input_ttf: &Path, candidate: &[u8], out_path: &Path) {
    std::fs::write(out_path, candidate).expect("write candidate");
    let comparator = manifest.join("spike/compare_stat.py");
    let status = Command::new(common::python())
        .arg(comparator)
        .arg(input_ttf)
        .arg(out_path)
        .status()
        .expect("run comparator");
    assert!(status.success(), "oracle comparison failed for {}", out_path.display());
}

#[test]
fn stat_matches_axisregistry_oracle() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let font_path = common::spike_font();
    let csv_path = common::mappings_csv();

    let font_bytes = std::fs::read(&font_path).expect("read spike font");
    let tmp = common::tmp_dir();

    // 1. The spike font as compiled (XTRA/XOPQ/YOPQ, no STAT).
    let out = fontc_web::regen_stat(font_bytes.clone()).expect("regen_stat spike");
    compare(manifest, &font_path, &out, &tmp.join("stat-rust-spike.ttf"));

    // 2. The avar2-patched font (opsz/wght/wdth added by the pipeline).
    let csv = std::fs::read_to_string(&csv_path).expect("read mappings csv");
    let av2 = fontc_web::add_avar2(font_bytes, &csv, None, None).expect("add_avar2");
    let av2_path = tmp.join("stat-av2-input.ttf");
    std::fs::write(&av2_path, &av2).expect("write avar2 input");
    let out = fontc_web::regen_stat(av2).expect("regen_stat avar2");
    compare(manifest, &av2_path, &out, &tmp.join("stat-rust-av2.ttf"));

    // 3. Idempotency: regen again on the STAT'd output (exercises the
    // old-STAT removal + name-record cleanup path).
    let twice_path = tmp.join("stat-rust-av2-once.ttf");
    std::fs::write(&twice_path, &out).expect("write regen input");
    let out2 = fontc_web::regen_stat(out).expect("regen_stat twice");
    compare(manifest, &twice_path, &out2, &tmp.join("stat-rust-av2-twice.ttf"));
}
