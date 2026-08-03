//! Oracle for `clamp_out_of_range`: dropping the braced Crispy's
//! stranded (out-of-range) sources must (a) leave the default instance
//! untouched, (b) zero the stranded tuples' packed deltas so they can
//! no longer contribute, and (c) bring outlines roughly in line with
//! the fontmake (varLib) build — which drops the same sources upstream.
//! Exact fontc↔fontmake equality is NOT provable (fontdrasil and
//! fontTools VariationModels differ on tent shapes), so the cross-check
//! is an anti-mangling bound, not a tolerance match.

use std::path::Path;
use std::process::Command;

fn be_u16(b: &[u8], off: usize) -> u16 {
    u16::from_be_bytes([b[off], b[off + 1]])
}
fn be_u32(b: &[u8], off: usize) -> u32 {
    u32::from_be_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}
fn i16_at(b: &[u8], off: usize) -> i32 {
    i16::from_be_bytes([b[off], b[off + 1]]) as i32
}

fn table<'a>(font: &'a [u8], tag: &[u8; 4]) -> Option<&'a [u8]> {
    let num_tables = be_u16(font, 4) as usize;
    for i in 0..num_tables {
        let rec = 12 + i * 16;
        if font.get(rec..rec + 4)? == tag {
            let off = be_u32(font, rec + 8) as usize;
            let len = be_u32(font, rec + 12) as usize;
            return font.get(off..off + len);
        }
    }
    None
}

/// Packed points: count byte(s) then runs. Returns offset past the list.
fn skip_packed_points(b: &[u8], mut p: usize) -> usize {
    let b0 = b[p];
    p += 1;
    let mut count = (b0 & 0x7f) as usize;
    if b0 & 0x80 != 0 {
        count = (count << 8) | b[p] as usize;
        p += 1;
    }
    while count > 0 {
        let r = b[p];
        p += 1;
        let n = (r & 0x7f) as usize + 1;
        p += n * if r & 0x80 != 0 { 2 } else { 1 };
        count = count.saturating_sub(n);
    }
    p
}

/// True when any packed-delta run in [p, end) carries a nonzero byte.
fn deltas_nonzero(b: &[u8], mut p: usize, end: usize) -> bool {
    while p < end {
        let r = b[p];
        p += 1;
        let n = (r & 0x3f) as usize + 1;
        // OT delta-run control: 0x00 = bytes, 0x40 = words,
        // 0x80 = DELTAS_ARE_ZERO (no data), 0xC0 = longs.
        let sz = match r & 0xc0 {
            0x00 => 1,
            0x40 => 2,
            0x80 => 0,
            _ => 4,
        };
        if b[p..p + n * sz].iter().any(|&x| x != 0) {
            return true;
        }
        p += n * sz;
    }
    false
}

/// Walk the gvar tuples and return (stranded, live): tuples whose peak
/// lies outside ±1.0 (F2DOT14), and of those how many still carry
/// nonzero packed deltas — i.e. stranded sources that are still active.
fn stranded_tuple_counts(gvar: &[u8]) -> (usize, usize) {
    let axis_count = be_u16(gvar, 4) as usize;
    let shared_count = be_u16(gvar, 6) as usize;
    let off_shared = be_u32(gvar, 8) as usize;
    let glyph_count = be_u16(gvar, 12) as usize;
    let long_offsets = be_u16(gvar, 14) & 1 != 0;
    let off_data = be_u32(gvar, 16) as usize;

    let mut stranded_shared = std::collections::HashSet::new();
    for i in 0..shared_count {
        let off = off_shared + i * axis_count * 2;
        if (0..axis_count).any(|a| i16_at(gvar, off + a * 2).abs() > 16384) {
            stranded_shared.insert(i as u16);
        }
    }

    let mut stranded_total = 0;
    let mut live = 0;
    for g in 0..glyph_count {
        let (start, end) = if long_offsets {
            (
                be_u32(gvar, 20 + g * 4) as usize,
                be_u32(gvar, 20 + (g + 1) * 4) as usize,
            )
        } else {
            (
                be_u16(gvar, 20 + g * 2) as usize * 2,
                be_u16(gvar, 20 + (g + 1) * 2) as usize * 2,
            )
        };
        if end <= start {
            continue;
        }
        let gd = off_data + start;
        let count = (be_u16(gvar, gd) & 0x0fff) as usize;
        let data_off = gd + be_u16(gvar, gd + 2) as usize;

        let mut tuples = Vec::new(); // (data_size, stranded, private_points)
        let mut h = gd + 4;
        for _ in 0..count {
            let data_size = be_u16(gvar, h) as usize;
            let ti = be_u16(gvar, h + 2);
            h += 4;
            let mut stranded = false;
            if ti & 0x8000 != 0 {
                stranded = (0..axis_count).any(|a| i16_at(gvar, h + a * 2).abs() > 16384);
                h += axis_count * 2;
            } else if stranded_shared.contains(&(ti & 0x0fff)) {
                stranded = true;
            }
            if ti & 0x4000 != 0 {
                h += axis_count * 4;
            }
            tuples.push((data_size, stranded, ti & 0x2000 != 0));
        }

        let mut p = data_off;
        if be_u16(gvar, gd) & 0x8000 != 0 {
            p = skip_packed_points(gvar, p);
        }
        for (size, stranded, private) in tuples {
            let tuple_start = p;
            if private {
                p = skip_packed_points(gvar, p);
            }
            let tuple_end = tuple_start + size;
            if stranded {
                stranded_total += 1;
                if deltas_nonzero(gvar, p, tuple_end) {
                    live += 1;
                }
            }
            p = tuple_end;
        }
    }
    (stranded_total, live)
}

#[test]
fn clamp_drops_stranded_sources() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let python = std::env::var("AVAR2_PYTHON")
        .unwrap_or_else(|_| "/Users/agyei/Documents/avar2-studio/.venv/bin/python".to_string());
    let source_path = manifest.join("../../../Crispy/sources/Crispy.glyphs");
    let source = std::fs::read_to_string(&source_path).expect("read Crispy.glyphs");
    let before = fontc_web::compile_glyphs(source).expect("fontc compile");
    let after = fontc_web::clamp_out_of_range(before.clone()).expect("clamp");

    // (b) Stranded tuples must go from live to fully neutralized —
    // deltas zeroed AND peaks zeroed (peak-reading audits see the drop).
    let gvar_before = table(&before, b"gvar").expect("gvar before");
    let gvar_after = table(&after, b"gvar").expect("gvar after");
    let (stranded_before, live_before) = stranded_tuple_counts(gvar_before);
    assert!(
        stranded_before > 0 && live_before > 0,
        "oracle vacuous: Crispy.glyphs must contain live stranded sources"
    );
    let (stranded_after, live_after) = stranded_tuple_counts(gvar_after);
    assert_eq!(
        live_after, 0,
        "{live_after} stranded tuples still carry deltas after clamp"
    );
    assert_eq!(
        stranded_after, 0,
        "{stranded_after} out-of-range peaks still present after clamp"
    );

    // (a) Default instance untouched: outlines and default advances
    // are byte-identical (clamp touches gvar + HVAR only).
    assert_eq!(
        table(&before, b"glyf"),
        table(&after, b"glyf"),
        "default outlines moved"
    );
    assert_eq!(
        table(&before, b"hmtx"),
        table(&after, b"hmtx"),
        "default advances moved"
    );

    // (c) Anti-mangling cross-check against the fontmake build.
    std::fs::write("/tmp/clamp-before.ttf", &before).expect("write before");
    std::fs::write("/tmp/clamp-after.ttf", &after).expect("write after");
    let comparator = manifest.join("spike/compare_clamp.py");
    let status = Command::new(&python)
        .arg(comparator)
        .arg(&source_path)
        .arg("/tmp/clamp-before.ttf")
        .arg("/tmp/clamp-after.ttf")
        .status()
        .expect("run comparator");
    assert!(status.success(), "clamp oracle comparison failed");
}
