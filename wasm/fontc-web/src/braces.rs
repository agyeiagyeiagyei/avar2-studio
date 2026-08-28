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

use fontdrasil::coords::NormalizedLocation;
use fontdrasil::variations::VariationModel;
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
    /// CORRECTION target: parametric overrides naming the point the outline
    /// should be computed *as if at*. Empty for a plain brace layer. See
    /// `apply_control_axes` for the two different delta models.
    #[serde(default)]
    target: HashMap<String, f64>,
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

    /// The glyph's default-instance points and contour end indices:
    /// contour points for simple glyphs (with endPtsOfContours for
    /// IUP), component offsets for composites (no IUP — anchor deltas
    /// apply explicitly), plus the 4 phantom points (fontTools
    /// `_getPhantomPoints`).
    fn base_points(&self, gid: u16) -> Result<(Vec<[f64; 2]>, Vec<usize>), JsError> {
        let glyph = self
            .loca
            .get_glyf(GlyphId::new(gid as u32), &self.glyf)
            .map_err(|e| err(format!("glyph {gid}: {e}")))?;
        let (mut pts, end_pts, x_min, y_max) = match &glyph {
            None => (Vec::new(), Vec::new(), 0, 0), // empty glyph: phantoms only
            Some(Glyph::Simple(simple)) => (
                simple
                    .points()
                    .map(|p| [p.x as f64, p.y as f64])
                    .collect(),
                simple
                    .end_pts_of_contours()
                    .iter()
                    .map(|e| e.get() as usize)
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
                (pts, Vec::new(), composite.x_min(), composite.y_max())
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
        Ok((pts, end_pts))
    }

    /// Points at `coords` (normalized, fvar axis order): base points
    /// plus every active tuple's deltas scaled per the OT scalar rules
    /// (a f64 port of read-fonts' `compute_scalar_f32`, which matches
    /// fontTools' `supportScalar(ot=True)`).
    fn instance_points(&self, gid: u16, coords: &[f64]) -> Result<Vec<[f64; 2]>, JsError> {
        let (base_pts, end_pts) = self.base_points(gid)?;
        let mut pts = base_pts.clone();
        let Some(gvar) = &self.gvar else { return Ok(pts) };
        let Some(data) = gvar
            .glyph_variation_data(GlyphId::new(gid as u32))
            .map_err(|e| err(format!("glyph {gid} gvar: {e}")))?
        else {
            return Ok(pts);
        };
        for tuple in data.tuples() {
            let peak = coords_f64(&tuple.peak(), coords.len());
            let start = tuple
                .intermediate_start()
                .map(|t| coords_f64(&t, coords.len()));
            let end = tuple
                .intermediate_end()
                .map(|t| coords_f64(&t, coords.len()));
            let scalar = tuple_scalar(&peak, start, end, coords);
            if scalar == 0.0 {
                continue;
            }
            // Pack explicit deltas, then IUP-fill the untouched points
            // (the renderer's semantics — never zero them out).
            let mut packed: Vec<Option<(f64, f64)>> = vec![None; pts.len()];
            for delta in tuple.deltas() {
                let Some(slot) = packed.get_mut(delta.position as usize) else {
                    continue;
                };
                *slot = Some((delta.x_delta as f64, delta.y_delta as f64));
            }
            let filled = if end_pts.is_empty() {
                packed
                    .into_iter()
                    .map(|d| d.unwrap_or((0.0, 0.0)))
                    .collect::<Vec<_>>()
            } else {
                crate::iup::iup_delta(&packed, &base_pts, &end_pts)
            };
            for (p, (dx, dy)) in pts.iter_mut().zip(filled.into_iter()) {
                p[0] += scalar * dx;
                p[1] += scalar * dy;
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

    /// Per-source shape differences for the extrapolator: each
    /// master-level tuple's peak (normalized) and its IUP-filled
    /// per-point deltas vs the default instance. Brace/interior-region
    /// tuples (bounds strictly inside the axis box on some axis) are
    /// local edits, not trends — excluded from the basis.
    fn source_deltas(
        &self,
        gid: u16,
        axis_count: usize,
    ) -> Result<Vec<crate::extrapolate::SourceDelta>, JsError> {
        let (base_pts, end_pts) = self.base_points(gid)?;
        let Some(gvar) = &self.gvar else { return Ok(Vec::new()) };
        let Some(data) = gvar
            .glyph_variation_data(GlyphId::new(gid as u32))
            .map_err(|e| err(format!("glyph {gid} gvar: {e}")))?
        else {
            return Ok(Vec::new());
        };
        let mut out = Vec::new();
        for tuple in data.tuples() {
            let peak = coords_f64(&tuple.peak(), axis_count);
            // A brace has an intermediate region bounded INSIDE the box
            // on an axis it moves; master regions reach the axis edge.
            let is_brace = match (tuple.intermediate_start(), tuple.intermediate_end()) {
                (Some(_st), Some(en)) => (0..axis_count).any(|i| {
                    peak[i] != 0.0 && en.get(i).map(|v| v.to_f64().abs()).unwrap_or(0.0) < 0.999
                }),
                _ => false,
            };
            if is_brace {
                continue;
            }
            let mut packed: Vec<Option<(f64, f64)>> = vec![None; base_pts.len()];
            for delta in tuple.deltas() {
                if let Some(slot) = packed.get_mut(delta.position as usize) {
                    *slot = Some((delta.x_delta as f64, delta.y_delta as f64));
                }
            }
            let filled = if end_pts.is_empty() {
                packed
                    .into_iter()
                    .map(|d| d.unwrap_or((0.0, 0.0)))
                    .collect::<Vec<_>>()
            } else {
                crate::iup::iup_delta(&packed, &base_pts, &end_pts)
            };
            out.push(crate::extrapolate::SourceDelta {
                loc: peak,
                deltas: filled,
            });
        }
        Ok(out)
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

/// Options for `build_grown_font` beyond the brace-layer basics.
#[derive(Default)]
pub(crate) struct GrowOptions<'a> {
    /// Per-instance coordinates for the new axes, keyed by instance
    /// (subfamily) name; values are parallel to `new_axes`. Instances
    /// not listed get the axis default (the reference transforms'
    /// fallback).
    pub instance_overrides: Option<&'a HashMap<String, Vec<f64>>>,
    /// Replaces the padded HVAR wholesale (SPAC rebuilds HVAR from the
    /// patched gvar's phantom deltas; padding alone wouldn't carry the
    /// new advance deltas).
    pub hvar_bytes: Option<Vec<u8>>,
}

/// Append `new_axes` to fvar (+ name records, instance coordinate
/// padding with the axis default), grow avar/HVAR/GDEF/MVAR to match,
/// and rewrite gvar with the new axis count, injecting `extras`
/// (gid → new tuples) into the per-glyph variation data.
pub(crate) fn build_grown_font(
    font_bytes: &[u8],
    new_axes: &[NewFvarAxis],
    extras: HashMap<u16, Vec<NewTuple>>,
    options: &GrowOptions,
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
    // Instance subfamily names for the override lookup, resolved like
    // fontTools `name.getDebugName`: the (3,1,0x409) record for the id,
    // falling back to any record with it.
    let inst_names: HashMap<u16, String> = if options.instance_overrides.is_some() {
        debug_names(&font)
    } else {
        HashMap::new()
    };
    for inst in fvar.axis_instance_arrays.instances.iter_mut() {
        let iname = inst_names.get(&inst.subfamily_name_id.to_u16());
        for (i, a) in new_axes.iter().enumerate() {
            let v = iname
                .zip(options.instance_overrides)
                .and_then(|(n, o)| o.get(n))
                .and_then(|vals| vals.get(i))
                .copied()
                .unwrap_or(a.default);
            inst.coordinates.push(Fixed::from_f64(v));
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
    if let Some(b) = &options.hvar_bytes {
        replacements.insert(crate::TAG_HVAR, b.clone()); // rebuilt, not just padded
    }
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

/// name-id → string map in fontTools `getDebugName` order: the
/// (3,1,0x409) record wins, any other record with the id is the
/// fallback.
fn debug_names(font: &FontRef) -> HashMap<u16, String> {
    let mut names = HashMap::new();
    let mut fallback: HashMap<u16, String> = HashMap::new();
    if let Ok(name) = font.name() {
        let data = name.string_data();
        for rec in name.name_record() {
            let Ok(s) = rec.string(data) else { continue };
            let id = rec.name_id().to_u16();
            if rec.platform_id() == 3 && rec.encoding_id() == 1 && rec.language_id() == 0x409 {
                names.insert(id, s.to_string());
            }
            fallback.entry(id).or_insert_with(|| s.to_string());
        }
    }
    for (id, s) in fallback {
        names.entry(id).or_insert(s);
    }
    names
}

// --------------------------------------------------------------------------
// Brace tuple construction
// --------------------------------------------------------------------------

/// Build the coordinates for a brace tuple engaged on `control_idx` at
/// `control_norm` (±1): a peak-only tuple on the new axis. `start`/`end`
/// carry the inferred region (0 → ±1) so serialization writes no
/// intermediate region (fontTools `compileIntermediateCoord` would
/// return None — the inferred tent is exactly the one we want).
pub(crate) fn brace_coords(total_axes: usize, control_idx: usize, control_norm: f64) -> NewTuple {
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

/// A tuple peaked at an explicit normalized location, with the inferred
/// (start,end) region each axis implies. Unlike `brace_coords`, which pins
/// only the control axis, this pins every axis whose peak is non-zero — so a
/// correction stays in the region it was authored for.
pub(crate) fn peaked_tuple(peak: Vec<f64>) -> NewTuple {
    let start = peak.iter().map(|p| p.min(0.0)).collect();
    let end = peak.iter().map(|p| p.max(0.0)).collect();
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
                // The sidecar stores a full N-D point, so the location carries
                // the control axis's own value. It isn't in fvar yet (we are
                // adding it), and the engaged extreme is already implied by
                // `control_norm` — skip it rather than rejecting the layer.
                if ptag == tag {
                    continue;
                }
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

            if layer.target.is_empty() {
                // PLAIN brace layer. The static build has no drawn outlines, so
                // the demo approximation stands: engaging the control axis morphs
                // the glyph toward its own shape at the layer's parametric
                // location. Delta is measured from the default master and the
                // tuple pins only the control axis.
                let at_default = instancer.instance_points(gid, &zeroes)?;
                let mut tuple = brace_coords(total_axes, control_idx, control_norm);
                tuple.deltas = point_deltas(&at_loc, &at_default, 0.0, false);
                extras.entry(gid).or_default().push(tuple);
                continue;
            }

            // CORRECTION layer. The outline is the glyph as if it sat at
            // `target`, so the delta is measured from the layer's own location —
            // exactly the full app's model, which makes the two agree.
            let mut target_coords = coords.clone();
            for (pin_tag, value) in &layer.target {
                let ptag = Tag::from_str(pin_tag)
                    .map_err(|e| err(format!("bad axis tag '{pin_tag}': {e}")))?;
                // A target on the control axis itself is meaningless; ignore it
                // rather than failing, matching the sidecar's own rule.
                if let Some(&idx) = existing.get(&ptag) {
                    let (_, min, default, max) = triples[idx];
                    target_coords[idx] = normalize(*value, min, default, max).clamp(-1.0, 1.0);
                }
            }
            let at_target = instancer.instance_points(gid, &target_coords)?;
            let deltas = point_deltas(&at_target, &at_loc, 0.0, false);

            // Pin the correction to where it was authored: peak every parametric
            // axis at the layer's own normalized value, plus the control axis.
            let mut peak = vec![0.0; total_axes];
            peak[..old_axis_count].copy_from_slice(&coords);
            peak[control_idx] = control_norm;
            let mut tuple = peaked_tuple(peak.clone());
            tuple.deltas = deltas.clone();
            extras.entry(gid).or_default().push(tuple);

            // An axis whose normalized peak is 0 is OMITTED from a gvar tuple,
            // and an omitted axis is unrestricted — so a correction authored at
            // an axis's default would otherwise apply at every value of it.
            // gvar cannot peak at 0, so instead we cancel: a companion tuple
            // peaked at that axis's extreme carrying the negated delta. The two
            // sum to delta*(1 - |axis|), which is delta at the authored end and
            // zero at the far end — the same shape a hand-authored anchor layer
            // produces in the full app, without needing one here.
            let negated: Vec<(i16, i16)> =
                deltas.iter().map(|(x, y)| (-x, -y)).collect();
            for (idx, &(_, min, default, max)) in triples.iter().enumerate() {
                if coords[idx] != 0.0 {
                    continue; // already pinned by its own peak
                }
                for extreme in [1.0_f64, -1.0_f64] {
                    let has_travel = if extreme > 0.0 { max > default } else { min < default };
                    if !has_travel {
                        continue;
                    }
                    let mut cancel_peak = peak.clone();
                    cancel_peak[idx] = extreme;
                    let mut cancel = peaked_tuple(cancel_peak);
                    cancel.deltas = negated.clone();
                    extras.entry(gid).or_default().push(cancel);
                }
            }
        }
        new_axes.push(NewFvarAxis {
            tag,
            name: axis.name.clone().unwrap_or_else(|| axis.tag.clone()),
            min: axis.min,
            default: axis.default,
            max: axis.max,
        });
    }

    build_grown_font(&font_bytes, &new_axes, extras, &GrowOptions::default())
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
    let clamp = |tag: &str, v: f64| {
        let t = Tag::from_str(tag).expect("PARAM_TAGS are valid tags");
        let (lo, hi) = ranges.get(&t).copied().unwrap_or((f64::MIN, f64::MAX));
        v.clamp(lo, hi)
    };
    // The follower (XTRA) tracks the ACHIEVED stem move per side, not
    // the requested one. When the driver clamps at the box edge (an
    // instance already at XOPQ max grades darker by nothing), the
    // counters must not move either — otherwise the "dark" brace is a
    // pure condense inside a held advance and the grade reads as
    // deformed spacing instead of weight. Away from the edges the
    // achieved move IS the requested move, so values are unchanged.
    let dark_o = clamp("XOPQ", o + d_o / 2.0);
    let light_o = clamp("XOPQ", o - d_o / 2.0);
    let mut light = HashMap::new();
    light.insert(Tag::new(b"XTRA"), clamp("XTRA", x + COMP_RATIO * (o - light_o)));
    light.insert(Tag::new(b"XOPQ"), light_o);
    light.insert(Tag::new(b"YOPQ"), clamp("YOPQ", y - d_y / 2.0));
    let mut dark = HashMap::new();
    dark.insert(Tag::new(b"XTRA"), clamp("XTRA", x - COMP_RATIO * (dark_o - o)));
    dark.insert(Tag::new(b"XOPQ"), dark_o);
    dark.insert(Tag::new(b"YOPQ"), clamp("YOPQ", y + d_y / 2.0));
    (light, dark)
}

/// Scoped grade tuple: peak at the instance's normalized parametric
/// location × GRAD ±1, with an intermediate region spanning each
/// participating parametric axis (0 → peak → axis end) — so the grade
/// applies fully AT its instance and fades to nothing at the origin
/// and the far corners, mirroring grade_shadow's brace placement.
///
/// The earlier port used a GRAD-only tent, which applied every graded
/// instance's ABSOLUTE deltas at every parametric location (and summed
/// them across instances): at locations lighter than the instance the
/// light brace drove stem widths negative, inverting contours — glyphs
/// rendered as filled blobs at GRAD −10.
fn grade_tuple_coords(
    total_axes: usize,
    base_loc: &[f64],
    grad_idx: usize,
    grad_norm: f64,
) -> NewTuple {
    let mut peak = vec![0.0; total_axes];
    let mut start = vec![0.0; total_axes];
    let mut end = vec![0.0; total_axes];
    for (i, &p) in base_loc.iter().enumerate() {
        if p == 0.0 {
            continue; // axis at its default: non-participating
        }
        peak[i] = p;
        if p > 0.0 {
            end[i] = 1.0; // start stays 0
        } else {
            start[i] = -1.0; // end stays 0
        }
    }
    peak[grad_idx] = grad_norm;
    start[grad_idx] = grad_norm.min(0.0);
    end[grad_idx] = grad_norm.max(0.0);
    NewTuple {
        peak,
        start,
        end,
        deltas: Vec::new(),
    }
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
                let mut tuple =
                    grade_tuple_coords(total_axes, &base_loc, grad_idx, grad_norm);
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
    build_grown_font(&font_bytes, &new_axes, extras, &GrowOptions::default())
}

// --------------------------------------------------------------------------
// pin_corner: hold a ghost corner up with a model-computed tuple
// --------------------------------------------------------------------------

#[derive(Deserialize)]
pub(crate) struct PinRequest {
    corner: HashMap<String, f64>,
    /// Sweep-scaffolded location; null = the sweep collapsed to the
    /// default, so synthesize the corner shape by extrapolating the
    /// model's master trends (the "instance at the extreme" workflow).
    scaffold: Option<HashMap<String, f64>>,
}

/// Hold an uncovered corner up with the scaffold's shape, the way
/// adding a master there would. Per glyph: rebuild the variation model
/// over (origin + the glyph's tuple peaks + the corner), decompose the
/// corner's indicator into proper model regions, and inject tuples
/// carrying (scaffold − default) × the region's weight. The regions
/// are what make this a master add and not a bleed: at OTHER sources'
/// corners the pin's scalar is 0 (their shapes are untouched), at the
/// pinned corner it is 1 (exactly the scaffold shape).
pub(crate) fn pin_corner(
    font_bytes: Vec<u8>,
    request_json: &str,
) -> Result<Vec<u8>, JsError> {
    let request: PinRequest =
        serde_json::from_str(request_json).map_err(|e| err(format!("bad pin JSON: {e}")))?;
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let triples = fvar_triples(&font)?;
    let norm_loc = |loc: &HashMap<String, f64>| -> Vec<f64> {
        triples
            .iter()
            .map(|(tag, min, default, max)| {
                let v = loc.get(&tag.to_string()).copied().unwrap_or(*default);
                normalize(v, *min, *default, *max).clamp(-1.0, 1.0)
            })
            .collect()
    };
    let corner_norm = norm_loc(&request.corner);
    let scaffold_norm = request.scaffold.as_ref().map(|s| norm_loc(s));
    let axis_order: Vec<Tag> = triples.iter().map(|t| t.0).collect();
    let instancer = GlyphInstancer::new(&font)?;
    let zeroes = vec![0.0; triples.len()];
    let corner_pos: Vec<(String, f64)> = axis_order
        .iter()
        .map(|t| t.to_string())
        .zip(corner_norm.iter().copied())
        .collect();
    let corner_pos_refs: Vec<(&str, f64)> =
        corner_pos.iter().map(|(s, v)| (s.as_str(), *v)).collect();
    let corner_loc = NormalizedLocation::for_pos(&corner_pos_refs);

    let mut extras: HashMap<u16, Vec<NewTuple>> = HashMap::new();
    for gid in 0..instancer.num_glyphs {
        // This glyph's source locations: origin + its tuple peaks.
        let mut locations: HashSet<NormalizedLocation> = HashSet::new();
        locations.insert(NormalizedLocation::default());
        if let Some(gvar) = &instancer.gvar {
            if let Ok(Some(data)) = gvar.glyph_variation_data(GlyphId::new(gid as u32)) {
                for tuple in data.tuples() {
                    let peak = coords_f64(&tuple.peak(), triples.len());
                    let pos: Vec<(String, f64)> = axis_order
                        .iter()
                        .map(|t| t.to_string())
                        .zip(peak.iter().copied())
                        .collect();
                    let pos_refs: Vec<(&str, f64)> =
                        pos.iter().map(|(s, v)| (s.as_str(), *v)).collect();
                    locations.insert(NormalizedLocation::for_pos(&pos_refs));
                }
            }
        }
        if locations.contains(&corner_loc) {
            continue; // a source already reaches this corner
        }
        locations.insert(corner_loc.clone());
        let model = VariationModel::new(locations.clone(), axis_order.clone());

        // The corner's indicator decomposes into the regions a true
        // master there would get (weight per region).
        let indicator: HashMap<NormalizedLocation, Vec<f64>> = locations
            .iter()
            .map(|loc| {
                let v = if *loc == corner_loc { 1.0 } else { 0.0 };
                (loc.clone(), vec![v])
            })
            .collect();
        let decomposition = model
            .deltas::<f64, f64>(&indicator)
            .map_err(|e| err(format!("variation model failed: {e:?}")))?;

        let at_scaffold = match &scaffold_norm {
            Some(s) => instancer.instance_points(gid, s)?,
            // Synthesis: free extrapolation of the model's trends to
            // the corner (MutatorMath-flavored per-axis peel-off — see
            // extrapolate.rs), NOT the tent model (which zeroes here).
            None => {
                let (base_pts, _) = instancer.base_points(gid)?;
                let sources = instancer.source_deltas(gid, triples.len())?;
                crate::extrapolate::synthesize(&base_pts, triples.len(), &sources, &corner_norm)
            }
        };
        let at_default = instancer.instance_points(gid, &zeroes)?;
        let base_deltas = point_deltas(&at_scaffold, &at_default, 0.0, false);
        if scaffold_norm.is_none() {
            // Synthesis path: a glyph whose extrapolated shape is the
            // default (sub-unit deltas) adds no tuples — and if NO
            // glyph changes, the corner has no trend reaching it.
            let max_d = base_deltas
                .iter()
                .map(|&(dx, dy)| dx.abs().max(dy.abs()))
                .max()
                .unwrap_or(0);
            if max_d == 0 {
                continue;
            }
        }
        for (region, weights) in decomposition {
            if region.is_default() || weights[0] == 0.0 {
                continue;
            }
            let w = weights[0];
            let mut peak = vec![0.0; triples.len()];
            let mut start = vec![0.0; triples.len()];
            let mut end = vec![0.0; triples.len()];
            for (i, tag) in axis_order.iter().enumerate() {
                if let Some(tent) = region.get(tag) {
                    start[i] = tent.min.into_inner().into_inner();
                    peak[i] = tent.peak.into_inner().into_inner();
                    end[i] = tent.max.into_inner().into_inner();
                }
            }
            let deltas = base_deltas
                .iter()
                .map(|&(dx, dy)| (ot_round(dx as f64 * w) as i16, ot_round(dy as f64 * w) as i16))
                .collect();
            extras
                .entry(gid)
                .or_default()
                .push(NewTuple { peak, start, end, deltas });
        }
    }
    if extras.is_empty() {
        if scaffold_norm.is_none() {
            return Err(err(
                "no design trend reaches this corner — a pin would change nothing. \
                 Draw the extreme in the source (or add an instance there as a master) to cover it.",
            ));
        }
        return Ok(font_bytes);
    }
    // Renderers take advances from HVAR, not gvar phantoms — rebuild it
    // from the phantom deltas (incl. the pin's) or the corner renders
    // with default advances (letters collide into a blob).
    let hvar_bytes = crate::spac::rebuild_hvar(&font, &extras, triples.len() as u16)?;
    let options = GrowOptions {
        hvar_bytes: Some(hvar_bytes),
        ..Default::default()
    };
    build_grown_font(&font_bytes, &[], extras, &options)
}

// --------------------------------------------------------------------------
// clamp_out_of_range: bring stranded sources back into the axis box
// --------------------------------------------------------------------------

/// Neutralize out-of-range ("stranded") tuples by zeroing their packed
/// delta bytes AND their coordinates (peak + intermediate bounds) in
/// place. Zero deltas alone would already inert the tuple (0 × any
/// scalar = 0); zeroing the coordinates too makes peak-reading audits
/// (the studio's coverage check) see the source as resolved. This is
/// NOT the mangling the divergence oracle proved: that was zeroing a
/// LIVE tuple's peak (default advance 166→2154) — a zero-peak
/// zero-delta tuple is a global no-op. Intermediate bounds of in-range
/// tuples are clamped to ±1 (varLib clamps those). Stranded shared-peak
/// entries are zeroed as well (every referencing tuple is stranded —
/// same peak).
fn zero_out_of_range_tuples(bytes: &mut [u8], axis_count: usize) -> Result<usize, JsError> {
    fn be_u16(b: &[u8], off: usize) -> Result<u16, JsError> {
        b.get(off..off + 2)
            .map(|s| u16::from_be_bytes([s[0], s[1]]))
            .ok_or_else(|| err("gvar: truncated"))
    }
    fn be_u32(b: &[u8], off: usize) -> Result<u32, JsError> {
        b.get(off..off + 4)
            .map(|s| u32::from_be_bytes([s[0], s[1], s[2], s[3]]))
            .ok_or_else(|| err("gvar: truncated"))
    }
    fn coord_at(bytes: &[u8], off: usize) -> i16 {
        i16::from_be_bytes([bytes[off], bytes[off + 1]])
    }
    fn clamp_block(bytes: &mut [u8], off: usize, n: usize) -> usize {
        let mut changed = 0;
        for i in 0..n {
            let v = coord_at(bytes, off + i * 2);
            let c = v.clamp(-16384, 16384);
            if c != v {
                bytes[off + i * 2..off + i * 2 + 2].copy_from_slice(&c.to_be_bytes());
                changed += 1;
            }
        }
        changed
    }
    fn zero_block(bytes: &mut [u8], off: usize, n: usize) {
        for b in &mut bytes[off..off + n * 2] {
            *b = 0;
        }
    }

    if bytes.len() < 20 {
        return Err(err("gvar: header truncated"));
    }
    let shared_count = be_u16(bytes, 6)? as usize;
    let off_shared = be_u32(bytes, 8)? as usize;
    let glyph_count = be_u16(bytes, 12)? as usize;
    let flags = be_u16(bytes, 14)?;
    let off_data = be_u32(bytes, 16)? as usize;
    let long_offsets = flags & 1 != 0;

    // Shared-peak entries with out-of-range coords: zero them (every
    // tuple referencing one is stranded by definition — same peak — so
    // its deltas die in the glyph walk below; a zero-peak zero-delta
    // tuple is a global no-op).
    let mut stranded_shared: std::collections::HashSet<u16> = std::collections::HashSet::new();
    for i in 0..shared_count {
        let off = off_shared + i * axis_count * 2;
        if (0..axis_count).any(|a| coord_at(bytes, off + a * 2).abs() > 16384) {
            zero_block(bytes, off, axis_count);
            stranded_shared.insert(i as u16);
        }
    }

    // Packed points: (count byte(s) then runs). Returns bytes consumed.
    fn skip_packed_points(bytes: &[u8], mut p: usize) -> Result<usize, JsError> {
        let b0 = *bytes.get(p).ok_or_else(|| err("packed points: truncated"))?;
        p += 1;
        let count = if b0 & 0x80 != 0 {
            let b1 = *bytes.get(p).ok_or_else(|| err("packed points: truncated"))?;
            p += 1;
            (((b0 & 0x7f) as usize) << 8) | b1 as usize
        } else {
            (b0 & 0x7f) as usize
        };
        let mut left = count;
        while left > 0 {
            let r = *bytes.get(p).ok_or_else(|| err("packed run: truncated"))?;
            p += 1;
            let n = (r & 0x7f) as usize + 1;
            let sz = if r & 0x80 != 0 { 2 } else { 1 };
            p += n * sz;
            left = left.saturating_sub(n);
        }
        Ok(p)
    }

    // Zero the packed-delta data bytes within `size` bytes at `p`.
    fn zero_packed_deltas(bytes: &mut [u8], mut p: usize, end: usize) -> Result<(), JsError> {
        while p < end {
            let r = bytes[p];
            p += 1;
            let n = (r & 0x3f) as usize + 1;
            // OT delta-run control (DELTAS_SIZE_MASK): 0x00 = 1 byte,
            // 0x40 = words (2B), 0x80 = DELTAS_ARE_ZERO (no data),
            // 0xC0 = longs (4B).
            let sz = match r & 0xc0 {
                0x00 => 1,
                0x40 => 2,
                0x80 => 0,
                _ => 4,
            };
            for i in 0..n * sz {
                if p + i < bytes.len() {
                    bytes[p + i] = 0;
                }
            }
            p += n * sz;
        }
        Ok(())
    }

    let mut changed = 0;
    for g in 0..glyph_count {
        let (start, end) = if long_offsets {
            (
                be_u32(bytes, 20 + g * 4)? as usize,
                be_u32(bytes, 20 + (g + 1) * 4)? as usize,
            )
        } else {
            (
                be_u16(bytes, 20 + g * 2)? as usize * 2,
                be_u16(bytes, 20 + (g + 1) * 2)? as usize * 2,
            )
        };
        if end <= start {
            continue;
        }
        let gd = off_data + start;
        let count = (be_u16(bytes, gd)? & 0x0fff) as usize;
        let data_off = gd + be_u16(bytes, gd + 2)? as usize;

        // Pass 1: tuple headers — clamp in-range intermediate bounds,
        // and note which tuples are stranded (out-of-range embedded
        // peak, or referencing an out-of-range shared peak).
        let mut stranded: Vec<bool> = Vec::with_capacity(count);
        let mut sizes: Vec<usize> = Vec::with_capacity(count);
        let mut privates: Vec<bool> = Vec::with_capacity(count);
        let mut h = gd + 4;
        for _ in 0..count {
            let data_size = be_u16(bytes, h)? as usize;
            let tuple_index = be_u16(bytes, h + 2)?;
            sizes.push(data_size);
            privates.push(tuple_index & 0x2000 != 0);
            h += 4;
            let mut peak_stranded = false;
            if tuple_index & 0x8000 != 0 {
                peak_stranded = (0..axis_count).any(|a| coord_at(bytes, h + a * 2).abs() > 16384);
                if peak_stranded {
                    // Zero the peak (and the bounds below): with its
                    // deltas also zeroed the tuple is a global no-op,
                    // and peak-reading audits see the source resolved.
                    zero_block(bytes, h, axis_count);
                }
                h += axis_count * 2;
            } else if stranded_shared.contains(&(tuple_index & 0x0fff)) {
                peak_stranded = true;
            }
            if tuple_index & 0x4000 != 0 {
                if peak_stranded {
                    zero_block(bytes, h, axis_count * 2);
                } else {
                    changed += clamp_block(bytes, h, axis_count * 2);
                }
                h += axis_count * 4;
            }
            stranded.push(peak_stranded);
        }
        if !stranded.iter().any(|&x| x) {
            continue;
        }

        // Pass 2: serialized data. The glyph's shared packed-points
        // list exists only when the glyph's tupleVariationCount has
        // the TUPLES_SHARE_POINT_NUMBERS flag (0x8000).
        let mut p = data_off;
        if be_u16(bytes, gd)? & 0x8000 != 0 {
            p = skip_packed_points(bytes, p)?;
        }
        for (i, &is_stranded) in stranded.iter().enumerate() {
            // dataSize covers the private point list AND the deltas —
            // anchor the tuple's end BEFORE skipping the privates.
            let tuple_start = p;
            if privates[i] {
                p = skip_packed_points(bytes, p)?;
            }
            let end_of_tuple = tuple_start + sizes[i];
            if is_stranded {
                zero_packed_deltas(bytes, p, end_of_tuple)?;
                changed += 1;
            }
            p = end_of_tuple;
        }
    }
    Ok(changed)
}
/// Drop stranded (out-of-range) sources: neutralize their tuples by
/// zeroing their packed deltas AND their peaks/bounds (zero deltas make
/// the tuple a no-op; zeroed coordinates make peak-reading audits see
/// the drop), clamp in-range intermediate bounds to ±1, then rebuild
/// HVAR from the remaining tuples. This is what Glyphs.app and the
/// fontmake/varLib pipeline do with sources outside the axis box — the
/// studio's divergence oracle established that Glyphs.app == fontmake
/// == drop, while fontc extrapolated. Dropping makes the studio's font
/// match both.
pub(crate) fn clamp_out_of_range(font_bytes: Vec<u8>) -> Result<Vec<u8>, JsError> {
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let Some(gvar_data) = font.data_for_tag(TAG_GVAR) else {
        return Err(err("font has no gvar table (not a variable font?)"));
    };
    let triples = fvar_triples(&font)?;
    let mut gvar_bytes = gvar_data.as_bytes().to_vec();
    let dropped = zero_out_of_range_tuples(&mut gvar_bytes, triples.len())?;
    if dropped == 0 {
        return Ok(font_bytes);
    }
    let mut replacements = HashMap::new();
    replacements.insert(TAG_GVAR, gvar_bytes);
    let dropped_bytes = crate::repack(&font, replacements);
    let font = FontRef::new(&dropped_bytes).map_err(|e| err(format!("repack: {e}")))?;
    // Phantom deltas from the stranded sources are gone too — rebuild
    // HVAR from what remains, with no injected extras.
    let hvar_bytes = crate::spac::rebuild_hvar(&font, &HashMap::new(), triples.len() as u16)?;
    let mut replacements = HashMap::new();
    replacements.insert(crate::TAG_HVAR, hvar_bytes);
    Ok(crate::repack(&font, replacements))
}