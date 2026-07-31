//! Brace-layer effects on a compiled variable font: **control axes**
//! (secondary parametric axes) and the **GRAD** grade axis.
//!
//! This is the binary-level port of the studio's source-side shadow
//! pipeline (`control_axes.py` + `grade_shadow.py`, with the grade maths
//! from `grade.py`). Where the studio seeds brace layers on a shadow
//! `.glyphs` and rebuilds, this module instances the already-compiled
//! glyph at the brace's parametric location and injects the difference
//! as a gvar tuple — the same net effect without a source round-trip.
//!
//! Outline instancing is done by evaluating the font's own glyf points
//! and gvar tuples directly (a port of fontTools' `instancer`
//! semantics), NOT via skrifa's outline `draw`. A draw walk inserts
//! implied on-curve midpoints between consecutive off-curve points, so
//! the emitted points no longer align one-to-one with the glyph's gvar
//! point numbering (Crispy Mini's `e` has 6 such runs). Direct
//! evaluation keeps the gvar point order (contour points, then the 4
//! phantom points) by construction.
//!
//! Tuple placement (both features): the brace peak sits on the NEW
//! axis only (one-sided tent 0 → ±1), with no parametric coordinates —
//! the layer's parametric pins choose the *target shape* of the delta,
//! not its scope. Zero effect at the axis default (the rest of the
//! variation space is untouched there — the instancer/oracle sees the
//! original outlines), full effect at the engaged extreme: at the
//! default parametric position with the axis fully engaged the glyph
//! takes exactly the computed shape. Pinning the parametric coords in
//! the tuple would need an origin-crossing tent to stay alive at the
//! parametric default — and fontTools' instancer drops such tuples as
//! malformed (`lower < 0 and upper > 0`), so the delta is applied
//! uniformly across the parametric plane instead. (This mirrors how a
//! single studio brace at parametric-default × control-extreme behaves
//! in varLib: its delta applies everywhere the model doesn't attenuate
//! it.)

use std::collections::{HashMap, HashSet};
use std::str::FromStr;

use serde::Deserialize;
use wasm_bindgen::prelude::*;

use font_types::{Fixed, NameId, Tag};
use read_fonts::tables::glyf::{Anchor, CompositeGlyphFlags, Glyph};
use read_fonts::tables::variations::Tuple;
use read_fonts::types::{GlyphId, GlyphId16};
use read_fonts::{FontRef, TableProvider};
use write_fonts::from_obj::ToOwnedTable;
use write_fonts::tables::avar as w_avar;
use write_fonts::tables::fvar as w_fvar;
use write_fonts::tables::name as w_name;

use crate::{
    dump_replacement, err, normalize, pad_var_store, patch_gvar, NewTuple, TAG_FVAR, TAG_GVAR,
    TAG_NAME,
};

const TAG_GRAD: &str = "GRAD";
const GRAD_NAME: &str = "Grade";
// grade.py: GRAD_MIN, GRAD_DEFAULT, GRAD_MAX = -10.0, 0.0, 10.0
const GRAD_MIN: f64 = -10.0;
const GRAD_DEFAULT: f64 = 0.0;
const GRAD_MAX: f64 = 10.0;

// grade.py's pure-weight model: XOPQ (stems) + YOPQ (horizontals) drive,
// XTRA (counters) follows at COMP_RATIO × the stem move to hold width.
const K_YOPQ: f64 = 1.0;
const COMP_RATIO: f64 = 2.0;
const PARAM_TAGS: [&str; 3] = ["XTRA", "XOPQ", "YOPQ"];

// --------------------------------------------------------------------------
// Bundle JSON shapes
// --------------------------------------------------------------------------

#[derive(Deserialize)]
struct ControlAxis {
    tag: String,
    name: Option<String>,
    min: f64,
    default: f64,
    max: f64,
    #[serde(default)]
    layers: Vec<ControlLayer>,
}

#[derive(Deserialize)]
struct ControlLayer {
    glyph: String,
    #[serde(default)]
    location: HashMap<String, f64>,
}

#[derive(Deserialize)]
struct GradeDoc {
    enabled: bool,
    default_pct: Option<f64>,
    #[serde(default)]
    instances: Vec<GradeInstance>,
}

#[derive(Deserialize)]
struct GradeInstance {
    name: String,
    pct: Option<f64>,
}

// --------------------------------------------------------------------------
// Glyph instancer: fontTools-model evaluation of glyf + gvar
// --------------------------------------------------------------------------

/// Evaluates glyph point positions (contour/component points + the 4
/// phantom points) at arbitrary normalized coordinates, mirroring
/// fontTools' `_getCoordinatesAndControls` + `instantiateTupleVariationStore`.
struct GlyphInstancer<'a> {
    glyf: read_fonts::tables::glyf::Glyf<'a>,
    loca: read_fonts::tables::loca::Loca<'a>,
    gvar: Option<read_fonts::tables::gvar::Gvar<'a>>,
    hmtx: read_fonts::tables::hmtx::Hmtx<'a>,
    vmtx: Option<read_fonts::tables::vmtx::Vmtx<'a>>,
    num_glyphs: u16,
    name_to_gid: HashMap<String, u16>,
}

impl<'a> GlyphInstancer<'a> {
    fn new(font: &FontRef<'a>) -> Result<Self, JsError> {
        let glyf = font.glyf().map_err(|e| err(format!("missing glyf: {e}")))?;
        let loca = font.loca(None).map_err(|e| err(format!("missing loca: {e}")))?;
        let gvar = font.gvar().ok();
        let hmtx = font.hmtx().map_err(|e| err(format!("missing hmtx: {e}")))?;
        let vmtx = font.vmtx().ok();
        let num_glyphs = font
            .maxp()
            .map_err(|e| err(format!("missing maxp: {e}")))?
            .num_glyphs();
        let post = font.post().map_err(|e| err(format!("missing post: {e}")))?;
        let mut name_to_gid = HashMap::new();
        for gid in 0..num_glyphs {
            if let Some(name) = post.glyph_name(GlyphId16::new(gid)) {
                name_to_gid.insert(name.to_string(), gid);
            }
        }
        if name_to_gid.is_empty() {
            return Err(err("font has no glyph names (post table format 3?)"));
        }
        Ok(Self {
            glyf,
            loca,
            gvar,
            hmtx,
            vmtx,
            num_glyphs,
            name_to_gid,
        })
    }

    fn gid(&self, glyph_name: &str) -> Option<u16> {
        self.name_to_gid.get(glyph_name).copied()
    }

    /// The glyph's default-instance points: contour points for simple
    /// glyphs, component offsets for composites, plus the 4 phantom
    /// points (fontTools `_getPhantomPoints`).
    fn base_points(&self, gid: u16) -> Result<Vec<[f64; 2]>, JsError> {
        let glyph = self
            .loca
            .get_glyf(GlyphId::new(gid as u32), &self.glyf)
            .map_err(|e| err(format!("glyph {gid}: {e}")))?;
        let (mut pts, x_min, y_max) = match &glyph {
            None => (Vec::new(), 0, 0), // empty glyph: phantoms only
            Some(Glyph::Simple(simple)) => (
                simple
                    .points()
                    .map(|p| [p.x as f64, p.y as f64])
                    .collect(),
                simple.x_min(),
                simple.y_max(),
            ),
            Some(Glyph::Composite(composite)) => {
                let mut pts = Vec::new();
                for comp in composite.components() {
                    if comp
                        .flags
                        .contains(CompositeGlyphFlags::USE_MY_METRICS)
                    {
                        return Err(err(format!(
                            "glyph {gid}: composite with USE_MY_METRICS is unsupported"
                        )));
                    }
                    match comp.anchor {
                        Anchor::Offset { x, y } => pts.push([x as f64, y as f64]),
                        Anchor::Point { .. } => {
                            return Err(err(format!(
                                "glyph {gid}: point-anchored composite is unsupported"
                            )))
                        }
                    }
                }
                (pts, composite.x_min(), composite.y_max())
            }
        };
        let advance = self
            .hmtx
            .advance(GlyphId::new(gid as u32))
            .ok_or_else(|| err(format!("glyph {gid}: no hmtx entry")))? as f64;
        let lsb = self
            .hmtx
            .side_bearing(GlyphId::new(gid as u32))
            .unwrap_or(0) as f64;
        let left_side_x = x_min as f64 - lsb;
        let right_side_x = left_side_x + advance;
        let (top_side_y, bottom_side_y) = match &self.vmtx {
            Some(vmtx) => {
                let vadv = vmtx.advance(GlyphId::new(gid as u32)).unwrap_or(0) as f64;
                let tsb = vmtx.side_bearing(GlyphId::new(gid as u32)).unwrap_or(0) as f64;
                let top = tsb + y_max as f64;
                (top, top - vadv)
            }
            None => (0.0, 0.0),
        };
        pts.push([left_side_x, 0.0]);
        pts.push([right_side_x, 0.0]);
        pts.push([0.0, top_side_y]);
        pts.push([0.0, bottom_side_y]);
        Ok(pts)
    }

    /// Points at `coords` (normalized, fvar axis order): base points
    /// plus every active tuple's deltas scaled per the OT scalar rules
    /// (a f64 port of read-fonts' `compute_scalar_f32`, which matches
    /// fontTools' `supportScalar(ot=True)`).
    fn instance_points(&self, gid: u16, coords: &[f64]) -> Result<Vec<[f64; 2]>, JsError> {
        let mut pts = self.base_points(gid)?;
        let Some(gvar) = &self.gvar else { return Ok(pts) };
        let Some(data) = gvar
            .glyph_variation_data(GlyphId::new(gid as u32))
            .map_err(|e| err(format!("glyph {gid} gvar: {e}")))?
        else {
            return Ok(pts);
        };
        for tuple in data.tuples() {
            let scalar = tuple_scalar(
                &coords_f64(&tuple.peak(), coords.len()),
                tuple
                    .intermediate_start()
                    .map(|t| coords_f64(&t, coords.len())),
                tuple
                    .intermediate_end()
                    .map(|t| coords_f64(&t, coords.len())),
                coords,
            );
            if scalar == 0.0 {
                continue;
            }
            for delta in tuple.deltas() {
                let Some(p) = pts.get_mut(delta.position as usize) else {
                    continue;
                };
                p[0] += scalar * delta.x_delta as f64;
                p[1] += scalar * delta.y_delta as f64;
            }
        }
        Ok(pts)
    }

    /// Advance width at `coords`, derived from the phantom points (the
    /// same values the HVAR would produce; fontc keeps them in sync).
    fn advance_at(&self, gid: u16, coords: &[f64]) -> Result<f64, JsError> {
        let pts = self.instance_points(gid, coords)?;
        let n = pts.len();
        Ok(pts[n - 3][0] - pts[n - 4][0])
    }
}

/// Tuple coordinates as f64 (normalized), padded with 0.0 to `len`.
fn coords_f64(tuple: &Tuple, len: usize) -> Vec<f64> {
    (0..len)
        .map(|i| tuple.get(i).map(|v| v.to_f64()).unwrap_or(0.0))
        .collect()
}

/// OpenType tuple scalar: 0.0 when the tuple is inactive at `coords`.
/// Port of read-fonts' `TupleVariation::compute_scalar_f32` (kept in
/// f64 so results are bit-stable against fontTools' f64 maths).
fn tuple_scalar(
    peak: &[f64],
    start: Option<Vec<f64>>,
    end: Option<Vec<f64>>,
    coords: &[f64],
) -> f64 {
    let mut scalar = 1.0f64;
    for (i, &coord) in coords.iter().enumerate() {
        let peak = peak.get(i).copied().unwrap_or(0.0);
        if peak == 0.0 || peak == coord {
            continue;
        }
        if coord == 0.0 {
            return 0.0;
        }
        if let (Some(start), Some(end)) = (&start, &end) {
            let start = start.get(i).copied().unwrap_or(0.0);
            let end = end.get(i).copied().unwrap_or(0.0);
            if start > peak || peak > end || (start < 0.0 && end > 0.0) {
                // Malformed or origin-crossing region: the axis does not
                // constrain the scalar (OpenType rule).
                continue;
            }
            if coord < start || coord > end {
                return 0.0;
            }
            if coord < peak {
                if peak != start {
                    scalar *= (coord - start) / (peak - start);
                }
            } else if peak != end {
                scalar *= (end - coord) / (end - peak);
            }
        } else {
            if coord < peak.min(0.0) || coord > peak.max(0.0) {
                return 0.0;
            }
            scalar *= coord / peak;
        }
    }
    scalar
}

// --------------------------------------------------------------------------
// Shared grow-the-font plumbing (fvar + name + avar + VarStores + gvar)
// --------------------------------------------------------------------------

/// A new fvar axis to append (bundle-declared range; name goes into the
/// name table with a fresh nameID, following the add_avar2 precedent).
pub(crate) struct NewFvarAxis {
    pub tag: Tag,
    pub name: String,
    pub min: f64,
    pub default: f64,
    pub max: f64,
}

/// Existing fvar axes as (tag, min, default, max), in font order.
fn fvar_triples(font: &FontRef) -> Result<Vec<(Tag, f64, f64, f64)>, JsError> {
    Ok(font
        .fvar()
        .map_err(|e| err(format!("missing/invalid fvar: {e}")))?
        .axis_instance_arrays()
        .map_err(|e| err(format!("missing/invalid fvar: {e}")))?
        .axes()
        .iter()
        .map(|a| {
            (
                a.axis_tag(),
                a.min_value().to_f64(),
                a.default_value().to_f64(),
                a.max_value().to_f64(),
            )
        })
        .collect())
}

/// Tag for one of the parametric axes (XTRA/XOPQ/YOPQ).
fn param_tag(t: &str) -> Tag {
    Tag::from_str(t).expect("PARAM_TAGS are valid tags")
}

/// Append `new_axes` to fvar (+ name records, instance coordinate
/// padding with the axis default), grow avar/HVAR/GDEF/MVAR to match,
/// and rewrite gvar with the new axis count, injecting `extras`
/// (gid → new tuples) into the per-glyph variation data.
pub(crate) fn build_grown_font(
    font_bytes: &[u8],
    new_axes: &[NewFvarAxis],
    extras: HashMap<u16, Vec<NewTuple>>,
) -> Result<Vec<u8>, JsError> {
    let font = FontRef::new(font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;

    let mut fvar: w_fvar::Fvar = font
        .fvar()
        .map_err(|e| err(format!("missing/invalid fvar: {e}")))?
        .to_owned_table();
    let old_axis_count = fvar.axis_instance_arrays.axes.len();
    let total_axes = (old_axis_count + new_axes.len()) as u16;

    // ---- fvar + name ---------------------------------------------------
    let mut name: w_name::Name = font
        .name()
        .map_err(|e| err(format!("missing/invalid name table: {e}")))?
        .to_owned_table();
    let max_name_id = name
        .name_record
        .iter()
        .map(|r| r.name_id.to_u16())
        .max()
        .unwrap_or(0);
    let first_name_id = (max_name_id + 1).max(256);
    for (i, a) in new_axes.iter().enumerate() {
        let name_id = NameId::new(first_name_id + i as u16);
        fvar.axis_instance_arrays.axes.push(
            w_fvar::VariationAxisRecord::new(
                a.tag,
                Fixed::from_f64(a.min),
                Fixed::from_f64(a.default),
                Fixed::from_f64(a.max),
                0,
                name_id,
            ),
        );
        name.name_record.push(w_name::NameRecord::new(
            3,
            1,
            0x409,
            name_id,
            a.name.clone().into(),
        ));
    }
    for inst in fvar.axis_instance_arrays.instances.iter_mut() {
        for a in new_axes {
            inst.coordinates.push(Fixed::from_f64(a.default));
        }
    }

    // ---- avar: identity segment maps + VarStore padding -----------------
    // New axes have no mapping of their own; the avar2 store (when
    // present, i.e. after a mappings apply) needs its region list grown
    // to the new axis count just like the other VarStores.
    let avar_bytes = match font.avar() {
        Ok(avar) => {
            let mut avar: w_avar::Avar = avar.to_owned_table();
            for _ in 0..new_axes.len() {
                avar.axis_segment_maps.push(w_avar::SegmentMaps::default());
            }
            if let Some(store) = avar.var_store.as_mut() {
                pad_var_store(store, total_axes);
            }
            Some(write_fonts::dump_table(&avar).map_err(|e| err(format!("avar: {e}")))?)
        }
        Err(_) => None,
    };

    // ---- gvar + VarStore tables -----------------------------------------
    let gvar_bytes = match font.data_for_tag(TAG_GVAR) {
        Some(d) => Some(patch_gvar(d.as_bytes(), total_axes, &extras)?),
        None => {
            if !extras.is_empty() {
                return Err(err("font has no gvar table (not a variable font?)"));
            }
            None
        }
    };

    let mut replacements = crate::grown_varstore_replacements(&font, total_axes)?;
    replacements.insert(TAG_FVAR, dump_replacement(&fvar, "fvar")?);
    replacements.insert(TAG_NAME, dump_replacement(&name, "name")?);
    if let Some(b) = avar_bytes {
        replacements.insert(crate::TAG_AVAR, b);
    }
    if let Some(b) = gvar_bytes {
        replacements.insert(TAG_GVAR, b);
    }
    Ok(crate::repack(&font, replacements))
}

// --------------------------------------------------------------------------
// Brace tuple construction
// --------------------------------------------------------------------------

/// Build the coordinates for a brace tuple engaged on `control_idx` at
/// `control_norm` (±1): a peak-only tuple on the new axis. `start`/`end`
/// carry the inferred region (0 → ±1) so serialization writes no
/// intermediate region (fontTools `compileIntermediateCoord` would
/// return None — the inferred tent is exactly the one we want).
fn brace_coords(total_axes: usize, control_idx: usize, control_norm: f64) -> NewTuple {
    let mut peak = vec![0.0; total_axes];
    let mut start = vec![0.0; total_axes];
    let mut end = vec![0.0; total_axes];
    peak[control_idx] = control_norm;
    start[control_idx] = control_norm.min(0.0);
    end[control_idx] = control_norm.max(0.0);
    NewTuple {
        peak,
        start,
        end,
        deltas: Vec::new(),
    }
}

/// fontTools `otRound`.
fn ot_round(v: f64) -> f64 {
    (v + 0.5).floor()
}

fn point_deltas(at: &[[f64; 2]], base: &[[f64; 2]], shift_x: f64, hold_phantoms: bool) -> Vec<(i16, i16)> {
    let n = at.len();
    at.iter()
        .zip(base.iter())
        .enumerate()
        .map(|(i, (a, b))| {
            if hold_phantoms && i >= n - 4 {
                (0, 0)
            } else {
                (
                    ot_round(a[0] + shift_x - b[0]) as i16,
                    ot_round(a[1] - b[1]) as i16,
                )
            }
        })
        .collect()
}

// --------------------------------------------------------------------------
// apply_control_axes
// --------------------------------------------------------------------------

pub(crate) fn apply_control_axes(
    font_bytes: Vec<u8>,
    control_json: &str,
) -> Result<Vec<u8>, JsError> {
    let axes: Vec<ControlAxis> = serde_json::from_str(control_json)
        .map_err(|e| err(format!("bad control-axes JSON: {e}")))?;
    if axes.is_empty() {
        return Ok(font_bytes);
    }
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let triples = fvar_triples(&font)?;
    let existing: HashMap<Tag, usize> = triples
        .iter()
        .enumerate()
        .map(|(i, (tag, ..))| (*tag, i))
        .collect();
    let instancer = GlyphInstancer::new(&font)?;
    let old_axis_count = triples.len();
    let total_axes = old_axis_count + axes.len();
    let zeroes = vec![0.0; old_axis_count];

    let mut new_axes: Vec<NewFvarAxis> = Vec::with_capacity(axes.len());
    let mut extras: HashMap<u16, Vec<NewTuple>> = HashMap::new();
    let mut seen_tags: HashSet<Tag> = HashSet::new();
    for (new_i, axis) in axes.iter().enumerate() {
        let tag = Tag::from_str(&axis.tag)
            .map_err(|e| err(format!("bad control axis tag '{}': {e}", axis.tag)))?;
        if existing.contains_key(&tag) || !seen_tags.insert(tag) {
            return Err(err(format!("control axis '{tag}' already exists in fvar")));
        }
        if !(axis.min <= axis.default && axis.default <= axis.max) {
            return Err(err(format!(
                "control axis '{tag}': default {} outside [{}, {}]",
                axis.default, axis.min, axis.max
            )));
        }
        // The engaged extreme: the side of the default with travel (the
        // larger one for interior defaults).
        let control_idx = old_axis_count + new_i;
        let control_norm = if axis.max > axis.default
            && (axis.max - axis.default) >= (axis.default - axis.min)
        {
            1.0
        } else if axis.min < axis.default {
            -1.0
        } else {
            return Err(err(format!(
                "control axis '{tag}' has no travel around its default {}",
                axis.default
            )));
        };

        for layer in &axis.layers {
            let gid = instancer.gid(&layer.glyph).ok_or_else(|| {
                err(format!(
                    "control axis '{tag}': glyph '{}' not found in the font",
                    layer.glyph
                ))
            })?;
            // Sparse pins → full normalized location over the CURRENT
            // (pre-grow) axis set for instancing; unpinned = default.
            let mut coords = zeroes.clone();
            for (pin_tag, value) in &layer.location {
                let ptag = Tag::from_str(pin_tag)
                    .map_err(|e| err(format!("bad axis tag '{pin_tag}': {e}")))?;
                let &idx = existing.get(&ptag).ok_or_else(|| {
                    err(format!(
                        "control axis '{tag}': layer on '{}' references unknown axis '{ptag}'",
                        layer.glyph
                    ))
                })?;
                let (_, min, default, max) = triples[idx];
                coords[idx] = normalize(*value, min, default, max).clamp(-1.0, 1.0);
            }
            let at_loc = instancer.instance_points(gid, &coords)?;
            let at_default = instancer.instance_points(gid, &zeroes)?;
            let mut tuple = brace_coords(total_axes, control_idx, control_norm);
            tuple.deltas = point_deltas(&at_loc, &at_default, 0.0, false);
            extras.entry(gid).or_default().push(tuple);
        }
        new_axes.push(NewFvarAxis {
            tag,
            name: axis.name.clone().unwrap_or_else(|| axis.tag.clone()),
            min: axis.min,
            default: axis.default,
            max: axis.max,
        });
    }

    build_grown_font(&font_bytes, &new_axes, extras)
}

// --------------------------------------------------------------------------
// apply_grade
// --------------------------------------------------------------------------

/// grade.py `grade_coords`: (light, dark) parametric coords for a grade
/// at `base` (XTRA/XOPQ/YOPQ user-space values), clamped to the axis
/// ranges so a grade never asks for an out-of-range coordinate.
fn grade_coords(
    base: &HashMap<String, f64>,
    pct: f64,
    ranges: &HashMap<Tag, (f64, f64)>,
) -> (HashMap<Tag, f64>, HashMap<Tag, f64>) {
    let get = |tag: &str| base.get(tag).copied().unwrap_or(0.0);
    let (x, o, y) = (get("XTRA"), get("XOPQ"), get("YOPQ"));
    let d_o = pct * o;
    let d_y = pct * K_YOPQ * y;
    let d_x = COMP_RATIO * d_o;
    let clamp = |tag: &str, v: f64| {
        let t = Tag::from_str(tag).expect("PARAM_TAGS are valid tags");
        let (lo, hi) = ranges.get(&t).copied().unwrap_or((f64::MIN, f64::MAX));
        v.clamp(lo, hi)
    };
    let mut light = HashMap::new();
    light.insert(Tag::new(b"XTRA"), clamp("XTRA", x + d_x / 2.0));
    light.insert(Tag::new(b"XOPQ"), clamp("XOPQ", o - d_o / 2.0));
    light.insert(Tag::new(b"YOPQ"), clamp("YOPQ", y - d_y / 2.0));
    let mut dark = HashMap::new();
    dark.insert(Tag::new(b"XTRA"), clamp("XTRA", x - d_x / 2.0));
    dark.insert(Tag::new(b"XOPQ"), clamp("XOPQ", o + d_o / 2.0));
    dark.insert(Tag::new(b"YOPQ"), clamp("YOPQ", y + d_y / 2.0));
    (light, dark)
}

pub(crate) fn apply_grade(
    font_bytes: Vec<u8>,
    grade_json: &str,
    instance_coords_json: &str,
) -> Result<Vec<u8>, JsError> {
    let doc: GradeDoc =
        serde_json::from_str(grade_json).map_err(|e| err(format!("bad grade JSON: {e}")))?;
    if !doc.enabled || doc.instances.is_empty() {
        return Ok(font_bytes);
    }
    let instance_coords: HashMap<String, HashMap<String, f64>> =
        serde_json::from_str(instance_coords_json)
            .map_err(|e| err(format!("bad instance-coords JSON: {e}")))?;

    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let triples = fvar_triples(&font)?;
    let existing: HashMap<Tag, usize> = triples
        .iter()
        .enumerate()
        .map(|(i, (tag, ..))| (*tag, i))
        .collect();
    let grad_tag = param_tag(TAG_GRAD);
    if existing.contains_key(&grad_tag) {
        return Err(err("font already has a GRAD axis"));
    }
    // grade_shadow returns None (no GRAD axis) when the source isn't the
    // parametric family; mirror that as a no-op.
    if !PARAM_TAGS.iter().all(|t| existing.contains_key(&param_tag(t))) {
        return Ok(font_bytes);
    }
    let ranges: HashMap<Tag, (f64, f64)> = PARAM_TAGS
        .iter()
        .map(|t| {
            let tag = param_tag(t);
            let (_, min, _, max) = triples[existing[&tag]];
            (tag, (min, max))
        })
        .collect();
    let instancer = GlyphInstancer::new(&font)?;
    let old_axis_count = triples.len();
    let total_axes = old_axis_count + 1;
    let grad_idx = old_axis_count;
    let zeroes = vec![0.0; old_axis_count];
    let default_pct = doc.default_pct.unwrap_or(0.25);

    let norm_coords = |vals: &HashMap<Tag, f64>| -> Vec<f64> {
        let mut coords = zeroes.clone();
        for (tag, v) in vals {
            let idx = existing[tag];
            let (_, min, default, max) = triples[idx];
            coords[idx] = normalize(*v, min, default, max).clamp(-1.0, 1.0);
        }
        coords
    };

    let mut extras: HashMap<u16, Vec<NewTuple>> = HashMap::new();
    let mut applied = 0usize;
    for entry in &doc.instances {
        let Some(base) = instance_coords.get(&entry.name) else {
            continue; // grade_shadow: skip instances with no resolved coords
        };
        let pct = entry.pct.unwrap_or(default_pct);
        if pct <= 0.0 {
            continue;
        }
        let base_tags: HashMap<Tag, f64> = PARAM_TAGS
            .iter()
            .map(|t| (param_tag(t), base.get(*t).copied().unwrap_or(0.0)))
            .collect();
        let (light, dark) = grade_coords(base, pct, &ranges);
        let base_loc = norm_coords(&base_tags);
        let light_loc = norm_coords(&light);
        let dark_loc = norm_coords(&dark);

        for gid in 0..instancer.num_glyphs {
            let at_base = instancer.instance_points(gid, &base_loc)?;
            let a0 = instancer.advance_at(gid, &base_loc)?;
            for (grad_norm, gloc) in [(-1.0f64, &light_loc), (1.0, &dark_loc)] {
                let at_grade = instancer.instance_points(gid, gloc)?;
                let w = instancer.advance_at(gid, gloc)?;
                // Equalise the brace's advance to the glyph's true base
                // advance at the instance, shifting the outline
                // symmetrically — holds advance across GRAD with zero
                // phantom-point delta (grade_shadow.py).
                let shift = ((a0 - w) / 2.0).round_ties_even();
                let deltas = point_deltas(&at_grade, &at_base, shift, true);
                if deltas.iter().all(|&(dx, dy)| dx == 0 && dy == 0) {
                    continue; // nothing to say (e.g. space): advance held, shape unchanged
                }
                let mut tuple = brace_coords(total_axes, grad_idx, grad_norm);
                tuple.deltas = deltas;
                extras.entry(gid).or_default().push(tuple);
            }
        }
        applied += 1;
    }
    if applied == 0 {
        return Ok(font_bytes);
    }

    // In the source pipeline a Virtual Master pair pins the GRAD range;
    // here the fvar record itself declares it (GRADE −10/0/+10).
    let new_axes = vec![NewFvarAxis {
        tag: grad_tag,
        name: GRAD_NAME.to_string(),
        min: GRAD_MIN,
        default: GRAD_DEFAULT,
        max: GRAD_MAX,
    }];
    build_grown_font(&font_bytes, &new_axes, extras)
}
