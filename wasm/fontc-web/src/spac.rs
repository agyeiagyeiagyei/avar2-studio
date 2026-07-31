//! SPAC transform application (the bundle's `transforms` section) as
//! binary font surgery, ported from the two reference implementations:
//!
//! - **Uniform `spac`** — `gftools gen-spac`
//!   (`gftools/scripts/gen_spac.py`, mirrored by the studio's
//!   `transforms/builtin_spac.py`): every glyph that already has gvar
//!   variations tracks by the same per-side amount — two gvar tuples
//!   per glyph on the phantom points `[-4]` (origin) and `[-3]`
//!   (advance), so SPAC ±N moves each sidebearing by ±N (advance ±2N).
//! - **Width-aware `spac_widthaware`** — the studio's own
//!   `transforms/builtin_spac_widthaware.py`: the per-side amount
//!   scales with `(ink / mean_ink) ** bias`, so the font loosens by a
//!   consistent proportion of each glyph's own width (wider glyphs get
//!   more). Composites are spaced too — left phantom only, and
//!   USE_MY_METRICS composites are skipped (they already inherit their
//!   base glyph's delta).
//!
//! Both variants inject a SPAC fvar axis (range params.min..max,
//! default 0) and rebuild HVAR from the gvar phantom deltas (fontTools
//! `varLib.hvar.add_HVAR`), so the advance adjustment is
//! variation-driven: SPAC stays a live slider in the built font.
//! Per-instance SPAC coordinates come from the bundle's avar2 CSV when
//! it has a SPAC column (`builtin_spac._instance_spac_overrides`).

use std::collections::HashMap;

use serde::Deserialize;
use wasm_bindgen::prelude::*;

use font_types::{F2Dot14, Tag};
use read_fonts::tables::glyf::{Anchor, CompositeGlyphFlags, Glyph};
use read_fonts::tables::gvar::GlyphDelta;
use read_fonts::tables::variations::TupleVariation;
use read_fonts::types::GlyphId;
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
/// fallback to the default, then the ParamSpec clamp (bias [1.0, 2.5],
/// scale [0.1, 10.0]).
fn float_param(params: &HashMap<String, serde_json::Value>, key: &str, default: f64, clamp: (f64, f64)) -> f64 {
    params
        .get(key)
        .and_then(|v| v.as_f64())
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

    /// `_ink_width`: the default-master outline's control bounds width,
    /// resolved through composites (fontTools BoundsPen over the glyph
    /// set: bounds of every on/off-curve point, each component's
    /// transform applied).
    fn ink_width(&self, gid: u16) -> Result<f64, JsError> {
        let mut pts: Vec<(f64, f64)> = Vec::new();
        self.collect_points(gid, IDENTITY, &mut pts, 0)?;
        if pts.is_empty() {
            return Ok(0.0);
        }
        let (mut lo, mut hi) = (f64::MAX, f64::MIN);
        for (x, _) in &pts {
            lo = lo.min(*x);
            hi = hi.max(*x);
        }
        Ok(hi - lo)
    }

    fn collect_points(
        &self,
        gid: u16,
        t: Affine,
        out: &mut Vec<(f64, f64)>,
        depth: u32,
    ) -> Result<(), JsError> {
        if depth > 16 {
            return Err(err("composite nesting too deep (component cycle?)"));
        }
        match self.glyph(gid)? {
            None => Ok(()),
            Some(Glyph::Simple(simple)) => {
                for p in simple.points() {
                    out.push(apply_affine(&t, p.x as f64, p.y as f64));
                }
                Ok(())
            }
            Some(Glyph::Composite(composite)) => {
                for comp in composite.components() {
                    let (dx, dy) = match comp.anchor {
                        Anchor::Offset { x, y } => (x as f64, y as f64),
                        // fontTools BoundsPen applies the component's raw
                        // (dx, dy) slot even when it holds point indices.
                        Anchor::Point { base, component } => {
                            (base as f64, component as f64)
                        }
                    };
                    let tr = &comp.transform;
                    let component_t: Affine = [
                        tr.xx.to_f64(),
                        tr.xy.to_f64(),
                        tr.yx.to_f64(),
                        tr.yy.to_f64(),
                        dx,
                        dy,
                    ];
                    self.collect_points(comp.glyph.to_u32() as u16, compose(&t, &component_t), out, depth + 1)?;
                }
                Ok(())
            }
        }
    }
}

/// 2×3 affine [xx, xy, yx, yy, dx, dy]: x' = xx·x + xy·y + dx,
/// y' = yx·x + yy·y + dy (fontTools Transform order).
type Affine = [f64; 6];
const IDENTITY: Affine = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];

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
        // gen_spac: `if not hasattr(glyph, "coordinates"): continue`
        // (composites and empty glyphs have no outline coordinates) and
        // `if not glyph_variations: continue`.
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

/// Width-aware `spac_widthaware`: every glyph with measurable ink gets
/// the two tuples, its per-side amount scaled by
/// `(ink / mean_ink) ** bias * scale` (Python's round() is
/// round-ties-even).
fn widthaware_extras(
    view: &SpacFont,
    spac_idx: usize,
    total_axes: usize,
    lo: i32,
    hi: i32,
    bias: f64,
    scale: f64,
) -> Result<HashMap<u16, Vec<NewTuple>>, JsError> {
    // Two passes, like the reference: measure everything first so the
    // deltas normalize against the font's OWN mean ink width. Glyphs
    // whose outline can't be measured count as zero ink and are
    // skipped (the reference's blanket `except` in _ink_width).
    let mut inks: Vec<(u16, f64)> = Vec::new();
    for gid in 0..view.num_glyphs {
        let ink = view.ink_width(gid).unwrap_or(0.0);
        if ink > 0.0 {
            inks.push((gid, ink));
        }
    }
    if inks.is_empty() {
        return Err(err("width-aware SPAC: no measurable glyph outlines"));
    }
    let mean_ink = inks.iter().map(|&(_, ink)| ink).sum::<f64>() / inks.len() as f64;

    let mut extras = HashMap::new();
    for (gid, ink) in inks {
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
        let factor = (ink / mean_ink).powf(bias); // average-width glyph → 1.0 at any bias
        let n = view.gvar_point_count(gid)?;
        let mut tuples = Vec::with_capacity(2);
        for (norm, amount) in [(-1.0, lo as f64), (1.0, hi as f64)] {
            let mut tuple = brace_coords(total_axes, spac_idx, norm);
            tuple.deltas = vec![(0, 0); n];
            // Simple glyph: move both phantoms (±) for a symmetric grow.
            // Composite: left phantom only — moving it shifts the whole
            // component, which opens BOTH sidebearings equally, so a
            // right delta too would double the right side.
            tuple.deltas[n - 4] = ((-amount * factor * scale).round_ties_even() as i16, 0);
            if composite.is_none() {
                tuple.deltas[n - 3] = ((amount * factor * scale).round_ties_even() as i16, 0);
            }
            tuples.push(tuple);
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
fn rebuild_hvar(
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

/// `_instance_spac_overrides`: per-instance SPAC coordinates from the
/// avar2 CSV's SPAC column; blank cells fall back to the axis default.
/// Any unparseable value voids the whole column, like the reference's
/// blanket `except ValueError`.
fn instance_spac_overrides(csv: &str) -> HashMap<String, f64> {
    let mut overrides = HashMap::new();
    let csv = csv.trim_start_matches('\u{feff}');
    let mut lines = csv.lines().filter(|l| !l.trim().is_empty());
    let Some(header) = lines.next() else {
        return overrides;
    };
    let headers: Vec<&str> = header.split(',').map(str::trim).collect();
    let Some(spac_col) = headers.iter().position(|h| *h == "SPAC") else {
        return overrides;
    };
    for line in lines {
        let cells: Vec<&str> = line.split(',').collect();
        let name = cells.first().map(|s| s.trim()).unwrap_or("");
        let cell = cells.get(spac_col).map(|s| s.trim()).unwrap_or("");
        if name.is_empty() || cell.is_empty() {
            continue;
        }
        match cell.parse::<f64>() {
            Ok(v) => {
                overrides.insert(name.to_string(), v);
            }
            Err(_) => return HashMap::new(),
        }
    }
    overrides
}

// --------------------------------------------------------------------------
// apply_transforms
// --------------------------------------------------------------------------

pub(crate) fn apply_transforms(
    font_bytes: Vec<u8>,
    transforms_json: &str,
    avar2_csv: &str,
) -> Result<Vec<u8>, JsError> {
    let entries: Vec<TransformEntry> = serde_json::from_str(transforms_json)
        .map_err(|e| err(format!("bad transforms JSON: {e}")))?;
    let mut spac_entries = entries
        .iter()
        .filter(|e| e.enabled && matches!(e.kind.as_str(), "spac" | "spac_widthaware"));
    let Some(entry) = spac_entries.next() else {
        return Ok(font_bytes); // nothing enabled that this port implements
    };
    if spac_entries.next().is_some() {
        // The registry's one-injector-per-axis rule: two SPAC injectors
        // would produce a font with two SPAC axes.
        return Err(err(
            "only one SPAC transform can be enabled at a time ('spac' and 'spac_widthaware' both add the SPAC axis)",
        ));
    }

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

    let lo = int_param(&entry.params, "min", -20)?;
    let hi = int_param(&entry.params, "max", 40)?;
    if lo >= hi {
        return Err(err(format!("SPAC min ({lo}) must be less than max ({hi})")));
    }

    let view = SpacFont::new(&font)?;
    let extras = match entry.kind.as_str() {
        "spac" => uniform_extras(&view, spac_idx, total_axes, lo, hi)?,
        _ => {
            let bias = float_param(&entry.params, "bias", 1.0, (1.0, 2.5));
            let scale = float_param(&entry.params, "scale", 1.25, (0.1, 10.0));
            widthaware_extras(&view, spac_idx, total_axes, lo, hi, bias, scale)?
        }
    };

    // The reference reads the per-instance SPAC coordinates from the
    // source's sidecar CSV; here the bundle's avar2 CSV plays that role.
    let overrides: HashMap<String, Vec<f64>> = instance_spac_overrides(avar2_csv)
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
    };
    build_grown_font(&font_bytes, &new_axes, extras, &options)
}
