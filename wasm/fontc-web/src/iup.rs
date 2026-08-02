//! IUP — "Interpolate Untouched Points" (OpenType gvar spec), a port of
//! fontTools `varLib.iup` (iup_segment / iup_contour / iup_delta).
//!
//! When a gvar tuple covers only a subset of a glyph's points (packed
//! point numbers), the uncovered points' deltas are inferred from the
//! covered neighbors along each contour — not zero. Without this, an
//! instancer built on explicit deltas alone renders different outlines
//! than any real renderer (fontTools, harfbuzz, FreeType all infer).

/// Interpolated deltas for a run of coordinates between two covered
/// reference points (fontTools iup_segment): per component, clamped
/// linear interpolation by coordinate position; identical coordinates
/// yield the shared delta when equal, else zero.
fn iup_segment(
    coords: &[[f64; 2]],
    rc1: [f64; 2],
    rd1: (f64, f64),
    rc2: [f64; 2],
    rd2: (f64, f64),
) -> Vec<(f64, f64)> {
    let mut out_x: Vec<f64> = Vec::new();
    let mut out_y: Vec<f64> = Vec::new();
    for j in 0..2 {
        let out = if j == 0 { &mut out_x } else { &mut out_y };
        let (mut x1, mut x2, mut d1, mut d2) = (
            if j == 0 { rc1[0] } else { rc1[1] },
            if j == 0 { rc2[0] } else { rc2[1] },
            if j == 0 { rd1.0 } else { rd1.1 },
            if j == 0 { rd2.0 } else { rd2.1 },
        );
        if x1 == x2 {
            let v = if d1 == d2 { d1 } else { 0.0 };
            out.extend(std::iter::repeat(v).take(coords.len()));
            continue;
        }
        if x1 > x2 {
            std::mem::swap(&mut x1, &mut x2);
            std::mem::swap(&mut d1, &mut d2);
        }
        let scale = (d2 - d1) / (x2 - x1);
        for pair in coords {
            let x = pair[j];
            let d = if x <= x1 {
                d1
            } else if x >= x2 {
                d2
            } else {
                d1 + (x - x1) * scale
            };
            out.push(d);
        }
    }
    out_x.into_iter().zip(out_y.into_iter()).collect()
}

/// Fill a contour's missing deltas (fontTools iup_contour).
fn iup_contour(deltas: &[Option<(f64, f64)>], coords: &[[f64; 2]]) -> Vec<(f64, f64)> {
    let n = deltas.len();
    if !deltas.iter().any(|d| d.is_none()) {
        return deltas.iter().map(|d| d.unwrap()).collect();
    }
    let indices: Vec<usize> = deltas
        .iter()
        .enumerate()
        .filter_map(|(i, d)| d.map(|_| i))
        .collect();
    if indices.is_empty() {
        return vec![(0.0, 0.0); n];
    }

    let mut out: Vec<(f64, f64)> = Vec::with_capacity(n);
    let mut iter = indices.iter();
    let mut start = *iter.next().unwrap();
    if start != 0 {
        // Initial segment that wraps around.
        let ri2 = *indices.last().unwrap();
        out.extend(iup_segment(
            &coords[0..start],
            coords[start],
            deltas[start].unwrap(),
            coords[ri2],
            deltas[ri2].unwrap(),
        ));
    }
    out.push(deltas[start].unwrap());
    for &end in iter {
        if end - start > 1 {
            out.extend(iup_segment(
                &coords[start + 1..end],
                coords[start],
                deltas[start].unwrap(),
                coords[end],
                deltas[end].unwrap(),
            ));
        }
        out.push(deltas[end].unwrap());
        start = end;
    }
    if start != n - 1 {
        // Final segment that wraps around.
        let ri2 = indices[0];
        out.extend(iup_segment(
            &coords[start + 1..n],
            coords[start],
            deltas[start].unwrap(),
            coords[ri2],
            deltas[ri2].unwrap(),
        ));
    }
    debug_assert_eq!(out.len(), n);
    out
}

/// Fill missing deltas across the whole glyph (fontTools iup_delta):
/// each contour independently; the last four slots (phantoms) are
/// single-point contours of their own.
pub fn iup_delta(
    deltas: &[Option<(f64, f64)>],
    coords: &[[f64; 2]],
    ends: &[usize],
) -> Vec<(f64, f64)> {
    let n = coords.len();
    let mut out = Vec::with_capacity(n);
    let mut start = 0;
    let phantom_ends = [n - 4, n - 3, n - 2, n - 1];
    for end in ends.iter().chain(phantom_ends.iter()) {
        let end = end + 1;
        out.extend(iup_contour(&deltas[start..end], &coords[start..end]));
        start = end;
    }
    out
}
