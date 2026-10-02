//! Corner rounding — source-level port of the desktop engine
//! (`src/avar2_studio/transforms/corner_rounding.py`), applied to a
//! .glyphs (format 3) source before fontc compiles it, so the static
//! demo builds the same font the desktop studio does.
//!
//! The semantics mirror the Python engine rule for rule:
//!   - every line-line corner of every interpolating layer gains a
//!     cubic round; the radius blends the layer's stroke (XOPQ) with
//!     its width (XTRA), outer corners and counters separately;
//!   - a corner buried in ink (overlap construction) holds its point;
//!   - an ink-concave corner one thin wall from an outer round follows
//!     it concentrically;
//!   - per-master overrides are pinned deltas on the parametric plane
//!     (fontdrasil's VariationModel — the same model family fontc
//!     compiles with, which matches fontTools);
//!   - axis mode (`rounding_axis`) doubles masters into sharp/rounded
//!     twins on a ROND axis instead of baking, default 0 = sharp.
//!
//! Everything non-geometric passes through untouched: non-corner nodes
//! are cloned verbatim, components/anchors/attributes are kept, and
//! twin ids are deterministic hashes so identical requests build
//! byte-identical fonts.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::fmt::Write as _;

use serde::Deserialize;

use fontdrasil::coords::NormalizedLocation;
use fontdrasil::variations::{ModelDeltas, RoundingBehaviour, VariationModel};
use font_types::Tag;
use glyphs_reader::Plist;
use smol_str::SmolStr;

type Dict = BTreeMap<SmolStr, Plist>;

const ROOM: f64 = 0.48;
const MIN_TURN_DEG: f64 = 1.0;
const T_CAP: f64 = 2.0;
const PLANE_TAGS: [&str; 3] = ["XOPQ", "XTRA", "YOPQ"];

// ---------------------------------------------------------------------------
// Request shapes
// ---------------------------------------------------------------------------

fn d_outer_pct() -> f64 { 15.0 }
fn d_inner_pct() -> f64 { 5.0 }
fn d_outer_xtra() -> f64 { 3.0 }
fn d_inner_xtra() -> f64 { 1.0 }
fn d_outer_min() -> f64 { 2.0 }
fn d_inner_min() -> f64 { 1.0 }
fn d_axis_max() -> f64 { 100.0 }

#[derive(Deserialize)]
pub(crate) struct Params {
    #[serde(default = "d_outer_pct")]
    outer_pct: f64,
    #[serde(default = "d_inner_pct")]
    inner_pct: f64,
    #[serde(default = "d_outer_xtra")]
    outer_xtra_pct: f64,
    #[serde(default = "d_inner_xtra")]
    inner_xtra_pct: f64,
    #[serde(default = "d_outer_min")]
    outer_min: f64,
    #[serde(default = "d_inner_min")]
    inner_min: f64,
    #[serde(default)]
    master_overrides: BTreeMap<String, OverrideEntry>,
    #[serde(default)]
    rounding_axis: bool,
    #[serde(default = "d_axis_max")]
    axis_max: f64,
}

#[derive(Deserialize)]
struct OverrideEntry {
    outer: Option<f64>,
    inner: Option<f64>,
}

/// The `-control.json` sidecar, as far as rounding needs it: correction
/// layers that declare a `target` round "as if at" the target's
/// XOPQ/XTRA (`control_targets` in the Python engine).
#[derive(Deserialize, Default)]
struct ControlSidecar {
    #[serde(default)]
    axes: Vec<ControlAxis>,
}

#[derive(Deserialize)]
struct ControlAxis {
    #[serde(default)]
    layers: Vec<ControlLayer>,
}

#[derive(Deserialize)]
struct ControlLayer {
    glyph: Option<String>,
    #[serde(default)]
    location: HashMap<String, f64>,
    #[serde(default)]
    target: HashMap<String, f64>,
}

type Targets = HashMap<String, Vec<(HashMap<String, f64>, HashMap<String, f64>)>>;

fn control_targets(control_json: Option<&str>) -> Result<Targets, String> {
    let mut out: Targets = HashMap::new();
    let Some(text) = control_json else { return Ok(out) };
    if text.trim().is_empty() {
        return Ok(out);
    }
    let sidecar: ControlSidecar =
        serde_json::from_str(text).map_err(|e| format!("bad control sidecar JSON: {e}"))?;
    for ax in sidecar.axes {
        for rec in ax.layers {
            let declared: HashMap<String, f64> = rec
                .target
                .iter()
                .filter(|(k, _)| k.as_str() == "XOPQ" || k.as_str() == "XTRA")
                .map(|(k, v)| (k.clone(), *v))
                .collect();
            if declared.is_empty() {
                continue;
            }
            let Some(glyph) = rec.glyph else { continue };
            out.entry(glyph).or_default().push((rec.location, declared));
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// Plist helpers
// ---------------------------------------------------------------------------

fn num_plist(v: f64) -> Plist {
    if v == v.trunc() && v.abs() < 9.0e15 {
        Plist::Integer(v as i64)
    } else {
        Plist::Float(v.into())
    }
}

fn as_dict_mut<'a>(p: &'a mut Plist, context: &str) -> Result<&'a mut Dict, String> {
    match p {
        Plist::Dictionary(d) => Ok(d),
        _ => Err(format!("{context}: expected a dictionary")),
    }
}

fn layer_coordinates(layer: &Dict) -> Option<Vec<f64>> {
    let attr = layer.get("attr").or_else(|| layer.get("attributes"))?;
    let coords = attr.as_dict()?.get("coordinates")?.as_array()?;
    coords.iter().map(Plist::as_f64).collect()
}

fn fnv1a(seed: u64, bytes: &[u8]) -> u64 {
    let mut h = seed;
    for b in bytes {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

/// Deterministic UUID from content, like editor.rs `brace_layer_id`:
/// identical inputs give identical ids, so rebuilds are byte-stable.
fn det_uuid(buf: &[u8]) -> String {
    let h1 = fnv1a(0xcbf29ce484222325, buf);
    let h2 = fnv1a(0x84222325cbf29ce4, buf);
    let mut b = [0u8; 16];
    b[..8].copy_from_slice(&h1.to_be_bytes());
    b[8..].copy_from_slice(&h2.to_be_bytes());
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let mut s = String::with_capacity(36);
    for (i, byte) in b.iter().enumerate() {
        if matches!(i, 4 | 6 | 8 | 10) {
            s.push('-');
        }
        write!(s, "{byte:02X}").unwrap();
    }
    s
}

// ---------------------------------------------------------------------------
// Working model of a glyph's layers
// ---------------------------------------------------------------------------

/// One node: position, normalized glyphsLib type name ("line" etc.) and
/// the original Plist value, cloned back verbatim when not a corner.
struct Node {
    x: f64,
    y: f64,
    kind: &'static str,
    raw: Plist,
}

struct PathData {
    shape_idx: usize,
    closed: bool,
    nodes: Vec<Node>,
}

struct LayerData {
    layer_idx: usize,
    layer_id: String,
    coords: Option<Vec<f64>>,
    is_master: bool,
    paths: Vec<PathData>,
}

fn type_name(t: &str) -> Result<&'static str, String> {
    match t {
        "l" | "ls" | "LINE" | "LINE SMOOTH" => Ok("line"),
        "c" | "cs" | "CURVE" | "CURVE SMOOTH" => Ok("curve"),
        "o" | "OFFCURVE" => Ok("offcurve"),
        "q" | "qs" | "QCURVE" | "QCURVE SMOOTH" => Ok("qcurve"),
        _ => Err(format!("unknown node type '{t}'")),
    }
}

fn parse_layer(layer_idx: usize, ld: &Dict, master_ids: &HashSet<String>) -> Result<Option<LayerData>, String> {
    let layer_id = ld
        .get("layerId")
        .and_then(Plist::as_str)
        .unwrap_or("")
        .to_string();
    let coords = layer_coordinates(ld);
    let is_master = master_ids.contains(&layer_id);
    if !is_master && coords.is_none() {
        return Ok(None); // backup layer — never touched
    }
    let mut paths = Vec::new();
    if let Some(Plist::Array(shapes)) = ld.get("shapes") {
        for (si, shape) in shapes.iter().enumerate() {
            let Some(sd) = shape.as_dict() else { continue };
            let Some(Plist::Array(raw_nodes)) = sd.get("nodes") else {
                continue; // a component ("ref") or other shape
            };
            let closed = sd.get("closed").and_then(Plist::as_i64).map(|v| v != 0).unwrap_or(false);
            let mut nodes = Vec::new();
            for n in raw_nodes {
                let Plist::Array(a) = n else {
                    return Err("glyphs 2 node strings are not supported in the browser — build with the desktop studio".into());
                };
                if a.len() < 3 {
                    return Err("node without a type".into());
                }
                let x = a[0].as_f64().ok_or("bad node x")?;
                let y = a[1].as_f64().ok_or("bad node y")?;
                let kind = type_name(a[2].as_str().unwrap_or("l"))?;
                nodes.push(Node { x, y, kind, raw: n.clone() });
            }
            paths.push(PathData { shape_idx: si, closed, nodes });
        }
    } else if ld.contains_key("paths") {
        return Err("glyphs 2 sources are not supported in the browser — build with the desktop studio".into());
    }
    Ok(Some(LayerData { layer_idx, layer_id, coords, is_master, paths }))
}

fn structure(layer: &LayerData) -> Vec<(bool, Vec<&'static str>)> {
    layer
        .paths
        .iter()
        .map(|p| (p.closed, p.nodes.iter().map(|n| n.kind).collect()))
        .collect()
}

// ---------------------------------------------------------------------------
// Geometry (ports of the Python helpers, same names)
// ---------------------------------------------------------------------------

fn pts(p: &PathData) -> Vec<(f64, f64)> {
    p.nodes.iter().map(|n| (n.x, n.y)).collect()
}

fn area(p: &[(f64, f64)]) -> f64 {
    let n = p.len();
    (0..n)
        .map(|i| p[i].0 * p[(i + 1) % n].1 - p[(i + 1) % n].0 * p[i].1)
        .sum::<f64>()
        / 2.0
}

fn inside(pt: (f64, f64), poly: &[(f64, f64)]) -> bool {
    let (x, y) = pt;
    let n = poly.len();
    let mut c = false;
    for i in 0..n {
        let (x1, y1) = poly[i];
        let (x2, y2) = poly[(i + 1) % n];
        if (y1 > y) != (y2 > y) && x < (x2 - x1) * (y - y1) / (y2 - y1) + x1 {
            c = !c;
        }
    }
    c
}

fn winding(pt: (f64, f64), poly: &[(f64, f64)]) -> i64 {
    let (x, y) = pt;
    let n = poly.len();
    let mut w = 0;
    for i in 0..n {
        let (x1, y1) = poly[i];
        let (x2, y2) = poly[(i + 1) % n];
        if y1 <= y && y < y2 && (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1) > 0.0 {
            w += 1;
        } else if y2 <= y && y < y1 && (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1) < 0.0 {
            w -= 1;
        }
    }
    w
}

fn snap(v: f64) -> f64 {
    (v + 0.5).floor()
}

struct Stats {
    corners: u64,
    layers: u64,
    collapsed: u64,
    clamped: u64,
    hidden: u64,
    concentric: u64,
    twins: u64,
    skipped_layers: Vec<String>,
}

/// (t, k, effective radius): tangent cap + room clamp, collapsed to
/// zeros when no room is left.
fn fit(r: f64, phi: f64, l1: f64, l2: f64, stats: &mut Stats) -> (f64, f64, f64) {
    let tan_half = (phi.to_radians() / 2.0).tan();
    let mut t = (r * tan_half).min(T_CAP * r);
    let t_max = (ROOM * l1.min(l2)).floor();
    if t > t_max {
        t = t_max;
        stats.clamped += 1;
    }
    if t < 1.0 {
        stats.collapsed += 1;
        return (0.0, 0.0, 0.0);
    }
    let r_eff = t / tan_half;
    let k = (4.0 / 3.0) * (phi.to_radians() / 4.0).tan() * r_eff;
    (t, k, r_eff)
}

// ---------------------------------------------------------------------------
// Font-level context
// ---------------------------------------------------------------------------

struct Master {
    id: String,
    name: String,
    values: Vec<f64>,
}

struct FontCtx {
    axis_tags: Vec<String>,
    masters: Vec<Master>,
    xopq_idx: usize,
    xtra_idx: Option<usize>,
    plane: Vec<(usize, String)>,
}

fn font_ctx(root: &Dict) -> Result<Option<FontCtx>, String> {
    let mut axis_tags = Vec::new();
    if let Some(Plist::Array(axes)) = root.get("axes") {
        for a in axes {
            let Some(d) = a.as_dict() else { continue };
            let tag = d
                .get("tag")
                .or_else(|| d.get("Tag"))
                .and_then(Plist::as_str)
                .unwrap_or("");
            axis_tags.push(tag.to_string());
        }
    }
    let Some(xopq_idx) = axis_tags.iter().position(|t| t.eq_ignore_ascii_case("XOPQ")) else {
        return Ok(None); // the stroke rule has nothing to follow
    };
    let xtra_idx = axis_tags.iter().position(|t| t.eq_ignore_ascii_case("XTRA"));
    let plane = axis_tags
        .iter()
        .enumerate()
        .filter(|(_, t)| PLANE_TAGS.contains(&t.as_str()))
        .map(|(i, t)| (i, t.clone()))
        .collect();
    let mut masters = Vec::new();
    if let Some(Plist::Array(fm)) = root.get("fontMaster") {
        for m in fm {
            let Some(d) = m.as_dict() else { continue };
            let id = d.get("id").and_then(Plist::as_str).unwrap_or("").to_string();
            let name = d.get("name").and_then(Plist::as_str).unwrap_or(&id).to_string();
            let values = d
                .get("axesValues")
                .and_then(Plist::as_array)
                .map(|a| a.iter().filter_map(Plist::as_f64).collect())
                .unwrap_or_default();
            masters.push(Master { id, name, values });
        }
    }
    if masters.is_empty() {
        return Err("no fontMaster entries".into());
    }
    Ok(Some(FontCtx { axis_tags, masters, xopq_idx, xtra_idx, plane }))
}

/// (XOPQ, XTRA) a layer's drawing represents — its coordinates, a
/// correction target's declared values, or its master's axes.
fn layer_stroke(ctx: &FontCtx, glyph: &str, layer: &LayerData, targets: &Targets) -> (Option<f64>, f64) {
    let pick = |coords: &[f64]| {
        let xopq = coords.get(ctx.xopq_idx).copied();
        let xtra = ctx.xtra_idx.and_then(|i| coords.get(i).copied()).unwrap_or(0.0);
        (xopq, xtra)
    };
    if let Some(coords) = &layer.coords {
        let (mut xopq, mut xtra) = pick(coords);
        let location: HashMap<&str, f64> = ctx
            .axis_tags
            .iter()
            .zip(coords.iter())
            .map(|(t, v)| (t.as_str(), *v))
            .collect();
        if let Some(recs) = targets.get(glyph) {
            for (rec_loc, target) in recs {
                let matches = rec_loc
                    .iter()
                    .all(|(k, v)| (location.get(k.as_str()).copied().unwrap_or(1e9) - v).abs() <= 0.51);
                if matches {
                    if let Some(v) = target.get("XOPQ") {
                        xopq = Some(*v);
                    }
                    if let Some(v) = target.get("XTRA") {
                        xtra = *v;
                    }
                    break;
                }
            }
        }
        return (xopq, xtra);
    }
    let master = ctx.masters.iter().find(|m| m.id == layer.layer_id);
    match master {
        Some(m) => pick(&m.values),
        None => (None, 0.0),
    }
}

/// The parametric location a layer's radii are evaluated at, with a
/// correction target's XOPQ/XTRA merged in.
fn plane_loc(ctx: &FontCtx, glyph: &str, layer: &LayerData, targets: &Targets) -> Option<HashMap<String, f64>> {
    let from_coords = |coords: &[f64]| -> HashMap<String, f64> {
        ctx.plane
            .iter()
            .filter(|(i, _)| *i < coords.len())
            .map(|(i, tag)| (tag.clone(), coords[*i]))
            .collect()
    };
    if let Some(coords) = &layer.coords {
        let mut loc = from_coords(coords);
        let location: HashMap<&str, f64> = ctx
            .axis_tags
            .iter()
            .zip(coords.iter())
            .map(|(t, v)| (t.as_str(), *v))
            .collect();
        if let Some(recs) = targets.get(glyph) {
            for (rec_loc, target) in recs {
                let matches = rec_loc
                    .iter()
                    .all(|(k, v)| (location.get(k.as_str()).copied().unwrap_or(1e9) - v).abs() <= 0.51);
                if matches {
                    for (k, v) in target {
                        loc.insert(k.clone(), *v);
                    }
                    break;
                }
            }
        }
        return Some(loc);
    }
    let master = ctx.masters.iter().find(|m| m.id == layer.layer_id)?;
    Some(from_coords(&master.values))
}

// ---------------------------------------------------------------------------
// Per-master overrides: pinned deltas on the parametric plane
// ---------------------------------------------------------------------------

struct OverrideModel {
    tags: Vec<String>,
    origin: Vec<f64>,
    spans: Vec<(f64, f64)>,
    model: VariationModel,
    deltas: ModelDeltas<f64>,
}

impl OverrideModel {
    fn norm(&self, j: usize, v: f64) -> f64 {
        let d = v - self.origin[j];
        let span = if d > 0.0 {
            self.spans[j].1 - self.origin[j]
        } else {
            self.origin[j] - self.spans[j].0
        };
        if span > 0.0 {
            d / span
        } else {
            0.0
        }
    }

    fn at(&self, loc: &HashMap<String, f64>) -> (f64, f64) {
        let pos: Vec<(String, f64)> = self
            .tags
            .iter()
            .enumerate()
            .map(|(j, tag)| {
                let v = loc.get(tag).copied().unwrap_or(self.origin[j]);
                (tag.clone(), self.norm(j, v).clamp(-1.0, 1.0))
            })
            .collect();
        let pos_refs: Vec<(&str, f64)> = pos.iter().map(|(t, v)| (t.as_str(), *v)).collect();
        let nloc = NormalizedLocation::for_pos(&pos_refs);
        let out = self.model.interpolate_from_deltas(&nloc, &self.deltas);
        (out.first().copied().unwrap_or(0.0), out.get(1).copied().unwrap_or(0.0))
    }
}

#[allow(clippy::too_many_arguments)]
fn override_model(ctx: &FontCtx, params: &Params) -> Result<Option<OverrideModel>, String> {
    if params.master_overrides.is_empty() {
        return Ok(None);
    }
    let names: HashSet<&str> = ctx.masters.iter().map(|m| m.name.as_str()).collect();
    let unknown: Vec<&str> = params
        .master_overrides
        .keys()
        .map(String::as_str)
        .filter(|k| !names.contains(k))
        .collect();
    if !unknown.is_empty() {
        let mut valid: Vec<&str> = names.into_iter().collect();
        valid.sort_unstable();
        return Err(format!(
            "round_corners: master override(s) name no master: {} (masters: {})",
            unknown.join(", "),
            valid.join(", ")
        ));
    }
    let (o_pct, o_xtra, o_min) = (params.outer_pct / 100.0, params.outer_xtra_pct / 100.0, params.outer_min);
    let (i_pct, i_xtra, i_min) = (params.inner_pct / 100.0, params.inner_xtra_pct / 100.0, params.inner_min);

    // Per unique parametric point: pinned (outer, inner) deltas.
    let mut order: Vec<Vec<f64>> = Vec::new();
    let mut pinned: Vec<(Option<f64>, Option<f64>, String)> = Vec::new();
    for m in &ctx.masters {
        let loc: Vec<f64> = ctx
            .plane
            .iter()
            .map(|(i, _)| m.values.get(*i).copied().unwrap_or(0.0))
            .collect();
        let idx = match order.iter().position(|l| l == &loc) {
            Some(i) => i,
            None => {
                order.push(loc);
                pinned.push((None, None, String::new()));
                order.len() - 1
            }
        };
        let Some(ov) = params.master_overrides.get(&m.name) else { continue };
        let xopq = m.values.get(ctx.xopq_idx).copied().unwrap_or(0.0);
        let xtra = ctx.xtra_idx.and_then(|i| m.values.get(i).copied()).unwrap_or(0.0);
        let slot = &mut pinned[idx];
        if let Some(o) = ov.outer {
            let want = o - (o_pct * xopq + o_xtra * xtra).max(o_min);
            if let Some(prev) = slot.0 {
                if (prev - want).abs() > 1e-6 {
                    return Err(format!(
                        "round_corners: '{}' and '{}' sit at the same parametric point but override different values — a slanted master shares its upright's rounding",
                        slot.2, m.name
                    ));
                }
            }
            slot.0 = Some(want);
        }
        if let Some(iv) = ov.inner {
            let want = iv - (i_pct * xopq + i_xtra * xtra).max(i_min);
            if let Some(prev) = slot.1 {
                if (prev - want).abs() > 1e-6 {
                    return Err(format!(
                        "round_corners: '{}' and '{}' sit at the same parametric point but override different values — a slanted master shares its upright's rounding",
                        slot.2, m.name
                    ));
                }
            }
            slot.1 = Some(want);
        }
        slot.2 = m.name.clone();
    }

    let tags: Vec<String> = ctx.plane.iter().map(|(_, t)| t.clone()).collect();
    let origin = order[0].clone();
    let spans: Vec<(f64, f64)> = (0..tags.len())
        .map(|j| {
            let vals: Vec<f64> = order.iter().map(|l| l[j]).collect();
            (vals.iter().cloned().fold(f64::INFINITY, f64::min),
             vals.iter().cloned().fold(f64::NEG_INFINITY, f64::max))
        })
        .collect();

    let tmp = OverrideModel {
        tags: tags.clone(),
        origin: origin.clone(),
        spans: spans.clone(),
        model: VariationModel::empty(),
        deltas: Vec::new(),
    };
    let mut locations = HashSet::new();
    let mut point_seqs: HashMap<NormalizedLocation, Vec<f64>> = HashMap::new();
    for (idx, loc) in order.iter().enumerate() {
        let pos: Vec<(String, f64)> = tags
            .iter()
            .enumerate()
            .map(|(j, tag)| (tag.clone(), tmp.norm(j, loc[j])))
            .collect();
        let pos_refs: Vec<(&str, f64)> = pos.iter().map(|(t, v)| (t.as_str(), *v)).collect();
        let nloc = NormalizedLocation::for_pos(&pos_refs);
        locations.insert(nloc.clone());
        point_seqs.insert(
            nloc,
            vec![pinned[idx].0.unwrap_or(0.0), pinned[idx].1.unwrap_or(0.0)],
        );
    }
    let axis_order: Vec<Tag> = tags
        .iter()
        .map(|t| Tag::new_checked(t.as_bytes()).map_err(|e| format!("bad plane tag '{t}': {e}")))
        .collect::<Result<_, _>>()?;
    let model = VariationModel::new(locations, axis_order);
    let deltas = model
        .deltas_with_rounding::<f64, f64>(&point_seqs, RoundingBehaviour::None)
        .map_err(|e| format!("override model failed: {e:?}"))?;
    Ok(Some(OverrideModel { tags, origin, spans, model, deltas }))
}

// ---------------------------------------------------------------------------
// The engine
// ---------------------------------------------------------------------------

/// Round a .glyphs (format 3) source in place. Returns the transformed
/// source text and a stats summary line.
pub(crate) fn round_source(
    source: &str,
    params_json: &str,
    control_json: Option<&str>,
) -> Result<(String, String), String> {
    let params: Params =
        serde_json::from_str(params_json).map_err(|e| format!("bad round_corners params: {e}"))?;
    let targets = control_targets(control_json)?;

    let mut plist = Plist::parse(source).map_err(|e| format!("could not parse .glyphs: {e}"))?;
    let root = as_dict_mut(&mut plist, ".glyphs root")?;

    let Some(ctx) = font_ctx(root)? else {
        return Ok((source.to_string(), "round_corners: no XOPQ axis — nothing rounded".into()));
    };
    let over_model = override_model(&ctx, &params)?;

    let mut stats = Stats {
        corners: 0,
        layers: 0,
        collapsed: 0,
        clamped: 0,
        hidden: 0,
        concentric: 0,
        twins: 0,
        skipped_layers: Vec::new(),
    };

    let axis_max = if params.rounding_axis {
        if params.axis_max <= 0.0 {
            return Err("round_corners: the ROND axis maximum must be positive".into());
        }
        Some(params.axis_max)
    } else {
        None
    };

    // Axis mode: grow the font by the ROND axis before touching glyphs.
    let mut twin_master_id: HashMap<String, String> = HashMap::new();
    if let Some(max) = axis_max {
        extend_for_axis(root, max, &ctx, &mut twin_master_id)?;
    }

    let master_ids: HashSet<String> = ctx.masters.iter().map(|m| m.id.clone()).collect();

    let Some(Plist::Array(glyphs)) = root.get_mut("glyphs") else {
        return Err("no glyphs array".into());
    };

    for glyph in glyphs.iter_mut() {
        let gd = as_dict_mut(glyph, "glyph")?;
        let gname = gd
            .get("glyphname")
            .and_then(Plist::as_str)
            .unwrap_or("")
            .to_string();
        round_glyph(
            gd, &gname, &ctx, &params, &targets, over_model.as_ref(), axis_max,
            &twin_master_id, &master_ids, &mut stats,
        )?;
    }

    let summary = format!(
        "round_corners: {} corners over {} layers; {} clamped, {} collapsed, {} buried in overlaps, {} concentric at thin walls{}{}",
        stats.corners, stats.layers, stats.clamped, stats.collapsed, stats.hidden, stats.concentric,
        if params.master_overrides.is_empty() {
            String::new()
        } else {
            format!(", {} master override(s)", params.master_overrides.len())
        },
        match axis_max {
            Some(m) => format!("; ROND axis 0-{m} (default 0 = sharp), {} twin layers", stats.twins),
            None => String::new(),
        },
    );
    Ok((plist.to_string(), summary))
}

/// Grow the font by a ROND axis: axis entry, every master/instance and
/// coordinate layer gains a trailing 0, every master gets a twin at
/// `axis_max` (deterministic id) to hold the rounded geometry.
fn extend_for_axis(
    root: &mut Dict,
    axis_max: f64,
    ctx: &FontCtx,
    twin_master_id: &mut HashMap<String, String>,
) -> Result<(), String> {
    match root.get_mut("axes") {
        Some(Plist::Array(axes)) => {
            let mut d = Dict::new();
            d.insert(SmolStr::from("name"), Plist::String("Rounding".into()));
            d.insert(SmolStr::from("tag"), Plist::String("ROND".into()));
            axes.push(Plist::Dictionary(d));
        }
        _ => return Err("no axes array — the ROND axis needs Glyphs 3 axes".into()),
    }
    // Masters: extend, then append twins (clones carry metrics etc.).
    let mut twins: Vec<Plist> = Vec::new();
    match root.get_mut("fontMaster") {
        Some(Plist::Array(fm)) => {
            for m in fm.iter_mut() {
                let md = as_dict_mut(m, "fontMaster")?;
                let vals = match md.get_mut("axesValues") {
                    Some(Plist::Array(a)) => a,
                    _ => return Err("master without axesValues".into()),
                };
                vals.push(num_plist(0.0));
            }
            for m in fm.iter() {
                let Some(md) = m.as_dict() else { continue };
                let mut td = md.clone();
                let id = md.get("id").and_then(Plist::as_str).unwrap_or("").to_string();
                let name = md.get("name").and_then(Plist::as_str).unwrap_or(&id).to_string();
                let tid = det_uuid(format!("{id}:ROND").as_bytes());
                td.insert(SmolStr::from("id"), Plist::String(tid.clone().into()));
                td.insert(
                    SmolStr::from("name"),
                    Plist::String(format!("{name} Rounded").into()),
                );
                if let Some(Plist::Array(vals)) = td.get_mut("axesValues") {
                    if let Some(last) = vals.last_mut() {
                        *last = num_plist(axis_max);
                    }
                }
                twin_master_id.insert(id, tid);
                twins.push(Plist::Dictionary(td));
            }
            fm.extend(twins);
        }
        _ => return Err("no fontMaster array".into()),
    }
    let _ = ctx;
    if let Some(Plist::Array(instances)) = root.get_mut("instances") {
        for inst in instances.iter_mut() {
            if let Plist::Dictionary(idict) = inst {
                if let Some(Plist::Array(vals)) = idict.get_mut("axesValues") {
                    vals.push(num_plist(0.0));
                }
            }
        }
    }
    // Every coordinate layer in the font gains the axis at 0.
    if let Some(Plist::Array(glyphs)) = root.get_mut("glyphs") {
        for glyph in glyphs.iter_mut() {
            let Plist::Dictionary(gd) = glyph else { continue };
            let Some(Plist::Array(layers)) = gd.get_mut("layers") else { continue };
            for layer in layers.iter_mut() {
                let Plist::Dictionary(ld) = layer else { continue };
                let key = if ld.contains_key("attr") { "attr" } else { "attributes" };
                let Some(Plist::Dictionary(ad)) = ld.get_mut(key) else { continue };
                if let Some(Plist::Array(coords)) = ad.get_mut("coordinates") {
                    coords.push(num_plist(0.0));
                }
            }
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn round_glyph(
    gd: &mut Dict,
    gname: &str,
    ctx: &FontCtx,
    params: &Params,
    targets: &Targets,
    over_model: Option<&OverrideModel>,
    axis_max: Option<f64>,
    twin_master_id: &HashMap<String, String>,
    master_ids: &HashSet<String>,
    stats: &mut Stats,
) -> Result<(), String> {
    let Some(Plist::Array(layer_plists)) = gd.get_mut("layers") else {
        return Ok(());
    };

    // Working copies of every touchable layer.
    let mut all: Vec<LayerData> = Vec::new();
    for (li, lp) in layer_plists.iter().enumerate() {
        let Some(ld) = lp.as_dict() else { continue };
        if let Some(data) = parse_layer(li, ld, master_ids)? {
            all.push(data);
        }
    }
    let master_layers: Vec<usize> = ctx
        .masters
        .iter()
        .filter_map(|m| all.iter().position(|l| l.layer_id == m.id))
        .collect();
    if master_layers.is_empty() {
        return Ok(());
    }

    // In axis mode every interpolating layer gets a twin, rounded or not.
    let make_twins = axis_max.is_some();

    let ref_structure = structure(&all[master_layers[0]]);
    let masters_match = master_layers
        .iter()
        .all(|&i| structure(&all[i]) == ref_structure);

    // The interpolating set: master layers + structure-matched braces.
    let mut layers: Vec<usize> = Vec::new();
    if masters_match {
        layers.extend(&master_layers);
        for (i, l) in all.iter().enumerate() {
            if l.is_master || l.coords.is_none() {
                continue;
            }
            if structure(l) == ref_structure {
                layers.push(i);
            } else {
                stats.skipped_layers.push(format!("{gname}/brace"));
            }
        }
    } else {
        stats.skipped_layers.push(format!("{gname} (masters differ)"));
    }

    // Corners, decided on the master layers (same nodes in every layer).
    let mut path_corners: Vec<(usize, Vec<usize>)> = Vec::new();
    if masters_match {
        for (pi, (_closed, types)) in ref_structure.iter().enumerate() {
            let n = types.len();
            let mut skip: HashSet<usize> = (0..n)
                .filter(|&i| !(types[i] == "line" && types[(i + 1) % n] == "line"))
                .collect();
            for &mi in &master_layers {
                let p = pts(&all[mi].paths[pi]);
                for i in 0..n {
                    let (dx, dy) = (p[(i + 1) % n].0 - p[i].0, p[(i + 1) % n].1 - p[i].1);
                    if (dx * dx + dy * dy).sqrt() < 1e-9 {
                        skip.insert(i);
                        skip.insert((i + 1) % n);
                    }
                }
            }
            let corners: Vec<usize> = (0..n).filter(|i| !skip.contains(i)).collect();
            if !corners.is_empty() {
                stats.corners += corners.len() as u64;
                path_corners.push((pi, corners));
            }
        }
    }

    // Plan per layer across all paths (concentric pairing needs them all).
    let (o_pct, o_xtra, o_min) = (params.outer_pct / 100.0, params.outer_xtra_pct / 100.0, params.outer_min);
    let (i_pct, i_xtra, i_min) = (params.inner_pct / 100.0, params.inner_xtra_pct / 100.0, params.inner_min);
    let mut plans: HashMap<(usize, usize), HashMap<usize, (f64, f64)>> = HashMap::new();
    for &lidx in &layers {
        let layer = &all[lidx];
        let (w_opt, xt) = layer_stroke(ctx, gname, layer, targets);
        let w = w_opt.unwrap_or_else(|| ctx.masters[0].values.get(ctx.xopq_idx).copied().unwrap_or(0.0));
        let (mut d_out, mut d_in) = (0.0, 0.0);
        if let Some(model) = over_model {
            if let Some(loc) = plane_loc(ctx, gname, layer, targets) {
                let (a, b) = model.at(&loc);
                d_out = a;
                d_in = b;
            }
        }
        let polys: Vec<Vec<(f64, f64)>> = layer.paths.iter().map(pts).collect();
        let mut outers: Vec<(f64, f64, f64, f64)> = Vec::new();
        let mut inners: Vec<(usize, usize, (f64, f64), f64, f64, f64, f64)> = Vec::new();
        for (pi, corners) in &path_corners {
            let p = &polys[*pi];
            let n = p.len();
            let s = if area(p) > 0.0 { 1.0 } else { -1.0 };
            let is_counter = polys
                .iter()
                .enumerate()
                .filter(|(qi, q)| *qi != *pi && inside(p[0], q))
                .count()
                % 2
                == 1;
            for &i in corners {
                let a = p[(i + n - 1) % n];
                let b = p[i];
                let c = p[(i + 1) % n];
                let w_other: i64 = polys
                    .iter()
                    .enumerate()
                    .filter(|(qi, _)| *qi != *pi)
                    .map(|(_, q)| winding(b, q))
                    .sum();
                let own = if s > 0.0 { 1 } else { -1 };
                if w_other != 0 && w_other + own != 0 {
                    plans.entry((lidx, *pi)).or_default().insert(i, (0.0, 0.0));
                    stats.hidden += 1;
                    continue;
                }
                let v1 = (b.0 - a.0, b.1 - a.1);
                let v2 = (c.0 - b.0, c.1 - b.1);
                let l1 = (v1.0 * v1.0 + v1.1 * v1.1).sqrt();
                let l2 = (v2.0 * v2.0 + v2.1 * v2.1).sqrt();
                let cross = v1.0 * v2.1 - v1.1 * v2.0;
                let dot = v1.0 * v2.0 + v1.1 * v2.1;
                let phi = cross.atan2(dot).to_degrees().abs();
                if phi < MIN_TURN_DEG || l1 < 1e-9 || l2 < 1e-9 {
                    plans.entry((lidx, *pi)).or_default().insert(i, (0.0, 0.0));
                    continue;
                }
                let outer = ((cross > 0.0) == (s > 0.0)) != is_counter;
                if outer {
                    let r = (((o_pct * w + o_xtra * xt).max(o_min)) + d_out).max(0.0);
                    let (t, k, r_eff) = fit(r, phi, l1, l2, stats);
                    plans.entry((lidx, *pi)).or_default().insert(i, (t, k));
                    if t > 0.0 {
                        outers.push((b.0, b.1, r_eff, phi));
                    }
                } else {
                    let r = (((i_pct * w + i_xtra * xt).max(i_min)) + d_in).max(0.0);
                    inners.push((*pi, i, b, phi, l1, l2, r));
                }
            }
        }
        for (pi, i, b, phi, l1, l2, mut r) in inners {
            let mut sorted: Vec<&(f64, f64, f64, f64)> = outers.iter().collect();
            sorted.sort_by(|p1, p2| {
                let d1 = (p1.0 - b.0).powi(2) + (p1.1 - b.1).powi(2);
                let d2 = (p2.0 - b.0).powi(2) + (p2.1 - b.1).powi(2);
                d1.partial_cmp(&d2).unwrap_or(std::cmp::Ordering::Equal)
            });
            for (ox, oy, r_o, phi_o) in sorted {
                let d = ((ox - b.0).powi(2) + (oy - b.1).powi(2)).sqrt();
                if d >= *r_o {
                    continue;
                }
                let mid = ((ox + b.0) / 2.0, (oy + b.1) / 2.0);
                let total: i64 = polys.iter().map(|q| winding(mid, q)).sum();
                if total == 0 {
                    continue;
                }
                let wall = d * (phi_o.to_radians() / 2.0).cos();
                if r_o - wall > r {
                    r = r_o - wall;
                    stats.concentric += 1;
                }
                break;
            }
            let (t, k, _r_eff) = fit(r, phi, l1, l2, stats);
            plans.entry((lidx, pi)).or_default().insert(i, (t, k));
        }
    }

    // Apply. In axis mode the twin takes the round and the original
    // collapses; otherwise the original takes the round.
    let mut twins: Vec<Plist> = Vec::new();
    if make_twins {
        for (lidx, l) in all.iter().enumerate() {
            let Some(src) = layer_plists.get(l.layer_idx) else { continue };
            let Some(sd) = src.as_dict() else { continue };
            let mut td = sd.clone();
            if l.is_master {
                let tid = twin_master_id
                    .get(&l.layer_id)
                    .ok_or("twin master missing")?
                    .clone();
                td.insert(SmolStr::from("layerId"), Plist::String(tid.clone().into()));
                td.insert(SmolStr::from("associatedMasterId"), Plist::String(tid.into()));
            } else {
                let tid = det_uuid(format!("{gname}:{}:ROND-twin", l.layer_id).as_bytes());
                td.insert(SmolStr::from("layerId"), Plist::String(tid.into()));
                if let Some(assoc) = sd.get("associatedMasterId").and_then(Plist::as_str) {
                    if let Some(t) = twin_master_id.get(assoc) {
                        td.insert(
                            SmolStr::from("associatedMasterId"),
                            Plist::String(t.clone().into()),
                        );
                    }
                }
                let key = if td.contains_key("attr") { "attr" } else { "attributes" };
                if let Some(Plist::Dictionary(ad)) = td.get_mut(key) {
                    if let Some(Plist::Array(coords)) = ad.get_mut("coordinates") {
                        if let Some(last) = coords.last_mut() {
                            *last = num_plist(axis_max.unwrap_or(0.0));
                        }
                    }
                }
            }
            // The twin takes the rounded plan; write its paths now.
            let wi = twins.len();
            twins.push(Plist::Dictionary(td));
            let twin_dict = match &mut twins[wi] {
                Plist::Dictionary(d) => d,
                _ => unreachable!(),
            };
            if layers.contains(&lidx) {
                apply_plan(twin_dict, l, &path_corners, |pi, i| {
                    plans
                        .get(&(lidx, pi))
                        .and_then(|m| m.get(&i))
                        .copied()
                        .unwrap_or((0.0, 0.0))
                })?;
            }
        }
    }

    for &lidx in &layers {
        let l = &all[lidx];
        let Some(lp) = layer_plists.get_mut(l.layer_idx) else { continue };
        let ld = as_dict_mut(lp, "layer")?;
        if make_twins {
            // The original collapses: same quads, coincident on the corner.
            apply_plan(ld, l, &path_corners, |_, _| (0.0, 0.0))?;
        } else {
            apply_plan(ld, l, &path_corners, |pi, i| {
                plans.get(&(lidx, pi)).and_then(|m| m.get(&i)).copied().unwrap_or((0.0, 0.0))
            })?;
        }
    }
    stats.layers += layers.len() as u64;
    if make_twins {
        stats.twins += twins.len() as u64;
        layer_plists.extend(twins);
    }
    Ok(())
}

/// Rebuild a layer dict's path nodes with the plan applied: non-corner
/// nodes pass through verbatim; each corner becomes T1(line smooth),
/// two offcurves, T2(curve smooth) — coincident when (t, k) is zero.
fn apply_plan(
    ld: &mut Dict,
    layer: &LayerData,
    path_corners: &[(usize, Vec<usize>)],
    tk: impl Fn(usize, usize) -> (f64, f64),
) -> Result<(), String> {
    let Some(Plist::Array(shapes)) = ld.get_mut("shapes") else {
        return Ok(());
    };
    for (pi, corners) in path_corners {
        let path = &layer.paths[*pi];
        let p = pts(path);
        let n = p.len();
        let shape = shapes
            .get_mut(path.shape_idx)
            .ok_or("shape index out of range")?;
        let sd = as_dict_mut(shape, "path shape")?;
        let mut new_nodes: Vec<Plist> = Vec::with_capacity(n + corners.len() * 3);
        for i in 0..n {
            if !corners.contains(&i) {
                new_nodes.push(path.nodes[i].raw.clone());
                continue;
            }
            let (t, k) = tk(*pi, i);
            let a = p[(i + n - 1) % n];
            let b = p[i];
            let c = p[(i + 1) % n];
            let d1 = (b.0 - a.0, b.1 - a.1);
            let mut l1 = (d1.0 * d1.0 + d1.1 * d1.1).sqrt();
            if l1 == 0.0 {
                l1 = 1.0; // Python: `math.hypot(*d1) or 1.0`
            }
            let d2 = (c.0 - b.0, c.1 - b.1);
            let mut l2 = (d2.0 * d2.0 + d2.1 * d2.1).sqrt();
            if l2 == 0.0 {
                l2 = 1.0;
            }
            let u1 = (d1.0 / l1, d1.1 / l1);
            let u2 = (d2.0 / l2, d2.1 / l2);
            let t1 = (snap(b.0 - t * u1.0), snap(b.1 - t * u1.1));
            let t2 = (snap(b.0 + t * u2.0), snap(b.1 + t * u2.1));
            let h1 = (snap(t1.0 + k * u1.0), snap(t1.1 + k * u1.1));
            let h2 = (snap(t2.0 - k * u2.0), snap(t2.1 - k * u2.1));
            for (pos, kind) in [(t1, "ls"), (h1, "o"), (h2, "o"), (t2, "cs")] {
                new_nodes.push(Plist::Array(vec![
                    num_plist(pos.0),
                    num_plist(pos.1),
                    Plist::String(kind.into()),
                ]));
            }
        }
        sd.insert(SmolStr::from("nodes"), Plist::Array(new_nodes));
    }
    Ok(())
}
