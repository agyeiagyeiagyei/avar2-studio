//! Shared fixtures for the oracle tests that start from a compiled
//! CrispyMini: the "spike" font (examples/crispy-mini compiled by the
//! crate's own fontc, no avar2) and the gftools avar2 oracle that
//! spike/build_oracle.py derives from it. Both are generated here, so a
//! fresh checkout needs no hand-made files; `$AVAR2_SPIKE_FONT` /
//! `$AVAR2_ORACLE` still substitute hand-made ones.

#![allow(dead_code)] // each test binary uses a subset of these

use std::path::{Path, PathBuf};
use std::process::Command;

fn manifest() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR"))
}

/// Cargo's scratch dir for integration tests: `<target dir>/tmp`.
pub fn tmp_dir() -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"));
    std::fs::create_dir_all(&dir).expect("create target/tmp");
    dir
}

/// Python with fontTools + gftools + avar2_studio: `$AVAR2_PYTHON`, else
/// the repo venv when it exists, else `python3`.
pub fn python() -> String {
    std::env::var("AVAR2_PYTHON").unwrap_or_else(|_| {
        let venv = concat!(env!("CARGO_MANIFEST_DIR"), "/../../.venv/bin/python");
        if Path::new(venv).exists() { venv.to_string() } else { "python3".to_string() }
    })
}

/// The avar2 mappings CSV: `$AVAR2_CSV`, else the studio's CrispyMini-avar.csv.
pub fn mappings_csv() -> PathBuf {
    std::env::var("AVAR2_CSV")
        .map(PathBuf::from)
        .unwrap_or_else(|_| manifest().join("../../examples/crispy-mini/sources/CrispyMini-avar.csv"))
}

/// The spike font: CrispyMini compiled without avar2, written to
/// target/tmp/fontc-wasm-spike.ttf (the comparators take file paths).
pub fn spike_font() -> PathBuf {
    if let Ok(p) = std::env::var("AVAR2_SPIKE_FONT") {
        return PathBuf::from(p);
    }
    let source = manifest().join("../../examples/crispy-mini/sources/CrispyMini.glyphs");
    let source = std::fs::read_to_string(&source).expect("read CrispyMini.glyphs");
    // fontc stamps head.modified from the clock; pin it so the bytes are
    // reproducible (same epoch as editor_oracle.rs).
    std::env::set_var("SOURCE_DATE_EPOCH", "1456796400");
    let bytes = fontc_web::compile_glyphs(source).expect("compile spike font");
    let out = tmp_dir().join("fontc-wasm-spike.ttf");
    // Write-then-rename: a sibling test in the same binary never reads a
    // half-written font.
    let part = out.with_extension(format!("{:?}.part", std::thread::current().id()));
    std::fs::write(&part, &bytes).expect("write spike font");
    std::fs::rename(&part, &out).expect("rename spike font");
    out
}

/// The gftools avar2 oracle for `csv` applied to `spike`
/// (spike/build_oracle.py), as target/tmp/av2-oracle.ttf.
pub fn oracle_font(spike: &Path, csv: &Path) -> PathBuf {
    if let Ok(p) = std::env::var("AVAR2_ORACLE") {
        return PathBuf::from(p);
    }
    let out = tmp_dir().join("av2-oracle.ttf");
    let status = Command::new(python())
        .arg(manifest().join("spike/build_oracle.py"))
        .arg(spike)
        .arg(csv)
        .arg(&out)
        .status()
        .expect("run spike/build_oracle.py");
    assert!(status.success(), "spike/build_oracle.py failed");
    out
}
