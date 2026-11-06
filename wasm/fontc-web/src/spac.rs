//! SPAC transform application (the bundle's `transforms` section) as
//! binary font surgery, ported from the two reference implementations:
//!
//! - **Uniform `spac`** — `gftools gen-spac`
//!   (`gftools/scripts/gen_spac.py`, mirrored by the studio's
//!   `transforms/builtin_spac.py`): every outline glyph that already has
//!   gvar variations tracks by the same per-side amount — two gvar tuples
//!   per glyph on the phantom points `[-4]` (origin) and `[-3]`
//!   (advance), so SPAC ±N moves each sidebearing by ±N (advance ±2N).
//!   Composite glyphs are left at their own width, exactly as `gen-spac`
//!   leaves them (E2k-b).
//! - **Width-aware `spac_widthaware`** — the studio's own
//!   `transforms/builtin_spac_widthaware.py`: the per-side amount
//!   scales with `(ink / mean_ink) ** bias`, so the font loosens by a
//!   consistent proportion of each glyph's own width (wider glyphs get
//!   more). The amount is computed at EACH master corner from that
//!   master's own ink (E2i-c) — the corners come from the caller as
//!   `params.master_locations`, normalized against the built font's
//!   fvar/avar — as the integer the sources export writes
//!   (`spac_amounts.per_side`, with its 1 u floor on the tight end), and
//!   the corners' amounts become gvar deltas through fontTools'
//!   VariationModel (`getDeltas(…, round=round)`). Composites are
//!   spaced too — with their base (first) component's amount on both
//!   sides (E2k) — USE_MY_METRICS composites are skipped (they already
//!   inherit their base glyph's delta), and `.notdef` is left alone.
//!
//! Both variants inject a SPAC fvar axis (range params.min..max,
//! default 0) and rebuild HVAR from the gvar phantom deltas (fontTools
//! `varLib.hvar.add_HVAR`), so the advance adjustment is
//! variation-driven: SPAC stays a live slider in the built font.
//! Per-instance SPAC coordinates ride the transforms JSON
//! (`params.instance_spac`), parsed from the mappings CSV by the caller
//! (`builtin_spac._instance_spac_overrides` / csv_io).

use std::collections::HashMap;

use serde::Deserialize;
use wasm_bindgen::prelude::*;

use font_types::{F2Dot14, Tag};
use read_fonts::tables::glyf::{Anchor, CompositeGlyphFlags, Glyph};
use read_fonts::tables::gvar::GlyphDelta;
use read_fonts::tables::variations::TupleVariation;
use read_fonts::types::{GlyphId, GlyphId16};
use read_fonts::{FontRef, TableProvider};
use write_fonts::tables::hvar as w_hvar;
use write_fonts::tables::variations::ivs_builder::VariationStoreBuilder;
use write_fonts::tables::variations::{
    DeltaSetIndexMap, RegionAxisCoordinates, VariationRegion,
};

use crate::braces::{brace_coords, build_grown_font, GrowOptions, NewFvarAxis};
use crate::{dump_replacement, err, fl2fi, NewTuple};

const SPAC_TAG: Tag = Tag::new(b"SPAC");
const SPAC_NAME: &str = "Spacing";

// --------------------------------------------------------------------------
// Bundle JSON shapes + parameter coercion
// --------------------------------------------------------------------------

#[derive(Deserialize)]
struct TransformEntry {
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    enabled: bool,
    #[serde(default)]
    params: HashMap<String, serde_json::Value>,
}

/// `int(params.get(key, default))` — truncating like Python's int().
fn int_param(params: &HashMap<String, serde_json::Value>, key: &str, default: i32) -> Result<i32, JsError> {
    match params.get(key) {
        None | Some(serde_json::Value::Null) => Ok(default),
        Some(serde_json::Value::Number(n)) => Ok(n.as_f64().unwrap_or(default as f64) as i32),
        Some(v) => Err(err(format!("SPAC {key} must be a number, got {v}"))),
    }
}

/// `float(params.get(key, default))` with the reference's try/except
/// fallback to the default, then the ParamSpec clamp (bias [1.0, 4.0],
/// scale [0.1, 10.0]).
/// A numeric value, or a numeric STRING — the flyout passes raw strings
/// (the desktop server coerces them; the static demo stores them as-is).
fn json_f64(v: &serde_json::Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str().and_then(|s| s.trim().parse::<f64>().ok()))
}

fn float_param(params: &HashMap<String, serde_json::Value>, key: &str, default: f64, clamp: (f64, f64)) -> f64 {
    params
        .get(key)
        .and_then(json_f64)
        .unwrap_or(default)
        .clamp(clamp.0, clamp.1)
}

// --------------------------------------------------------------------------
// Read-side view of the glyf/gvar data SPAC needs (all by gid — glyph
// names are never involved)
// --------------------------------------------------------------------------

struct SpacFont<'a> {
    glyf: read_fonts::tables::glyf::Glyf<'a>,
    loca: read_fonts::tables::loca::Loca<'a>,
    gvar: read_fonts::tables::gvar::Gvar<'a>,
    num_glyphs: u16,
}

impl<'a> SpacFont<'a> {
    fn new(font: &FontRef<'a>) -> Result<Self, JsError> {
        Ok(Self {
            glyf: font.glyf().map_err(|e| err(format!("missing glyf: {e}")))?,
            loca: font.loca(None).map_err(|e| err(format!("missing loca: {e}")))?,
            gvar: font.gvar().map_err(|e| err(format!("missing gvar: {e}")))?,
            num_glyphs: font
                .maxp()
                .map_err(|e| err(format!("missing maxp: {e}")))?
                .num_glyphs(),
        })
    }

    fn glyph(&self, gid: u16) -> Result<Option<Glyph<'a>>, JsError> {
        self.loca
            .get_glyf(GlyphId::new(gid as u32), &self.glyf)
            .map_err(|e| err(format!("glyph {gid}: {e}")))
    }

    /// `_gvar_point_count`: contour points (simple) or components
    /// (composite) + the 4 phantoms. For glyphs with existing
    /// variations this equals gen_spac's
    /// `len(gvar.variations[name][0].coordinates)` by construction.
    fn gvar_point_count(&self, gid: u16) -> Result<usize, JsError> {
        Ok(match self.glyph(gid)? {
            None => 4,
            Some(Glyph::Simple(simple)) => simple.points().count() + 4,
            Some(Glyph::Composite(composite)) => composite.components().count() + 4,
        })
    }

    /// gen_spac's `gvar.variations.get(glyph_name)`: has variation data
    /// with at least one tuple.
    fn has_variations(&self, gid: u16) -> Result<bool, JsError> {
        let data = self
            .gvar
            .glyph_variation_data(GlyphId::new(gid as u32))
            .map_err(|e| err(format!("glyph {gid} gvar: {e}")))?;
        Ok(data.is_some_and(|d| d.tuples().next().is_some()))
    }

    fn has_use_my_metrics(&self, composite: &read_fonts::tables::glyf::CompositeGlyph) -> bool {
        composite
            .components()
            .any(|c| c.flags.contains(CompositeGlyphFlags::USE_MY_METRICS))
    }
}

/// 2×3 affine [xx, xy, yx, yy, dx, dy]: x' = xx·x + xy·y + dx,
/// y' = yx·x + yy·y + dy (fontTools Transform order).
type Affine = [f64; 6];

fn apply_affine(t: &Affine, x: f64, y: f64) -> (f64, f64) {
    (t[0] * x + t[1] * y + t[4], t[2] * x + t[3] * y + t[5])
}

fn compose(p: &Affine, c: &Affine) -> Affine {
    [
        p[0] * c[0] + p[1] * c[2],
        p[0] * c[1] + p[1] * c[3],
        p[2] * c[0] + p[3] * c[2],
        p[2] * c[1] + p[3] * c[3],
        p[0] * c[4] + p[1] * c[5] + p[4],
        p[2] * c[4] + p[3] * c[5] + p[5],
    ]
}

// --------------------------------------------------------------------------
// Tuple construction (the two variants)
// --------------------------------------------------------------------------

/// Uniform `spac` (gen_spac `add_spacing_axis`): glyphs WITH outline
/// coordinates (simple only) and existing gvar variations get two
/// tuples, engaged at SPAC ±1, moving both horizontal phantoms by ±N.
fn uniform_extras(
    view: &SpacFont,
    spac_idx: usize,
    total_axes: usize,
    lo: i32,
    hi: i32,
) -> Result<HashMap<u16, Vec<NewTuple>>, JsError> {
    let mut extras = HashMap::new();
    for gid in 0..view.num_glyphs {
        // E2k-b: gftools parity — `gen-spac` skips composites
        // (`if not hasattr(glyph, "coordinates"): continue`), so the uniform
        // mode leaves them at their own width. The user's composite rule (E2k)
        // belongs to the width-aware mode, which spaces them like their base.
        if !matches!(view.glyph(gid)?, Some(Glyph::Simple(_))) || !view.has_variations(gid)? {
            continue;
        }
        let n = view.gvar_point_count(gid)?;
        let mut tuples = Vec::with_capacity(2);
        for (norm, amount) in [(-1.0, lo), (1.0, hi)] {
            let mut tuple = brace_coords(total_axes, spac_idx, norm);
            tuple.deltas = vec![(0, 0); n];
            tuple.deltas[n - 4] = (-amount as i16, 0);
            tuple.deltas[n - 3] = (amount as i16, 0);
            tuples.push(tuple);
        }
        extras.insert(gid, tuples);
    }
    Ok(extras)
}

// --------------------------------------------------------------------------
// Width-aware SPAC, E2i-c: one amount per master corner
//
// A port of transforms/builtin_spac_widthaware.py + spac_amounts.py and of
// the fontTools routines they call (BoundsPen over the instanced glyph set,
// supportScalar, VariationModel), operation for operation: the amounts are
// rounded to integers, so both sides must reach the same floats first.
// --------------------------------------------------------------------------

/// A sparse normalized location, keyed by fvar axis index, in the order
/// the reference's dict holds its items (float products follow it).
type Corner = Vec<(usize, f64)>;
/// A support region: (axis, lower, peak, upper), in dict order likewise.
type Support = Vec<(usize, f64, f64, f64)>;

/// One master location in USER units as the caller wrote it
/// (`{"XTRA": 3330.0, …}`), key order kept — serde_json's own maps sort.
struct UserLocation(Vec<(String, Option<f64>)>);

impl<'de> Deserialize<'de> for UserLocation {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct Items;
        impl<'de> serde::de::Visitor<'de> for Items {
            type Value = UserLocation;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("a map of axis tag to user-space value")
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(
                self,
                mut map: A,
            ) -> Result<UserLocation, A::Error> {
                let mut items = Vec::new();
                while let Some(item) = map.next_entry::<String, Option<f64>>()? {
                    items.push(item);
                }
                Ok(UserLocation(items))
            }
        }
        d.deserialize_map(Items)
    }
}

/// The transforms JSON again, reading only `params.master_locations`
/// (`BuildContext.master_locations`: the designspace's masters, with the
/// default as `{}` — pipeline.py `_master_locations`).
#[derive(Deserialize)]
struct MasterLocationsEntry {
    #[serde(default)]
    params: MasterLocationsParams,
}

#[derive(Deserialize, Default)]
struct MasterLocationsParams {
    #[serde(default)]
    master_locations: Option<Vec<Option<UserLocation>>>,
}

/// fontTools `models.supportScalar(location, support)` (ot=True), with
/// `coords` the location by fvar axis index (absent axes are 0).
fn support_scalar(coords: &[f64], support: &[(usize, f64, f64, f64)]) -> f64 {
    let mut scalar = 1.0;
    for &(axis, lower, peak, upper) in support {
        if peak == 0.0 {
            continue;
        }
        if lower > peak || peak > upper {
            continue;
        }
        if lower < 0.0 && upper > 0.0 {
            continue;
        }
        let v = coords[axis];
        if v == peak {
            continue;
        }
        if v <= lower || upper <= v {
            return 0.0;
        }
        if v < peak {
            scalar *= (v - lower) / (peak - lower);
        } else {
            scalar *= (v - upper) / (peak - upper);
        }
    }
    scalar
}

/// Python's `round(value, 6)`: the nearest decimal with six places to the
/// double's exact value, ties to even (Rust's `{:.6}` rounds the same way).
fn round6(value: f64) -> f64 {
    format!("{value:.6}").parse().expect("a formatted float parses")
}

/// `_avar_map`: an avar segment map, user-normalized → design-normalized.
/// `points` is sorted by `from`; an empty map is the identity.
fn avar_map(value: f64, points: &[(f64, f64)]) -> f64 {
    let (Some(first), Some(last)) = (points.first(), points.last()) else {
        return value;
    };
    if value <= first.0 {
        return first.1;
    }
    if value >= last.0 {
        return last.1;
    }
    for pair in points.windows(2) {
        let ((x0, y0), (x1, y1)) = (pair[0], pair[1]);
        if x0 <= value && value <= x1 {
            if x1 == x0 {
                return y1;
            }
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0);
        }
    }
    value
}

/// Dict equality for two sparse locations (key order does not count).
fn same_location(a: &Corner, b: &Corner) -> bool {
    a.len() == b.len() && a.iter().all(|item| b.contains(item))
}

/// The built font's axes as `_normalize_corners` reads them: fvar's
/// (min, default, max) and avar's v1 segment map per axis.
struct FontAxes {
    tags: Vec<Tag>,
    triples: Vec<(f64, f64, f64)>,
    /// `None` without an avar table; else one map per fvar axis, sorted by
    /// `from` (a later duplicate `from` replaces the earlier one, as in
    /// fontTools' dict).
    segments: Option<Vec<Vec<(f64, f64)>>>,
}

impl FontAxes {
    fn new(font: &FontRef) -> Result<Self, JsError> {
        let fvar = font
            .fvar()
            .map_err(|e| err(format!("missing/invalid fvar: {e}")))?;
        let axes = fvar
            .axis_instance_arrays()
            .map_err(|e| err(format!("missing/invalid fvar: {e}")))?;
        let tags: Vec<Tag> = axes.axes().iter().map(|a| a.axis_tag()).collect();
        let triples = axes
            .axes()
            .iter()
            .map(|a| {
                (
                    a.min_value().to_f64(),
                    a.default_value().to_f64(),
                    a.max_value().to_f64(),
                )
            })
            .collect();
        let segments = match font.avar() {
            Err(_) => None,
            Ok(avar) => {
                let mut maps: Vec<Vec<(f64, f64)>> = vec![Vec::new(); tags.len()];
                for (slot, map) in maps.iter_mut().zip(avar.axis_segment_maps().iter()) {
                    let map = map.map_err(|e| err(format!("avar segment map: {e}")))?;
                    let mut by_from = std::collections::BTreeMap::new();
                    for pair in map.axis_value_maps() {
                        by_from.insert(pair.from_coordinate().to_bits(), pair.to_coordinate());
                    }
                    *slot = by_from
                        .into_iter()
                        .map(|(from, to)| (F2Dot14::from_bits(from).to_f64(), to.to_f64()))
                        .collect();
                }
                Some(maps)
            }
        };
        Ok(Self { tags, triples, segments })
    }

    /// `_normalize_corners`: USER-SPACE master locations → design-space
    /// normalized corners, against the BUILT font's fvar and avar (the
    /// space gvar deltas are written in). Tags the font lacks and null
    /// values are skipped; a location that normalizes onto one already
    /// listed collapses into it.
    fn normalize_corners(&self, locations: &[Option<UserLocation>]) -> Vec<Corner> {
        let mut out: Vec<Corner> = Vec::new();
        for location in locations {
            let mut normalized: Corner = Vec::new();
            for (tag, value) in location.iter().flat_map(|l| l.0.iter()) {
                let Some(axis) = self.tags.iter().position(|t| t.to_string() == *tag) else {
                    continue;
                };
                let Some(value) = *value else { continue };
                let (lo, default, hi) = self.triples[axis];
                let span = if value > default { hi - default } else { default - lo };
                let mut norm = if span != 0.0 { (value - default) / span } else { 0.0 };
                if let Some(segments) = &self.segments {
                    norm = avar_map(norm, &segments[axis]);
                }
                if norm != 0.0 {
                    let rounded = round6(norm);
                    match normalized.iter_mut().find(|(a, _)| *a == axis) {
                        Some(item) => item.1 = rounded,
                        None => normalized.push((axis, rounded)),
                    }
                }
            }
            if !out.iter().any(|c| same_location(c, &normalized)) {
                out.push(normalized);
            }
        }
        out
    }
}

/// fontTools `varLib.models.VariationModel(corners, axisOrder=sorted(tags))`
/// as the reference uses it: supports, delta weights and `getDeltas`.
struct Model {
    /// One support per master, in the model's own (sorted) master order.
    supports: Vec<Support>,
    /// Per sorted master: (earlier master, its support's scalar here).
    delta_weights: Vec<Vec<(usize, f64)>>,
    /// Sorted master → index in the caller's corner list.
    reverse_mapping: Vec<usize>,
}

impl Model {
    fn new(corners: &[Corner], tags: &[Tag]) -> Result<Self, String> {
        let locations: Vec<Corner> = corners
            .iter()
            .map(|c| c.iter().copied().filter(|&(_, v)| v != 0.0).collect())
            .collect();
        for (i, a) in locations.iter().enumerate() {
            if locations[..i].iter().any(|b| same_location(a, b)) {
                return Err("Locations must be unique.".into());
            }
        }
        if !locations.iter().any(|l| l.is_empty()) {
            return Err("Base master not found.".into());
        }

        // getMasterLocationsSortKeyFunc: the on-axis points per axis.
        let mut axis_points: HashMap<usize, Vec<f64>> = HashMap::new();
        for loc in &locations {
            if let [(axis, value)] = loc[..] {
                axis_points.entry(axis).or_insert_with(|| vec![0.0]).push(value);
            }
        }
        // The key: rank, fewer-on-point last, then the location's axes in
        // tag order (axisOrder is every tag, sorted, so its index tuple
        // orders exactly like the tags), their signs, their magnitudes.
        let key = |loc: &Corner| {
            let on_point = loc
                .iter()
                .filter(|(axis, value)| axis_points.get(axis).is_some_and(|p| p.contains(value)))
                .count();
            let mut ordered: Vec<(Tag, f64)> = loc.iter().map(|&(a, v)| (tags[a], v)).collect();
            ordered.sort_by_key(|&(tag, _)| tag);
            let signs: Vec<i8> = ordered.iter().map(|&(_, v)| if v < 0.0 { -1 } else { 1 }).collect();
            let sizes: Vec<f64> = ordered.iter().map(|&(_, v)| v.abs()).collect();
            let axes: Vec<Tag> = ordered.iter().map(|&(tag, _)| tag).collect();
            (loc.len(), std::cmp::Reverse(on_point), axes, signs, sizes)
        };
        let mut order: Vec<usize> = (0..locations.len()).collect();
        let keys: Vec<_> = locations.iter().map(key).collect();
        order.sort_by(|&a, &b| {
            keys[a]
                .partial_cmp(&keys[b])
                .expect("master locations are finite")
        });
        let sorted: Vec<&Corner> = order.iter().map(|&i| &locations[i]).collect();

        // _locationsToRegions (axis ranges are -1..1), then
        // _computeMasterSupports: each earlier master with the same axes
        // that sits inside this box splits it, along the axes with the
        // largest (equal) range ratio.
        let mut regions: Vec<Support> = sorted
            .iter()
            .map(|loc| {
                loc.iter()
                    .map(|&(axis, v)| if v > 0.0 { (axis, 0.0, v, 1.0) } else { (axis, -1.0, v, 0.0) })
                    .collect()
            })
            .collect();
        let peak_of = |region: &Support, axis: usize| {
            region.iter().find(|r| r.0 == axis).map(|r| r.2)
        };
        for i in 0..regions.len() {
            for j in 0..i {
                let prev = regions[j].clone();
                if prev.len() != regions[i].len()
                    || !prev.iter().all(|p| peak_of(&regions[i], p.0).is_some())
                {
                    continue;
                }
                let relevant = regions[i].iter().all(|&(axis, lower, peak, upper)| {
                    let prev_peak = peak_of(&prev, axis).expect("same axes");
                    prev_peak == peak || (lower < prev_peak && prev_peak < upper)
                });
                if !relevant {
                    continue;
                }
                let mut best: Vec<(usize, f64, f64, f64)> = Vec::new();
                let mut best_ratio = -1.0;
                for &(axis, _, val, _) in &prev {
                    let &(_, lower, loc_v, upper) =
                        regions[i].iter().find(|r| r.0 == axis).expect("same axes");
                    let (mut new_lower, mut new_upper) = (lower, upper);
                    let ratio = if val < loc_v {
                        new_lower = val;
                        (val - loc_v) / (lower - loc_v)
                    } else if loc_v < val {
                        new_upper = val;
                        (val - loc_v) / (upper - loc_v)
                    } else {
                        continue; // can't split the box in this direction
                    };
                    if !ratio.is_finite() {
                        // fontTools divides by zero here and the build fails.
                        return Err("a master lies outside the axis range".into());
                    }
                    if ratio > best_ratio {
                        best.clear();
                        best_ratio = ratio;
                    }
                    if ratio == best_ratio {
                        best.push((axis, new_lower, loc_v, new_upper));
                    }
                }
                for triple in best {
                    let slot = regions[i].iter_mut().find(|r| r.0 == triple.0).expect("same axes");
                    *slot = triple;
                }
            }
        }

        // _computeDeltaWeights
        let mut delta_weights = Vec::with_capacity(sorted.len());
        for (i, loc) in sorted.iter().enumerate() {
            let mut coords = vec![0.0; tags.len()];
            for &(axis, v) in loc.iter() {
                coords[axis] = v;
            }
            let weights: Vec<(usize, f64)> = regions[..i]
                .iter()
                .enumerate()
                .map(|(j, support)| (j, support_scalar(&coords, support)))
                .filter(|&(_, scalar)| scalar != 0.0)
                .collect();
            delta_weights.push(weights);
        }
        Ok(Self { supports: regions, delta_weights, reverse_mapping: order })
    }

    /// `getDeltas(masterValues, round=round)` over integer master values:
    /// each delta is rounded (ties to even) before the later ones subtract
    /// it, so the model reads every master back exactly.
    fn deltas(&self, master_values: &[i64]) -> Vec<i64> {
        let mut out: Vec<f64> = Vec::with_capacity(self.delta_weights.len());
        for (i, weights) in self.delta_weights.iter().enumerate() {
            let mut delta = master_values[self.reverse_mapping[i]] as f64;
            for &(j, weight) in weights {
                if weight == 1.0 {
                    delta -= out[j];
                } else {
                    delta -= out[j] * weight;
                }
            }
            out.push(delta.round_ties_even());
        }
        out.into_iter().map(|d| d as i64).collect()
    }
}

/// spac_amounts.fit_dx: cap a NEGATIVE move so the advance stays ≥ 1 u.
fn fit_dx(dx: i64, advance: f64, composite: bool) -> i64 {
    if dx >= 0 {
        return dx;
    }
    let advance = advance.trunc() as i64; // int(advance)
    let lowest = if composite {
        1 - advance
    } else {
        -(advance - 1).div_euclid(2)
    };
    dx.max(lowest)
}

/// spac_amounts.per_side: one corner's per-side amount, as the integer
/// both renderers apply (Python's round() is round-ties-even).
#[allow(clippy::too_many_arguments)]
fn per_side(
    value: f64,
    ink: f64,
    mean_here: f64,
    mean_default: f64,
    bias: f64,
    scale: f64,
    advance: f64,
    composite: bool,
) -> i64 {
    if mean_here <= 0.0 || mean_default <= 0.0 {
        return 0;
    }
    let amount = value * (ink / mean_here).powf(bias) * (mean_here / mean_default) * scale;
    let dx = amount.round_ties_even() as i64;
    if dx < 0 {
        fit_dx(dx, advance, composite)
    } else {
        dx
    }
}

/// fontTools `BoundsPen` (through `BasePen`): exact bounds of lines and
/// quadratic curves, as (xMin, yMin, xMax, yMax).
#[derive(Default)]
struct BoundsPen {
    bounds: Option<[f64; 4]>,
    current: (f64, f64),
}

impl BoundsPen {
    fn add(&mut self, (x, y): (f64, f64)) {
        self.bounds = Some(match self.bounds {
            None => [x, y, x, y],
            Some(b) => [b[0].min(x), b[1].min(y), b[2].max(x), b[3].max(y)],
        });
    }

    fn move_to(&mut self, pt: (f64, f64)) {
        self.add(pt);
        self.current = pt;
    }

    fn line_to(&mut self, pt: (f64, f64)) {
        self.add(pt);
        self.current = pt;
    }

    /// `_qCurveToOne`: the end point always counts; the curve itself only
    /// when its control point lies outside the bounds so far.
    fn curve_to(&mut self, bcp: (f64, f64), pt: (f64, f64)) {
        self.add(pt);
        let b = self.bounds.expect("a moveTo precedes every curve");
        if !(b[0] <= bcp.0 && bcp.0 <= b[2] && b[1] <= bcp.1 && bcp.1 <= b[3]) {
            let q = quadratic_bounds(self.current, bcp, pt);
            self.bounds = Some([b[0].min(q[0]), b[1].min(q[1]), b[2].max(q[2]), b[3].max(q[3])]);
        }
        self.current = pt;
    }

    /// `BasePen.qCurveTo(*points)`: off-curve points, then the on-curve
    /// end; an implied on-curve point sits midway between two off-curves.
    fn q_curve_to(&mut self, points: &[(f64, f64)]) {
        let n = points.len() - 1;
        if n == 0 {
            return self.line_to(points[0]);
        }
        for i in 0..n - 1 {
            let ((x, y), (nx, ny)) = (points[i], points[i + 1]);
            self.curve_to(points[i], (0.5 * (x + nx), 0.5 * (y + ny)));
        }
        self.curve_to(points[n - 1], points[n]);
    }

    /// One TrueType contour, as `Glyph.draw` feeds it to the pen.
    fn contour(&mut self, pts: &[(f64, f64)], on_curve: &[bool]) {
        let Some(first_on) = on_curve.iter().position(|&on| on) else {
            // No on-curve point at all: the pen starts at the implied point
            // between the last and the first off-curve.
            let ((x, y), (nx, ny)) = (pts[pts.len() - 1], pts[0]);
            let start = (0.5 * (x + nx), 0.5 * (y + ny));
            self.move_to(start);
            let mut points = pts.to_vec();
            points.push(start);
            return self.q_curve_to(&points);
        };
        // Rotate so the contour ENDS on its first on-curve point.
        let split = first_on + 1;
        let pts: Vec<(f64, f64)> = pts[split..].iter().chain(&pts[..split]).copied().collect();
        let on: Vec<bool> = on_curve[split..].iter().chain(&on_curve[..split]).copied().collect();
        self.move_to(pts[pts.len() - 1]);
        let mut start = 0;
        while start < pts.len() {
            let next = on[start..].iter().position(|&o| o).expect("ends on-curve") + 1;
            if next == 1 {
                // The final lineTo is implied by closePath.
                if pts.len() - start > 1 {
                    self.line_to(pts[start]);
                }
            } else {
                self.q_curve_to(&pts[start..start + next]);
            }
            start += next;
        }
    }
}

/// fontTools `bezierTools.calcQuadraticBounds`.
fn quadratic_bounds(pt1: (f64, f64), pt2: (f64, f64), pt3: (f64, f64)) -> [f64; 4] {
    let (cx, cy) = pt1;
    let bx = (pt2.0 - cx) * 2.0;
    let by = (pt2.1 - cy) * 2.0;
    let ax = pt3.0 - cx - bx;
    let ay = pt3.1 - cy - by;
    let (ax2, ay2) = (ax * 2.0, ay * 2.0);
    let mut points = Vec::with_capacity(4);
    for (a2, b) in [(ax2, bx), (ay2, by)] {
        if a2 != 0.0 {
            let t = -b / a2;
            if (0.0..1.0).contains(&t) {
                points.push((ax * t * t + bx * t + cx, ay * t * t + by * t + cy));
            }
        }
    }
    points.extend([pt1, pt3]);
    let mut b = [points[0].0, points[0].1, points[0].0, points[0].1];
    for &(x, y) in &points[1..] {
        b = [b[0].min(x), b[1].min(y), b[2].max(x), b[3].max(y)];
    }
    b
}

/// A glyph's static outline structure (its points live in `base`).
enum Shape {
    Empty,
    Simple { on_curve: Vec<bool>, ends: Vec<usize> },
    Composite(Vec<Component>),
}

struct Component {
    gid: u16,
    /// The 2×2 in `Affine` order.
    scale: [f64; 4],
    /// False when the component is placed by matching points: fontTools
    /// cannot draw those (`getComponentInfo` raises), so the glyph's ink is
    /// unmeasurable.
    has_offset: bool,
}

/// Everything the width-aware SPAC measures on the built font.
struct WidthAware<'v, 'a> {
    view: &'v SpacFont<'a>,
    axes: FontAxes,
    shapes: Vec<Shape>,
    /// Per glyph: contour points (or component offsets), then the phantoms
    /// (left, right, top, bottom) — fontTools `_getCoordinatesAndControls`.
    base: Vec<Vec<[f64; 2]>>,
    advances: Vec<f64>,
    hvar: Option<read_fonts::tables::hvar::Hvar<'a>>,
    /// `.notdef` by name, as the reference excludes it; glyph 0 when the
    /// font carries no glyph names.
    notdef: Option<u16>,
    names: Vec<Option<String>>,
}

impl<'v, 'a> WidthAware<'v, 'a> {
    fn new(font: &FontRef<'a>, view: &'v SpacFont<'a>) -> Result<Self, JsError> {
        let hmtx = font.hmtx().map_err(|e| err(format!("missing hmtx: {e}")))?;
        let count = view.num_glyphs as usize;
        let (mut shapes, mut base, mut advances) =
            (Vec::with_capacity(count), Vec::with_capacity(count), Vec::with_capacity(count));
        for gid in 0..view.num_glyphs {
            let (shape, mut pts, x_min) = match view.glyph(gid)? {
                None => (Shape::Empty, Vec::new(), 0),
                Some(Glyph::Simple(simple)) => {
                    let (mut pts, mut on_curve) = (Vec::new(), Vec::new());
                    for p in simple.points() {
                        pts.push([p.x as f64, p.y as f64]);
                        on_curve.push(p.on_curve);
                    }
                    let ends = simple
                        .end_pts_of_contours()
                        .iter()
                        .map(|e| e.get() as usize)
                        .collect();
                    (Shape::Simple { on_curve, ends }, pts, simple.x_min())
                }
                Some(Glyph::Composite(composite)) => {
                    let (mut pts, mut comps) = (Vec::new(), Vec::new());
                    for comp in composite.components() {
                        let (offset, has_offset) = match comp.anchor {
                            Anchor::Offset { x, y } => ([x as f64, y as f64], true),
                            Anchor::Point { .. } => ([0.0, 0.0], false),
                        };
                        pts.push(offset);
                        let t = &comp.transform;
                        comps.push(Component {
                            gid: comp.glyph.to_u32() as u16,
                            scale: [t.xx.to_f64(), t.xy.to_f64(), t.yx.to_f64(), t.yy.to_f64()],
                            has_offset,
                        });
                    }
                    (Shape::Composite(comps), pts, composite.x_min())
                }
            };
            let id = GlyphId::new(gid as u32);
            let advance = hmtx
                .advance(id)
                .ok_or_else(|| err(format!("glyph {gid}: no hmtx entry")))? as f64;
            let left = x_min as f64 - hmtx.side_bearing(id).unwrap_or(0) as f64;
            // Only x is read back from the phantoms (advance, sidebearing).
            pts.extend([[left, 0.0], [left + advance, 0.0], [0.0, 0.0], [0.0, 0.0]]);
            shapes.push(shape);
            base.push(pts);
            advances.push(advance);
        }
        let names: Vec<Option<String>> = match font.post() {
            Ok(post) => (0..view.num_glyphs)
                .map(|gid| post.glyph_name(GlyphId16::new(gid)).map(str::to_string))
                .collect(),
            Err(_) => vec![None; count],
        };
        let notdef = if names.iter().all(Option::is_none) {
            Some(0)
        } else {
            names.iter().position(|n| n.as_deref() == Some(".notdef")).map(|i| i as u16)
        };
        Ok(Self {
            view,
            axes: FontAxes::new(font)?,
            shapes,
            base,
            advances,
            hvar: font.hvar().ok(),
            notdef,
            names,
        })
    }

    fn name(&self, gid: u16) -> String {
        self.names[gid as usize].clone().unwrap_or_else(|| format!("glyph{gid:05}"))
    }

    /// `_parametric_corners`: the fallback when the caller hands no master
    /// locations — every distinct peak vector of the font's own gvar, after
    /// the default. (The font has no SPAC axis yet, so nothing to exclude.)
    fn parametric_corners(&self) -> Result<Vec<Corner>, JsError> {
        let mut corners: Vec<Corner> = vec![Vec::new()];
        for gid in 0..self.view.num_glyphs {
            let Some(data) = self
                .view
                .gvar
                .glyph_variation_data(GlyphId::new(gid as u32))
                .map_err(|e| err(format!("glyph {gid} gvar: {e}")))?
            else {
                continue;
            };
            for tuple in data.tuples() {
                let peak = tuple.peak();
                let peaks: Corner = (0..self.axes.tags.len())
                    .filter_map(|i| peak.get(i).map(|v| (i, v.to_f64())))
                    .filter(|&(_, v)| v != 0.0)
                    .collect();
                if !peaks.is_empty() && !corners.iter().any(|c| same_location(c, &peaks)) {
                    corners.push(peaks);
                }
            }
        }
        Ok(corners)
    }

    /// Every glyph's points at a normalized location: the base points plus
    /// each active tuple's deltas (IUP-filled) times its scalar — fontTools
    /// `_TTGlyphGlyf._getGlyphInstance`. The default location is the base.
    fn points_at(&self, coords: &[f64]) -> Result<Vec<Vec<[f64; 2]>>, JsError> {
        if coords.iter().all(|&c| c == 0.0) {
            return Ok(self.base.clone());
        }
        let mut out = Vec::with_capacity(self.base.len());
        for gid in 0..self.view.num_glyphs {
            let base = &self.base[gid as usize];
            let mut pts = base.clone();
            let data = self
                .view
                .gvar
                .glyph_variation_data(GlyphId::new(gid as u32))
                .map_err(|e| err(format!("glyph {gid} gvar: {e}")))?;
            for tuple in data.iter().flat_map(|d| d.tuples()) {
                let scalar = support_scalar(coords, &tuple_support(&tuple, coords.len()));
                if scalar == 0.0 {
                    continue;
                }
                let mut deltas: Vec<Option<(f64, f64)>> = vec![None; pts.len()];
                for delta in tuple.deltas() {
                    if let Some(slot) = deltas.get_mut(delta.position as usize) {
                        *slot = Some((delta.x_delta as f64, delta.y_delta as f64));
                    }
                }
                // Untouched points: interpolated along their contour for a
                // simple glyph; a composite's components and the phantoms
                // are one-point contours, so they stay put.
                let filled = match &self.shapes[gid as usize] {
                    Shape::Simple { ends, .. } => crate::iup::iup_delta(&deltas, base, ends),
                    _ => deltas.into_iter().map(|d| d.unwrap_or((0.0, 0.0))).collect(),
                };
                for (p, (dx, dy)) in pts.iter_mut().zip(filled) {
                    p[0] += dx * scalar;
                    p[1] += dy * scalar;
                }
            }
            out.push(pts);
        }
        Ok(out)
    }

    /// `_ink_width` over a glyph set: the BoundsPen width of the outline,
    /// composites resolved through the same glyph set. `None` is the
    /// reference's swallowed exception (0.0 there).
    fn ink_width(&self, points: &[Vec<[f64; 2]>], instanced: bool, gid: u16) -> Option<f64> {
        let mut pen = BoundsPen::default();
        self.draw(points, instanced, gid, None, &mut pen, 0)?;
        pen.bounds.map(|b| b[2] - b[0])
    }

    fn draw(
        &self,
        points: &[Vec<[f64; 2]>],
        instanced: bool,
        gid: u16,
        transform: Option<&Affine>,
        pen: &mut BoundsPen,
        depth: u32,
    ) -> Option<()> {
        if depth > 64 {
            return None; // a component cycle: RecursionError in the reference
        }
        let pts = points.get(gid as usize)?;
        let outline = &pts[..pts.len() - 4];
        match &self.shapes[gid as usize] {
            Shape::Empty => {}
            Shape::Simple { on_curve, ends } => {
                // A top-level instanced glyph is drawn shifted by its
                // recomputed sidebearing (`lsb - xMin`); the shift is whole
                // units, but adding it to fractional coordinates rounds.
                let offset = if transform.is_none() && instanced && !outline.is_empty() {
                    let x_min = outline.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min);
                    let x_min = ot_round(x_min);
                    ot_round(x_min - pts[pts.len() - 4][0]) - x_min
                } else {
                    0.0
                };
                let placed: Vec<(f64, f64)> = outline
                    .iter()
                    .map(|p| {
                        let x = if offset != 0.0 { p[0] + offset } else { p[0] };
                        match transform {
                            Some(t) => apply_affine(t, x, p[1]),
                            None => (x, p[1]),
                        }
                    })
                    .collect();
                let mut start = 0;
                for &end in ends {
                    pen.contour(&placed[start..=end], &on_curve[start..=end]);
                    start = end + 1;
                }
            }
            Shape::Composite(components) => {
                for (comp, offset) in components.iter().zip(outline) {
                    if !comp.has_offset {
                        return None;
                    }
                    let s = &comp.scale;
                    let own: Affine = [s[0], s[1], s[2], s[3], offset[0], offset[1]];
                    let placed = match transform {
                        Some(outer) => compose(outer, &own),
                        None => own,
                    };
                    self.draw(points, instanced, comp.gid, Some(&placed), pen, depth + 1)?;
                }
            }
        }
        Some(())
    }

    /// `glyphSet[name].width` at a normalized location: hmtx plus the HVAR
    /// delta, or — without HVAR — the rounded phantom-point distance.
    fn advances_at(&self, coords: &[f64], points: &[Vec<[f64; 2]>]) -> Result<Vec<f64>, JsError> {
        if coords.iter().all(|&c| c == 0.0) {
            return Ok(self.advances.clone());
        }
        let Some(hvar) = &self.hvar else {
            return Ok(points
                .iter()
                .map(|pts| ot_round(pts[pts.len() - 3][0] - pts[pts.len() - 4][0]))
                .collect());
        };
        let store = hvar
            .item_variation_store()
            .map_err(|e| err(format!("HVAR store: {e}")))?;
        let regions = store
            .variation_region_list()
            .map_err(|e| err(format!("HVAR regions: {e}")))?;
        let scalars: Vec<f64> = regions
            .variation_regions()
            .iter()
            .map(|region| {
                let region = region.map_err(|e| err(format!("HVAR region: {e}")))?;
                let support: Support = region
                    .region_axes()
                    .iter()
                    .enumerate()
                    .filter(|(_, r)| r.peak_coord() != F2Dot14::ZERO)
                    .map(|(i, r)| {
                        (i, r.start_coord().to_f64(), r.peak_coord().to_f64(), r.end_coord().to_f64())
                    })
                    .collect();
                Ok(support_scalar(coords, &support))
            })
            .collect::<Result<_, JsError>>()?;
        let mapping = hvar
            .advance_width_mapping()
            .transpose()
            .map_err(|e| err(format!("HVAR advance mapping: {e}")))?;
        let all_data = store.item_variation_data();
        let mut out = Vec::with_capacity(self.advances.len());
        for gid in 0..self.view.num_glyphs {
            let (outer, inner) = match &mapping {
                Some(map) => {
                    let index = map
                        .get(gid as u32)
                        .map_err(|e| err(format!("HVAR advance mapping: {e}")))?;
                    (index.outer, index.inner)
                }
                None => (0, gid),
            };
            // VarStoreInstancer: an index outside the store adds nothing.
            let mut delta = 0.0;
            if let Some(data) = all_data.get(outer as usize) {
                let data = data.map_err(|e| err(format!("HVAR data {outer}: {e}")))?;
                if inner < data.item_count() {
                    for (d, region) in data.delta_set(inner).zip(data.region_indexes()) {
                        let scalar = scalars.get(region.get() as usize).copied().unwrap_or(0.0);
                        if scalar != 0.0 {
                            delta += d as f64 * scalar;
                        }
                    }
                }
            }
            out.push(self.advances[gid as usize] + delta);
        }
        Ok(out)
    }

    fn dense(&self, corner: &Corner) -> Vec<f64> {
        let mut coords = vec![0.0; self.axes.tags.len()];
        for &(axis, v) in corner {
            coords[axis] = v;
        }
        coords
    }
}

/// fontTools `otRound`.
fn ot_round(v: f64) -> f64 {
    (v + 0.5).floor()
}

/// A gvar tuple's `axes` as fontTools decompiles them: every axis whose
/// (start, peak, end) is not all zero, the region inferred from the peak
/// when the tuple carries no intermediate one.
fn tuple_support(tuple: &TupleVariation<GlyphDelta>, axis_count: usize) -> Support {
    let peak = tuple.peak();
    let bounds = tuple.intermediate_start().zip(tuple.intermediate_end());
    (0..axis_count)
        .filter_map(|i| {
            let p = peak.get(i).map(|v| v.to_f64()).unwrap_or(0.0);
            let (s, e) = match &bounds {
                Some((start, end)) => (
                    start.get(i).map(|v| v.to_f64()).unwrap_or(0.0),
                    end.get(i).map(|v| v.to_f64()).unwrap_or(0.0),
                ),
                None => (p.min(0.0), p.max(0.0)),
            };
            ((s, p, e) != (0.0, 0.0, 0.0)).then_some((i, s, p, e))
        })
        .collect()
}

/// What the width-aware injector measured and decided, before any tuple
/// is written.
struct Plan {
    corners: Vec<Corner>,
    /// (gid, default ink) for every glyph with ink, `.notdef` excluded.
    inks: Vec<(u16, f64)>,
    mean_ink: f64,
    /// Per corner, by gid: ink width (0.0 = none), and advance.
    corner_inks: Vec<Vec<f64>>,
    corner_means: Vec<f64>,
    corner_advances: Vec<Vec<f64>>,
    /// `None`: no parametric variation to scale against — one constant
    /// amount per glyph, the pre-E2i-c rule (as the reference falls back).
    model: Option<Model>,
}

impl Plan {
    fn new(
        wa: &WidthAware,
        master_locations: Option<&[Option<UserLocation>]>,
    ) -> Result<Self, JsError> {
        // Two passes, like the reference: measure every glyph first so the
        // amounts normalize against the font's OWN mean ink width.
        // `.notdef` is the compiler's own box: left alone, and out of the
        // mean.
        let default_inks: Vec<f64> = (0..wa.view.num_glyphs)
            .map(|gid| wa.ink_width(&wa.base, false, gid).unwrap_or(0.0))
            .collect();
        let inks: Vec<(u16, f64)> = (0..wa.view.num_glyphs)
            .filter(|&gid| Some(gid) != wa.notdef)
            .map(|gid| (gid, default_inks[gid as usize]))
            .filter(|&(_, ink)| ink > 0.0)
            .collect();
        if inks.is_empty() {
            return Err(err("width-aware SPAC: no measurable glyph outlines"));
        }
        let mean_ink = inks.iter().map(|&(_, ink)| ink).sum::<f64>() / inks.len() as f64;

        // The corners are the designspace masters, handed in by the caller
        // that has the designspace; inferring them from the font is the
        // fallback.
        let corners = match master_locations.filter(|given| !given.is_empty()) {
            Some(given) => {
                let corners = wa.axes.normalize_corners(given);
                eprintln!(
                    "spac_widthaware: {} master corner(s) from the designspace",
                    corners.len()
                );
                corners
            }
            None => wa.parametric_corners()?,
        };
        let (mut corner_inks, mut corner_means, mut corner_advances) =
            (Vec::new(), Vec::new(), Vec::new());
        for corner in &corners {
            let coords = wa.dense(corner);
            let instanced = !corner.is_empty();
            let points = wa.points_at(&coords)?;
            let at: Vec<f64> = (0..wa.view.num_glyphs)
                .map(|gid| wa.ink_width(&points, instanced, gid).unwrap_or(0.0))
                .collect();
            corner_advances.push(wa.advances_at(&coords, &points)?);
            let shared: Vec<f64> = inks
                .iter()
                .map(|&(gid, _)| at[gid as usize])
                .filter(|&ink| ink > 0.0)
                .collect();
            if shared.is_empty() {
                break;
            }
            corner_means.push(shared.iter().sum::<f64>() / shared.len() as f64);
            corner_inks.push(at);
        }
        let model = if corner_inks.len() == corners.len() && !corners.is_empty() {
            Some(Model::new(&corners, &wa.axes.tags).map_err(err)?)
        } else {
            None
        };
        Ok(Self { corners, inks, mean_ink, corner_inks, corner_means, corner_advances, model })
    }

    /// `_corner_amounts`: the per-side integer at every corner for SPAC
    /// `value` (lo or hi). `ink_gid` is the glyph whose ink sets the amount
    /// (a composite's base, E2k); the advance is the glyph's own.
    fn corner_amounts(
        &self,
        value: f64,
        gid: u16,
        ink_gid: u16,
        composite: bool,
        bias: f64,
        scale: f64,
    ) -> Vec<i64> {
        (0..self.corners.len())
            .map(|i| {
                per_side(
                    value,
                    self.corner_inks[i][ink_gid as usize],
                    self.corner_means[i],
                    self.mean_ink,
                    bias,
                    scale,
                    self.corner_advances[i][gid as usize],
                    composite,
                )
            })
            .collect()
    }
}

/// Width-aware `spac_widthaware`: every glyph with measurable ink gets a
/// pair of tuples (SPAC min, SPAC max) per master support, carrying the
/// deltas that give each master corner its own per-side amount.
#[allow(clippy::too_many_arguments)]
fn widthaware_extras(
    wa: &WidthAware,
    plan: &Plan,
    spac_idx: usize,
    total_axes: usize,
    lo: i32,
    hi: i32,
    bias: f64,
    scale: f64,
) -> Result<HashMap<u16, Vec<NewTuple>>, JsError> {
    let view = wa.view;
    let ink_by_gid: HashMap<u16, f64> = plan.inks.iter().copied().collect();
    let mut extras = HashMap::new();
    for &(gid, own_ink) in &plan.inks {
        let composite = match view.glyph(gid)? {
            Some(Glyph::Composite(c)) => Some(c),
            _ => None,
        };
        // A composite with USE_MY_METRICS takes its advance/lsb from a
        // component, so it ALREADY inherits that base glyph's SPAC
        // delta — injecting here would double it.
        if composite
            .as_ref()
            .is_some_and(|c| view.has_use_my_metrics(c))
        {
            continue;
        }
        // E2k (the user's rule): a composite tracks exactly like its base
        // letter — the base's amount on both sides. The base is the first
        // component (the flagged one in every composite the compilers write).
        let base_gid = composite
            .as_ref()
            .and_then(|c| c.components().next())
            .map(|comp| comp.glyph.to_u32() as u16);
        let ink = match (&composite, base_gid) {
            (Some(_), base) => base.and_then(|b| ink_by_gid.get(&b).copied()).unwrap_or(0.0),
            (None, _) => own_ink,
        };
        if ink <= 0.0 {
            continue;
        }
        let n = view.gvar_point_count(gid)?;
        // Both phantoms, simple or composite: ±dx gives left +dx and
        // advance +2dx, i.e. each side +dx — the base's amount for a
        // composite, its own for a simple glyph (E2k). Only the two
        // phantoms are listed; every other point is untouched.
        let spac_tuple = |mut tuple: NewTuple, norm: f64, delta: i64| -> Result<NewTuple, JsError> {
            let delta = i16::try_from(delta)
                .map_err(|_| err(format!("glyph {gid}: SPAC delta {delta} exceeds gvar's range")))?;
            tuple.peak[spac_idx] = norm;
            tuple.start[spac_idx] = norm.min(0.0);
            tuple.end[spac_idx] = norm.max(0.0);
            tuple.deltas = vec![(0, 0); n];
            tuple.deltas[n - 4] = (-delta, 0);
            tuple.deltas[n - 3] = (delta, 0);
            tuple.points = Some(vec![(n - 4) as u16, (n - 3) as u16]);
            Ok(tuple)
        };
        let blank = || NewTuple {
            peak: vec![0.0; total_axes],
            start: vec![0.0; total_axes],
            end: vec![0.0; total_axes],
            deltas: Vec::new(),
            points: None,
        };
        let mut tuples = Vec::new();
        let Some(model) = &plan.model else {
            let factor = (ink / plan.mean_ink).powf(bias); // average-width glyph → 1.0 at any bias
            for (norm, amount) in [(-1.0, lo as f64), (1.0, hi as f64)] {
                let delta = (amount * factor * scale).round_ties_even() as i64;
                tuples.push(spac_tuple(blank(), norm, delta)?);
            }
            extras.insert(gid, tuples);
            continue;
        };
        // A composite reads its BASE's ink at every corner, not its own.
        let ink_gid = base_gid.unwrap_or(gid);
        let amounts_lo =
            plan.corner_amounts(lo as f64, gid, ink_gid, composite.is_some(), bias, scale);
        let amounts_hi =
            plan.corner_amounts(hi as f64, gid, ink_gid, composite.is_some(), bias, scale);
        for (i, &dx) in amounts_lo.iter().enumerate() {
            let raw = if plan.corner_means[i] > 0.0 && plan.mean_ink > 0.0 {
                lo as f64
                    * (plan.corner_inks[i][ink_gid as usize] / plan.corner_means[i]).powf(bias)
                    * (plan.corner_means[i] / plan.mean_ink)
                    * scale
            } else {
                0.0
            };
            if dx != raw.round_ties_even() as i64 {
                let mut at: Vec<(String, f64)> = plan.corners[i]
                    .iter()
                    .map(|&(axis, v)| (wa.axes.tags[axis].to_string(), v))
                    .collect();
                at.sort_by(|a, b| a.0.cmp(&b.0));
                eprintln!(
                    "spac_widthaware floor: {} at corner {at:?} rule {raw:.1} -> {dx}",
                    wa.name(gid)
                );
            }
        }
        let deltas_lo = model.deltas(&amounts_lo);
        let deltas_hi = model.deltas(&amounts_hi);
        for (k, support) in model.supports.iter().enumerate() {
            for (norm, delta) in [(-1.0, deltas_lo[k]), (1.0, deltas_hi[k])] {
                let mut tuple = blank();
                for &(axis, lower, peak, upper) in support {
                    tuple.peak[axis] = peak;
                    tuple.start[axis] = lower;
                    tuple.end[axis] = upper;
                }
                tuples.push(spac_tuple(tuple, norm, delta)?);
            }
        }
        extras.insert(gid, tuples);
    }
    Ok(extras)
}

// --------------------------------------------------------------------------
// HVAR rebuild (fontTools varLib.hvar.add_HVAR)
// --------------------------------------------------------------------------

/// fontTools `varLib.hvar.add_HVAR` as both reference transforms use
/// it: drop the old HVAR and rebuild from the gvar phantom deltas —
/// per glyph, one (region, advance delta) pair per gvar tuple, advance
/// delta = Δx(pp2) − Δx(pp1) over the last-4 phantom slots — as an
/// indirect ItemVariationStore + AdvWidthMap. (fontTools' direct-store
/// path never triggers here: its singleModel check compares per-glyph
/// list identities, which are always distinct.)
pub(crate) fn rebuild_hvar(
    font: &FontRef,
    extras: &HashMap<u16, Vec<NewTuple>>,
    total_axes: u16,
) -> Result<Vec<u8>, JsError> {
    let view = SpacFont::new(font)?;
    let mut builder = VariationStoreBuilder::new(total_axes);
    let mut temp_ids = Vec::with_capacity(view.num_glyphs as usize);
    for gid in 0..view.num_glyphs {
        let n = view.gvar_point_count(gid)?;
        let mut pairs: Vec<(VariationRegion, i32)> = Vec::new();
        if let Some(data) = view
            .gvar
            .glyph_variation_data(GlyphId::new(gid as u32))
            .map_err(|e| err(format!("glyph {gid} gvar: {e}")))?
        {
            for tuple in data.tuples() {
                pairs.push((tuple_region(&tuple, total_axes), advance_delta(&tuple, n)));
            }
        }
        if let Some(tuples) = extras.get(&gid) {
            for tuple in tuples {
                let delta = tuple.deltas[n - 3].0 as i32 - tuple.deltas[n - 4].0 as i32;
                pairs.push((new_tuple_region(tuple), delta));
            }
        }
        temp_ids.push(builder.add_deltas(pairs));
    }
    let (store, varidx_map) = builder.build();
    let adv_map: DeltaSetIndexMap = temp_ids
        .iter()
        .map(|id| {
            varidx_map
                .get(*id)
                .unwrap_or_else(|| panic!("missing remap for {id:?}"))
        })
        .collect();
    let hvar = w_hvar::Hvar::new(store, Some(adv_map), None, None);
    dump_replacement(&hvar, "HVAR")
}

/// Δx(pp2) − Δx(pp1) for a tuple: the x deltas of the first two of the
/// four phantom slots (indices n−4, n−3); points the tuple doesn't
/// cover contribute 0 (fontTools `_get_advance_metrics`: None → 0).
fn advance_delta(tuple: &TupleVariation<GlyphDelta>, n: usize) -> i32 {
    let (mut pp1, mut pp2) = (0, 0);
    for delta in tuple.deltas() {
        if delta.position as usize == n - 4 {
            pp1 = delta.x_delta;
        } else if delta.position as usize == n - 3 {
            pp2 = delta.x_delta;
        }
    }
    pp2 - pp1
}

/// A tuple's support as a VarRegion over the full axis list: only axes
/// with a non-zero peak participate (fontTools `TupleVariation.axes`),
/// the rest get the (0, 0, 0) no-constraint tent
/// (`buildVarRegionList`'s `support.get(tag, (0, 0, 0))`).
fn tuple_region(tuple: &TupleVariation<GlyphDelta>, total_axes: u16) -> VariationRegion {
    let zero = F2Dot14::from_f32(0.0);
    let peak = tuple.peak();
    let start = tuple.intermediate_start();
    let end = tuple.intermediate_end();
    let region_axes = (0..total_axes as usize)
        .map(|i| {
            let p = peak.get(i).unwrap_or(zero);
            if p == zero {
                return RegionAxisCoordinates::new(zero, zero, zero);
            }
            let (mut s, mut e) = if p < zero { (p, zero) } else { (zero, p) };
            if let (Some(start), Some(end)) = (&start, &end) {
                s = start.get(i).unwrap_or(zero);
                e = end.get(i).unwrap_or(zero);
            }
            RegionAxisCoordinates::new(s, p, e)
        })
        .collect();
    VariationRegion { region_axes }
}

/// The same region for an injected tuple (f64 peak/start/end per axis,
/// already padded to the grown axis list).
fn new_tuple_region(tuple: &NewTuple) -> VariationRegion {
    let zero = F2Dot14::from_f32(0.0);
    let f2 = |v: f64| F2Dot14::from_bits(fl2fi(v) as i16);
    let region_axes = (0..tuple.peak.len())
        .map(|i| {
            let p = f2(tuple.peak[i]);
            if p == zero {
                return RegionAxisCoordinates::new(zero, zero, zero);
            }
            let (mut s, mut e) = if p < zero { (p, zero) } else { (zero, p) };
            // An explicit intermediate region, when the tuple carries one.
            if (tuple.start[i], tuple.end[i]) != (tuple.peak[i].min(0.0), tuple.peak[i].max(0.0)) {
                s = f2(tuple.start[i]);
                e = f2(tuple.end[i]);
            }
            RegionAxisCoordinates::new(s, p, e)
        })
        .collect();
    VariationRegion { region_axes }
}

// --------------------------------------------------------------------------
// Per-instance SPAC overrides (builtin_spac._instance_spac_overrides)
// --------------------------------------------------------------------------

/// `_instance_spac_overrides`: per-instance SPAC coordinates, blank cells
/// falling back to the axis default. The mappings CSV is parsed by the
/// CALLER (csv_io on the desktop, mappings-csv.js in the browser) and the
/// values ride the transforms JSON as `params.instance_spac` — Rust never
/// opens the CSV, so there is one parse rule, not two.
fn instance_spac_params(params: &HashMap<String, serde_json::Value>) -> HashMap<String, f64> {
    params
        .get("instance_spac")
        .and_then(|v| v.as_object())
        .map(|o| {
            o.iter()
                .filter_map(|(k, v)| json_f64(v).map(|f| (k.clone(), f)))
                .collect()
        })
        .unwrap_or_default()
}

// --------------------------------------------------------------------------
// apply_transforms
// --------------------------------------------------------------------------

pub(crate) fn apply_transforms(
    font_bytes: Vec<u8>,
    transforms_json: &str,
) -> Result<Vec<u8>, JsError> {
    let entries: Vec<TransformEntry> = serde_json::from_str(transforms_json)
        .map_err(|e| err(format!("bad transforms JSON: {e}")))?;
    let mut font_bytes = font_bytes;

    // SPAC injectors: at most one (the registry's one-injector-per-axis
    // rule — two SPAC injectors would produce two SPAC axes).
    let mut spac_entries = entries
        .iter()
        .enumerate()
        .filter(|(_, e)| e.enabled && matches!(e.kind.as_str(), "spac" | "spac_widthaware"));
    if let Some((index, entry)) = spac_entries.next() {
        if spac_entries.next().is_some() {
            return Err(err(
                "only one SPAC transform can be enabled at a time ('spac' and 'spac_widthaware' both add the SPAC axis)",
            ));
        }
        let master_locations = entry_master_locations(transforms_json, index)?;
        font_bytes = apply_spac(font_bytes, entry, master_locations.as_deref())?;
    }

    // Table fixers, in semantic order (fvar instances before STAT).
    for e in &entries {
        if !e.enabled {
            continue;
        }
        font_bytes = match e.kind.as_str() {
            "fix_instances" => crate::fix::fix_fvar_instances(font_bytes)?,
            "gen_stat" => crate::stat::regen_stat(font_bytes)?,
            "fix_unhinted" => crate::fix::fix_unhinted(font_bytes)?,
            _ => font_bytes,
        };
    }
    // Per-style axis stamps LAST: fix_instances regenerates the
    // instance list, so the ROND/slnt stamps (grade-style default +
    // per-style percents) must land on whatever instances survive the
    // fixers.
    for e in &entries {
        if !e.enabled || !matches!(e.kind.as_str(), "round_corners" | "slant") {
            continue;
        }
        let default_pct = float_param(&e.params, "default_pct", 0.0, (0.0, 100.0));
        let style_pcts: HashMap<String, f64> = e
            .params
            .get("style_pcts")
            .and_then(|v| v.as_object())
            .map(|o| {
                o.iter()
                    .filter_map(|(k, v)| json_f64(v).map(|f| (k.clone(), f)))
                    .collect()
            })
            .unwrap_or_default();
        font_bytes = match e.kind.as_str() {
            "round_corners" => crate::fix::stamp_rond_instances(font_bytes, default_pct, &style_pcts)?,
            "slant" => crate::fix::stamp_slnt_instances(font_bytes, default_pct, &style_pcts)?,
            _ => font_bytes,
        };
    }
    Ok(font_bytes)
}

/// The `index`th entry's `params.master_locations`, key order kept (see
/// `UserLocation`); `None` when the caller sent none.
fn entry_master_locations(
    transforms_json: &str,
    index: usize,
) -> Result<Option<Vec<Option<UserLocation>>>, JsError> {
    let mut entries: Vec<MasterLocationsEntry> = serde_json::from_str(transforms_json)
        .map_err(|e| err(format!("bad master_locations in the transforms JSON: {e}")))?;
    Ok(entries.swap_remove(index).params.master_locations)
}

/// What the width-aware injector measures on `font_bytes` for the enabled
/// `spac_widthaware` entry, as JSON — corners, default inks and their
/// mean, per-corner inks, means and advances, the model's supports — so
/// the oracle (spike/compare_spac_masters.py) can hold each against the
/// reference's own value, to the last bit, before any rounding hides a
/// difference. Glyphs are listed by glyph id.
pub(crate) fn widthaware_trace(font_bytes: &[u8], transforms_json: &str) -> Result<String, JsError> {
    let entries: Vec<TransformEntry> = serde_json::from_str(transforms_json)
        .map_err(|e| err(format!("bad transforms JSON: {e}")))?;
    let index = entries
        .iter()
        .position(|e| e.enabled && e.kind == "spac_widthaware")
        .ok_or_else(|| err("no enabled spac_widthaware entry"))?;
    let master_locations = entry_master_locations(transforms_json, index)?;
    let font = FontRef::new(font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let view = SpacFont::new(&font)?;
    let wa = WidthAware::new(&font, &view)?;
    let plan = Plan::new(&wa, master_locations.as_deref())?;
    let tag = |axis: usize| wa.axes.tags[axis].to_string();
    let corners: Vec<Vec<(String, f64)>> = plan
        .corners
        .iter()
        .map(|c| c.iter().map(|&(axis, v)| (tag(axis), v)).collect())
        .collect();
    let supports: Option<Vec<Vec<(String, f64, f64, f64)>>> = plan.model.as_ref().map(|m| {
        m.supports
            .iter()
            .map(|s| s.iter().map(|&(axis, lo, peak, hi)| (tag(axis), lo, peak, hi)).collect())
            .collect()
    });
    Ok(serde_json::json!({
        "corners": corners,
        "inks": plan.inks,
        "mean_ink": plan.mean_ink,
        "corner_inks": plan.corner_inks,
        "corner_means": plan.corner_means,
        "corner_advances": plan.corner_advances,
        "supports": supports,
    })
    .to_string())
}

fn apply_spac(
    font_bytes: Vec<u8>,
    entry: &TransformEntry,
    master_locations: Option<&[Option<UserLocation>]>,
) -> Result<Vec<u8>, JsError> {
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let fvar = font
        .fvar()
        .map_err(|e| err(format!("missing/invalid fvar: {e}")))?;
    let fvar_axes = fvar
        .axis_instance_arrays()
        .map_err(|e| err(format!("missing/invalid fvar: {e}")))?;
    let old_axis_count = fvar_axes.axes().len();
    if fvar_axes.axes().iter().any(|a| a.axis_tag() == SPAC_TAG) {
        return Err(err("font already has a SPAC axis"));
    }
    let total_axes = old_axis_count + 1;
    let spac_idx = old_axis_count;

    // Width-aware defaults to a symmetric tightening range (-40..40); the
    // uniform gftools port keeps gen_spac's -20 floor.
    let default_lo = if entry.kind == "spac_widthaware" { -40 } else { -20 };
    let lo = int_param(&entry.params, "min", default_lo)?;
    let hi = int_param(&entry.params, "max", 40)?;
    if lo >= hi {
        return Err(err(format!("SPAC min ({lo}) must be less than max ({hi})")));
    }

    let view = SpacFont::new(&font)?;
    let extras = match entry.kind.as_str() {
        "spac" => uniform_extras(&view, spac_idx, total_axes, lo, hi)?,
        _ => {
            let bias = float_param(&entry.params, "bias", 1.0, (1.0, 4.0));
            let scale = float_param(&entry.params, "scale", 1.25, (0.1, 10.0));
            let wa = WidthAware::new(&font, &view)?;
            let plan = Plan::new(&wa, master_locations)?;
            widthaware_extras(&wa, &plan, spac_idx, total_axes, lo, hi, bias, scale)?
        }
    };

    // Per-instance SPAC coordinates: parsed from the mappings CSV by the
    // caller and carried in the entry's `params.instance_spac`.
    let overrides: HashMap<String, Vec<f64>> = instance_spac_params(&entry.params)
        .into_iter()
        .map(|(name, v)| (name, vec![v]))
        .collect();
    let hvar_bytes = rebuild_hvar(&font, &extras, total_axes as u16)?;

    let new_axes = vec![NewFvarAxis {
        tag: SPAC_TAG,
        name: SPAC_NAME.to_string(),
        min: lo as f64,
        default: 0.0,
        max: hi as f64,
    }];
    let options = GrowOptions {
        instance_overrides: Some(&overrides),
        hvar_bytes: Some(hvar_bytes),
        // Every glyph carries the same per-master peaks: stored once.
        share_peaks: entry.kind == "spac_widthaware",
    };
    build_grown_font(&font_bytes, &new_axes, extras, &options)
}

// --------------------------------------------------------------------------
// Unit tests: the routines where a port can drift from the reference
// without any fixture noticing. Every expected value was produced by the
// reference itself (Python 3.11, fontTools 4.68 — `round`, `supportScalar`,
// `VariationModel`, `calcQuadraticBounds`, `BoundsPen` — and
// avar2_studio's `_avar_map` and `spac_amounts.per_side`); the oracle
// tests (tests/spac_oracle.rs, tests/spac_internals_oracle.rs) run the
// same comparisons live on whole fonts.
// --------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round6_is_pythons_round() {
        // Exact binary ties (k/128) go to the even digit; the rest to the
        // nearest decimal of the double's exact value.
        for (value, expected) in [
            (0.0078125, 0.007812),
            (0.0234375, 0.023438),
            (0.0390625, 0.039062),
            (-0.0078125, -0.007812),
            (0.1666665, 0.166666),
            (0.16666650000000002, 0.166667),
            (0.6666666666666666, 0.666667),
            (5e-07, 0.0),
            (0.9999995, 1.0),
            (0.25, 0.25),
            (-0.3333335, -0.333334),
            (1e-07, 0.0),
            (123.4567895, 123.456789),
        ] {
            assert_eq!(round6(value), expected, "round({value}, 6)");
        }
    }

    #[test]
    fn support_scalar_is_fonttools() {
        let cases: [(&[f64], &[(usize, f64, f64, f64)], f64); 11] = [
            (&[0.5], &[(0, 0.0, 1.0, 1.0)], 0.5),
            (&[0.5, 0.25], &[(0, 0.0, 1.0, 1.0), (1, 0.0, 0.5, 1.0)], 0.25),
            (&[0.75], &[(0, 0.25, 0.5, 1.0)], 0.5),
            // A region that crosses zero does not constrain the axis…
            (&[0.0], &[(0, -1.0, 0.5, 1.0)], 1.0),
            (&[0.3], &[(0, -1.0, 0.5, 1.0)], 1.0),
            // …nor does a malformed one.
            (&[0.5], &[(0, 0.6, 0.5, 1.0)], 1.0),
            (&[1.0], &[(0, 0.0, 0.5, 1.0)], 0.0),
            (&[0.0], &[(0, 0.0, 0.5, 1.0)], 0.0),
            (&[0.5, 0.0], &[(0, 0.0, 0.5, 1.0), (1, 0.0, 1.0, 1.0)], 0.0),
            (&[-0.2], &[(0, -1.0, -0.6, 0.0)], 0.33333333333333337),
            (
                &[0.1, 0.3, 0.7],
                &[(0, 0.0, 0.3, 1.0), (1, 0.0, 0.9, 1.0), (2, 0.0, 0.9, 1.0)],
                0.08641975308641975,
            ),
        ];
        for (coords, support, expected) in cases {
            assert_eq!(support_scalar(coords, support), expected, "{coords:?} in {support:?}");
        }
    }

    #[test]
    fn avar_map_is_the_references() {
        let map = [(-1.0, -1.0), (0.0, 0.0), (0.5, 0.25), (1.0, 1.0)];
        for (value, expected) in [
            (-1.5, -1.0),
            (-1.0, -1.0),
            (-0.5, -0.5),
            (0.0, 0.0),
            (0.3333333333333333, 0.16666666666666666),
            (0.5, 0.25),
            (0.75, 0.625),
            (1.0, 1.0),
            (1.2, 1.0),
        ] {
            assert_eq!(avar_map(value, &map), expected, "avar({value})");
        }
        assert_eq!(avar_map(0.4, &[]), 0.4, "an empty map is the identity");
        assert_eq!(avar_map(0.4, &[(0.0, 0.1)]), 0.1);
    }

    fn model(corners: &[&[(usize, f64)]], tags: &[&[u8; 4]]) -> Result<Model, String> {
        let corners: Vec<Corner> = corners.iter().map(|c| c.to_vec()).collect();
        let tags: Vec<Tag> = tags.iter().map(|t| Tag::new(*t)).collect();
        Model::new(&corners, &tags)
    }

    #[test]
    fn model_is_fonttools_variation_model() {
        // Two axes, a negative side, an interior master and a location
        // between masters (the oracle's synthetic fixture). XOPQ sorts
        // before XTRA, whatever the axes' order in the font.
        const X: usize = 0; // XTRA
        const O: usize = 1; // XOPQ
        let m = model(
            &[
                &[],
                &[(X, -1.0)],
                &[(X, 0.25)],
                &[(X, 1.0)],
                &[(O, 1.0)],
                &[(X, -1.0), (O, 1.0)],
                &[(X, 0.25), (O, 1.0)],
                &[(X, 1.0), (O, 1.0)],
                &[(X, 0.166667)],
            ],
            &[b"XTRA", b"XOPQ"],
        )
        .unwrap();
        let expected: Vec<Support> = vec![
            vec![],
            vec![(O, 0.0, 1.0, 1.0)],
            vec![(X, -1.0, -1.0, 0.0)],
            vec![(X, 0.0, 0.166667, 1.0)],
            vec![(X, 0.166667, 0.25, 1.0)],
            vec![(X, 0.25, 1.0, 1.0)],
            vec![(X, -1.0, -1.0, 0.0), (O, 0.0, 1.0, 1.0)],
            vec![(X, 0.0, 0.25, 1.0), (O, 0.0, 1.0, 1.0)],
            vec![(X, 0.25, 1.0, 1.0), (O, 0.0, 1.0, 1.0)],
        ];
        assert_eq!(m.supports, expected);
        assert_eq!(m.reverse_mapping, [0, 4, 1, 8, 2, 3, 5, 6, 7]);
        assert_eq!(m.delta_weights[4], [(0, 1.0), (3, 0.900000360000144)]);
        assert_eq!(
            m.delta_weights[7],
            [(0, 1.0), (1, 1.0), (3, 0.900000360000144), (4, 1.0)]
        );
        assert_eq!(
            m.deltas(&[-158, -98, -203, -305, -279, -219, -324, -409, -184]),
            [-158, -121, 60, -26, -22, -147, 0, 0, 17]
        );
        assert_eq!(
            m.deltas(&[3, 7, 11, 13, 17, 19, 23, 29, 31]),
            [3, 14, 4, 28, -17, 10, -2, -2, 2]
        );

        // The default is not first, a location lists its axes out of tag
        // order (the support keeps that order), and a box is split.
        const A: usize = 0;
        const B: usize = 1;
        let m = model(
            &[
                &[(B, 0.5), (A, 0.5)],
                &[(A, 1.0)],
                &[],
                &[(B, 1.0)],
                &[(A, 1.0), (B, 1.0)],
                &[(A, 0.5)],
            ],
            &[b"AAAA", b"BBBB"],
        )
        .unwrap();
        let expected: Vec<Support> = vec![
            vec![],
            vec![(A, 0.0, 0.5, 1.0)],
            vec![(A, 0.5, 1.0, 1.0)],
            vec![(B, 0.0, 1.0, 1.0)],
            vec![(A, 0.0, 1.0, 1.0), (B, 0.0, 1.0, 1.0)],
            vec![(B, 0.0, 0.5, 1.0), (A, 0.0, 0.5, 1.0)],
        ];
        assert_eq!(m.supports, expected);
        assert_eq!(m.reverse_mapping, [2, 5, 1, 3, 4, 0]);
        assert_eq!(m.delta_weights[5], [(0, 1.0), (1, 1.0), (3, 0.5), (4, 0.25)]);
        assert_eq!(m.deltas(&[10, 21, 5, 8, 40, 13]), [5, 8, 16, 3, 16, -8]);

        // One axis, both sides, interior masters on each.
        let m = model(
            &[&[], &[(A, 0.3)], &[(A, 0.7)], &[(A, 1.0)], &[(A, -0.5)], &[(A, -1.0)]],
            &[b"AAAA"],
        )
        .unwrap();
        let expected: Vec<Support> = vec![
            vec![],
            vec![(A, -1.0, -0.5, 0.0)],
            vec![(A, -1.0, -1.0, -0.5)],
            vec![(A, 0.0, 0.3, 1.0)],
            vec![(A, 0.3, 0.7, 1.0)],
            vec![(A, 0.7, 1.0, 1.0)],
        ];
        assert_eq!(m.supports, expected);
        assert_eq!(m.delta_weights[4], [(0, 1.0), (3, 0.42857142857142866)]);
        assert_eq!(m.deltas(&[1, 2, 4, 8, 16, 33]), [1, 15, 32, 1, 3, 7]);
    }

    #[test]
    fn model_refuses_what_fonttools_refuses() {
        let no_base = model(&[&[(0, 1.0)]], &[b"AAAA"]);
        assert_eq!(no_base.err().as_deref(), Some("Base master not found."));
        let twice = model(&[&[], &[(0, 1.0)], &[(0, 1.0)]], &[b"AAAA"]);
        assert_eq!(twice.err().as_deref(), Some("Locations must be unique."));
        // A zero coordinate is the default: two of those are duplicates too.
        let zeros = model(&[&[], &[(0, 0.0)]], &[b"AAAA"]);
        assert_eq!(zeros.err().as_deref(), Some("Locations must be unique."));
    }

    #[test]
    fn per_side_is_spac_amounts() {
        // (value, ink, mean_here, mean_default, bias, scale, advance, composite)
        for (args, expected) in [
            ((-100.0, 300.0, 250.0, 240.0, 1.0, 1.25, 600.0, false), -156),
            // The floor: a simple glyph's advance moves by 2·dx…
            ((-100.0, 300.0, 250.0, 240.0, 1.0, 1.25, 41.0, false), -20),
            ((-100.0, 300.0, 250.0, 240.0, 1.0, 1.25, 42.0, false), -20),
            // …a composite's by dx, and the advance is truncated first.
            ((-100.0, 300.0, 250.0, 240.0, 1.0, 1.25, 41.9, true), -40),
            // A zero advance: the "lowest" move is +1, and it wins.
            ((-100.0, 60.0, 250.0, 240.0, 1.0, 1.25, 0.0, false), 1),
            ((-100.0, 60.0, 250.0, 240.0, 1.0, 1.25, 0.0, true), 1),
            ((-100.0, 300.0, 250.0, 240.0, 1.0, 1.25, 2.0, false), 0),
            ((-100.0, 300.0, 250.0, 240.0, 1.0, 1.25, 1.0, false), 0),
            // The loose end is never floored, and bias is a power.
            ((100.0, 300.0, 250.0, 240.0, 1.5, 0.8, 5.0, false), 110),
            ((-100.0, 0.0, 250.0, 240.0, 1.0, 1.25, 500.0, false), 0),
            ((-100.0, 300.0, 0.0, 240.0, 1.0, 1.25, 500.0, false), 0),
            // Halves round to even: -25.0 exactly, then -22.5 → -22.
            ((-40.0, 100.0, 200.0, 200.0, 1.0, 1.25, 500.0, false), -25),
            ((-20.0, 150.0, 200.0, 200.0, 1.0, 1.5, 500.0, false), -22),
        ] {
            let (value, ink, here, default, bias, scale, advance, composite) = args;
            assert_eq!(
                per_side(value, ink, here, default, bias, scale, advance, composite),
                expected,
                "per_side{args:?}"
            );
        }
    }

    #[test]
    fn bounds_are_fonttools_bounds_pen() {
        assert_eq!(
            quadratic_bounds((10.5, 3.25), (200.75, -40.5), (60.125, 80.0)),
            [10.5, -8.403348554033485, 119.89195315451454, 80.0]
        );
        assert_eq!(
            quadratic_bounds((0.0, 0.0), (-30.0, 10.0), (20.0, 5.0)),
            [-11.25, 0.0, 20.0, 6.666666666666666]
        );
        // A contour with no on-curve point: the outline stays inside the
        // control points' box (0..300 wide).
        let mut pen = BoundsPen::default();
        pen.contour(&[(0.0, 0.0), (300.0, 0.0), (150.0, 500.0)], &[false; 3]);
        assert_eq!(pen.bounds, Some([50.0, 0.0, 250.0, 375.0]));
        // On-curve points with a run of off-curves between them.
        let mut pen = BoundsPen::default();
        pen.contour(
            &[(10.0, 10.0), (200.0, 30.0), (150.0, 200.0), (40.0, 260.0), (10.0, 100.0)],
            &[true, false, false, false, true],
        );
        assert_eq!(pen.bounds, Some([10.0, 10.0, 177.90697674418604, 234.73684210526315]));
    }
}
