//! Corner synthesis by free extrapolation — the "make an instance at
//! the extreme" operation, for pinning corners no source reaches.
//!
//! The VF tent model can't do this: a joint master (one that moves two
//! axes together) reads zero at any location at the default on either
//! axis, so corners that only joint-master trends approach collapse to
//! the default shape. This module instead estimates a per-axis trend
//! function E_a per glyph and continues it linearly past the last
//! master:
//!
//!   - basis: only sources inside the box from the origin toward the
//!     target corner (off-plane masters never pollute the trend);
//!   - pure masters (one axis moved) give direct samples of E_a;
//!   - joint masters are attributed by residual: δ minus the effects of
//!     every already-known axis lands on the remaining one (e.g. a
//!     heavy master at (XOPQ 0.5, YOPQ 0.85) attributes its YOPQ part
//!     via the pure YOPQ master — zero for a YOPQ-invariant glyph — and
//!     its whole heavy delta to XOPQ);
//!   - a joint master over several unknown axes splits its residual by
//!     peak magnitude (documented heuristic, rare in practice);
//!   - E_a continues the outermost segment's slope beyond the last
//!     sample (single sample: straight line through the origin).
//!
//! Brace/interior-region sources are local edits, not trends, and are
//! excluded from the basis by the caller (see `source_deltas`).

use std::collections::HashSet;

/// One basis source: normalized location + per-point deltas vs default.
pub(crate) struct SourceDelta {
    pub loc: Vec<f64>,
    pub deltas: Vec<(f64, f64)>,
}

type PointDeltas = Vec<(f64, f64)>;

fn sub_into(acc: &mut PointDeltas, d: &PointDeltas) {
    for (a, b) in acc.iter_mut().zip(d.iter()) {
        a.0 -= b.0;
        a.1 -= b.1;
    }
}
fn scale(d: &PointDeltas, k: f64) -> PointDeltas {
    d.iter().map(|&(x, y)| (x * k, y * k)).collect()
}

/// A 1D trend: samples of an axis's effect at axis-coordinates,
/// piecewise-linear from the origin, continuing the outermost slope.
struct AxisTrend {
    samples: Vec<(f64, PointDeltas)>, // sorted by |v|, all same sign
}

impl AxisTrend {
    fn eval(&self, v: f64, n_pts: usize) -> PointDeltas {
        let zero = || vec![(0.0, 0.0); n_pts];
        if v == 0.0 || self.samples.is_empty() {
            return zero();
        }
        let lerp = |a: &PointDeltas, b: &PointDeltas, t: f64| -> PointDeltas {
            a.iter()
                .zip(b.iter())
                .map(|((ax, ay), (bx, by))| (ax + (bx - ax) * t, ay + (by - ay) * t))
                .collect()
        };
        let s = &self.samples;
        // Piecewise linear from the origin through the samples (all
        // same-sign, |v| increasing); past the last sample, continue
        // the outermost segment's slope.
        let mut prev_v = 0.0;
        let mut prev_d = zero();
        for (vi, di) in s.iter() {
            if v.abs() <= vi.abs() {
                let t = (v - prev_v) / (*vi - prev_v);
                return lerp(&prev_d, di, t);
            }
            prev_v = *vi;
            prev_d = di.clone();
        }
        if s.len() == 1 {
            return scale(&s[0].1, v / s[0].0);
        }
        let (vn, dn) = &s[s.len() - 1];
        let (vp, dp) = &s[s.len() - 2];
        let t = (v - vp) / (vn - vp);
        lerp(dp, dn, t)
    }
}

/// Synthesize the glyph's points at `coords` (normalized) from the
/// basis sources. `base_pts` are the default-instance points.
pub(crate) fn synthesize(
    base_pts: &[[f64; 2]],
    axis_count: usize,
    sources: &[SourceDelta],
    coords: &[f64],
) -> Vec<[f64; 2]> {
    let n_pts = base_pts.len();

    // Basis: sources on C's side (never beyond C) per axis, with axes
    // C leaves at default unrestricted within the box — a joint master
    // that lives off the default plane still informs the trend (its
    // cross-axis part is peeled off by the residual pass below).
    let basis: Vec<&SourceDelta> = sources
        .iter()
        .filter(|s| {
            s.loc.iter().enumerate().all(|(i, &v)| {
                v == 0.0
                    || (coords[i] != 0.0
                        && v.signum() == coords[i].signum()
                        && v.abs() <= coords[i].abs() + 1e-9)
                    || (coords[i] == 0.0 && v.abs() <= 1.0 + 1e-9)
            })
        })
        .collect();

    let mut trends: Vec<AxisTrend> = (0..axis_count)
        .map(|_| AxisTrend { samples: Vec::new() })
        .collect();
    let mut known: HashSet<usize> = HashSet::new();
    let moved = |s: &SourceDelta| -> Vec<usize> {
        (0..axis_count).filter(|&i| s.loc[i] != 0.0).collect()
    };

    // Pure masters first.
    for s in &basis {
        let m = moved(s);
        if m.len() == 1 {
            let a = m[0];
            trends[a].samples.push((s.loc[a], s.deltas.clone()));
            trends[a].samples.sort_by(|x, y| x.0.abs().partial_cmp(&y.0.abs()).unwrap());
            known.insert(a);
        }
    }
    // Joint masters by residual, to a fixpoint.
    let mut joints: Vec<&SourceDelta> = basis.iter().copied().filter(|s| moved(s).len() >= 2).collect();
    loop {
        let mut progress = false;
        let mut deferred = Vec::new();
        for s in joints {
            let m = moved(s);
            let unknown: Vec<usize> = m.iter().copied().filter(|a| !known.contains(a)).collect();
            let mut residual = s.deltas.clone();
            for &a in m.iter().filter(|a| known.contains(a)) {
                let eff = trends[a].eval(s.loc[a], n_pts);
                sub_into(&mut residual, &eff);
            }
            if unknown.is_empty() {
                continue; // fully explained already
            }
            if unknown.len() == 1 {
                let a = unknown[0];
                trends[a].samples.push((s.loc[a], residual));
                trends[a].samples.sort_by(|x, y| x.0.abs().partial_cmp(&y.0.abs()).unwrap());
                known.insert(a);
                progress = true;
            } else {
                // Split the residual among unknown axes by peak magnitude.
                let total: f64 = unknown.iter().map(|&a| s.loc[a].abs()).sum();
                if total > 1e-9 {
                    for &a in &unknown {
                        let share = s.loc[a].abs() / total;
                        trends[a].samples.push((s.loc[a], scale(&residual, share)));
                        trends[a].samples.sort_by(|x, y| x.0.abs().partial_cmp(&y.0.abs()).unwrap());
                        known.insert(a);
                        progress = true;
                    }
                } else {
                    deferred.push(s);
                }
            }
        }
        joints = deferred;
        if !progress || joints.is_empty() {
            break;
        }
    }

    let mut out = base_pts.to_vec();
    for a in 0..axis_count {
        let v = coords[a];
        if v == 0.0 {
            continue;
        }
        let eff = trends[a].eval(v, n_pts);
        add_into_points(&mut out, &eff);
    }
    out
}

fn add_into_points(pts: &mut [[f64; 2]], d: &PointDeltas) {
    for (p, (dx, dy)) in pts.iter_mut().zip(d.iter()) {
        p[0] += dx;
        p[1] += dy;
    }
}
