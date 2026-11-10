//! Oracle comparison for `regen_stat`: run it on the CrispyMini spike
//! font (XTRA/XOPQ/YOPQ only), on the avar2-patched font (opsz/wght/wdth
//! added), and on an avar2 font carrying an axis outside the GF registry
//! (LCWD), and structurally compare each result against the
//! `axisregistry.build_stat(ttFont, [])` oracle plus the studio's
//! every-fvar-axis rule via spike/compare_stat.py. A second run on the
//! STAT'd output must reproduce it byte for byte.
//!
//! Fixtures come from tests/common (generated from examples/crispy-mini;
//! $AVAR2_SPIKE_FONT / $AVAR2_CSV / $AVAR2_PYTHON override).

use std::collections::HashSet;
use std::path::Path;
use std::process::Command;

use read_fonts::tables::stat::AxisValue;
use read_fonts::{FontRef, TableProvider};

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

    // 1. The spike font as compiled (XTRA/XOPQ/YOPQ; fontc's STAT has
    // axis records only, under the fvar name IDs).
    let out = fontc_web::regen_stat(font_bytes.clone()).expect("regen_stat spike");
    compare(manifest, &font_path, &out, &tmp.join("stat-rust-spike.ttf"));

    // 2. The avar2-patched font (opsz/wght/wdth added by the pipeline).
    let csv = std::fs::read_to_string(&csv_path).expect("read mappings csv");
    let av2 = fontc_web::add_avar2(font_bytes, &csv, None, None).expect("add_avar2");
    let av2_path = tmp.join("stat-av2-input.ttf");
    std::fs::write(&av2_path, &av2).expect("write avar2 input");
    let out = fontc_web::regen_stat(av2).expect("regen_stat avar2");
    compare(manifest, &av2_path, &out, &tmp.join("stat-rust-av2.ttf"));

    // 3. Idempotency: regen again on the STAT'd output (the old STAT's
    // name records are reused, so nothing is added or dropped).
    let out2 = fontc_web::regen_stat(out.clone()).expect("regen_stat twice");
    assert!(out2 == out, "regen_stat on its own output is not byte-identical");
}

/// Per fvar axis: (tag, fvar axisNameID, STAT (axisNameID, ordering)
/// when a record exists, value-record count).
fn axis_records(font_bytes: &[u8]) -> Vec<(String, u16, Option<(u16, u16)>, usize)> {
    let font = FontRef::new(font_bytes).expect("parse font");
    let fvar = font.fvar().expect("fvar");
    let fvar = fvar.axis_instance_arrays().expect("fvar arrays");
    let stat = font.stat().expect("STAT");
    let design: Vec<_> = stat.design_axes().expect("STAT axes").iter().collect();
    let values: Vec<AxisValue> = match stat.offset_to_axis_values().transpose().expect("STAT values") {
        Some(array) => array.axis_values().iter().map(|v| v.expect("STAT value")).collect(),
        None => Vec::new(),
    };
    let axis_index = |v: &AxisValue| match v {
        AxisValue::Format1(v) => Some(v.axis_index()),
        AxisValue::Format2(v) => Some(v.axis_index()),
        AxisValue::Format3(v) => Some(v.axis_index()),
        AxisValue::Format4(_) => None,
    };
    fvar.axes()
        .iter()
        .map(|a| {
            let rec = design.iter().position(|r| r.axis_tag() == a.axis_tag());
            let stat_rec = rec.map(|i| (design[i].axis_name_id().to_u16(), design[i].axis_ordering()));
            let n_values = values.iter().filter(|v| axis_index(v) == rec.map(|i| i as u16)).count();
            (a.axis_tag().to_string(), a.axis_name_id().to_u16(), stat_rec, n_values)
        })
        .collect()
}

#[test]
fn every_fvar_axis_gets_a_stat_record() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let font_path = common::spike_font();
    let csv_path = common::mappings_csv();
    let tmp = common::tmp_dir();

    // CrispyMini's axes are all in the GF registry. Add a column for one
    // that is not (Crispy's LCWD): add_avar2 appends it as a user axis
    // named by its tag, with the range of its non-empty cells (0..100,
    // default = min) — the remaining rows sit at the default.
    let csv = std::fs::read_to_string(&csv_path).expect("read mappings csv");
    let mut lines = csv.lines().filter(|l| !l.trim().is_empty());
    let mut augmented = format!("{},LCWD\n", lines.next().expect("csv header"));
    for (i, line) in lines.enumerate() {
        let cell = match i {
            0 => ",0",
            1 => ",100",
            _ => ",",
        };
        augmented.push_str(line);
        augmented.push_str(cell);
        augmented.push('\n');
    }
    let font_bytes = std::fs::read(&font_path).expect("read spike font");
    let av2 = fontc_web::add_avar2(font_bytes, &augmented, None, None).expect("add_avar2");
    let av2_path = tmp.join("stat-lcwd-input.ttf");
    std::fs::write(&av2_path, &av2).expect("write avar2 input");

    let out = fontc_web::regen_stat(av2).expect("regen_stat");
    let records = axis_records(&out);
    let tags: Vec<&str> = records.iter().map(|r| r.0.as_str()).collect();
    assert_eq!(tags, ["XTRA", "XOPQ", "YOPQ", "opsz", "wght", "wdth", "LCWD"]);

    // One STAT record per fvar axis, under fvar's own name ID, ordering
    // = position; the registry axes keep their value records, LCWD
    // (not in the registry) has none and comes last.
    let font = FontRef::new(&out).expect("parse font");
    let stat_tags: HashSet<String> = font
        .stat()
        .expect("STAT")
        .design_axes()
        .expect("STAT axes")
        .iter()
        .map(|a| a.axis_tag().to_string())
        .collect();
    let fvar_tags: HashSet<String> = records.iter().map(|r| r.0.clone()).collect();
    assert_eq!(stat_tags, fvar_tags, "STAT and fvar axis tags differ");
    for (i, (tag, fvar_name_id, stat_rec, n_values)) in records.iter().enumerate() {
        let (stat_name_id, ordering) = stat_rec.unwrap_or_else(|| panic!("{tag}: no STAT axis record"));
        assert_eq!(stat_name_id, *fvar_name_id, "{tag}: STAT and fvar name IDs differ");
        assert_eq!(ordering, i as u16, "{tag}: STAT ordering");
        if tag == "LCWD" {
            assert_eq!(*n_values, 0, "{tag}: a non-registry axis has no value records");
        } else {
            assert!(*n_values > 0, "{tag}: a registry axis keeps its value records");
        }
    }

    // The same, against the Python oracle (build_stat + the rule).
    compare(manifest, &av2_path, &out, &tmp.join("stat-rust-lcwd.ttf"));

    // Idempotent to the byte.
    let out2 = fontc_web::regen_stat(out.clone()).expect("regen_stat twice");
    assert!(out2 == out, "regen_stat on its own output is not byte-identical");
}
