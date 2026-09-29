//! Source-level glyph data for the browser glyph editor (Phase 0): read a
//! glyph's layers out of a .glyphs source (`glyph_model`) and compile with
//! editor-authored brace-layer overlays spliced in (`compile_with_overlays`).
//!
//! Both work on glyphs-reader's `Plist` — parse, walk/mutate, serialize with
//! `Plist::to_string`, then compile through the same `Input::from_glyphs`
//! path as `compile_glyphs`. The typed `glyphs_reader::Font` is deliberately
//! not used: it drops layer names and its `preprocess()` rewrites shapes
//! (smart-component instantiation, corner components), while the editor
//! needs the source exactly as authored.
//!
//! Outlines use the studio's sidecar schema (control_axes.py
//! `_parts_to_outline` / `_outline_to_layer_data`):
//! `{"width": f64, "paths": [{"closed": bool, "nodes": [[x, y, type], ...]}],
//!   "components": [{"name": glyph, "transform": [xx, xy, yx, yy, dx, dy]}],
//!   "anchors": [{"name", "x", "y"}]}`. Node types are glyphsLib names
//! ("line"/"curve"/"offcurve"/"qcurve"); transforms are fontTools-order
//! 2x3 matrices. Missing keys are tolerated on read (node type defaults
//! to "line").

use std::collections::{BTreeMap, HashMap};
use std::fmt::Write as _;

use serde::Deserialize;
use wasm_bindgen::prelude::*;

use glyphs_reader::Plist;
use smol_str::SmolStr;

use crate::err;

type Dict = BTreeMap<SmolStr, Plist>;

// --------------------------------------------------------------------------
// Request JSON shapes
// --------------------------------------------------------------------------

#[derive(Deserialize)]
struct OverlayRequest {
    #[serde(default)]
    axes: Vec<AxisSpec>,
    #[serde(default)]
    overlays: Vec<Overlay>,
}

#[derive(Deserialize)]
struct AxisSpec {
    tag: String,
    name: Option<String>,
    min: f64,
    default: f64,
    max: f64,
}

#[derive(Deserialize)]
struct Overlay {
    glyph: String,
    location: HashMap<String, f64>,
    outline: Outline,
}

#[derive(Deserialize)]
struct Outline {
    width: Option<f64>,
    #[serde(default)]
    paths: Vec<OutlinePath>,
    #[serde(default)]
    components: Vec<OutlineComponent>,
    #[serde(default)]
    anchors: Vec<OutlineAnchor>,
}

#[derive(Deserialize)]
struct OutlinePath {
    /// glyphsLib's read default (`_outline_to_layer_data`) is True.
    closed: Option<bool>,
    #[serde(default)]
    nodes: Vec<OutlineNode>,
}

#[derive(Deserialize)]
struct OutlineComponent {
    name: String,
    transform: Option<[f64; 6]>,
}

#[derive(Deserialize)]
struct OutlineAnchor {
    name: String,
    x: Option<f64>,
    y: Option<f64>,
}

/// A sidecar node: `[x, y]` or `[x, y, type]` (type defaults to "line").
struct OutlineNode {
    x: f64,
    y: f64,
    kind: NodeKind,
}

impl<'de> Deserialize<'de> for OutlineNode {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> serde::de::Visitor<'de> for V {
            type Value = OutlineNode;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("a [x, y] or [x, y, type] node")
            }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> Result<Self::Value, A::Error> {
                let x: f64 = seq
                    .next_element()?
                    .ok_or_else(|| serde::de::Error::custom("node missing x"))?;
                let y: f64 = seq
                    .next_element()?
                    .ok_or_else(|| serde::de::Error::custom("node missing y"))?;
                let type_name: Option<String> = seq.next_element()?;
                let kind = match type_name.as_deref() {
                    None => NodeKind::Line,
                    Some(s) => NodeKind::from_sidecar(s).ok_or_else(|| {
                        serde::de::Error::custom(format!("unknown node type '{s}'"))
                    })?,
                };
                Ok(OutlineNode { x, y, kind })
            }
        }
        d.deserialize_seq(V)
    }
}

/// The four node kinds the sidecar schema carries (smoothness is not
/// represented, matching glyphsLib's `str(node.type)`).
#[derive(Clone, Copy)]
enum NodeKind {
    Line,
    Curve,
    OffCurve,
    QCurve,
}

impl NodeKind {
    fn from_sidecar(s: &str) -> Option<Self> {
        match s {
            "line" => Some(Self::Line),
            "curve" => Some(Self::Curve),
            "offcurve" => Some(Self::OffCurve),
            "qcurve" => Some(Self::QCurve),
            _ => None,
        }
    }

    /// The Glyphs 3 node letter (glyphs-reader NodeType::from_str).
    fn letter(self) -> &'static str {
        match self {
            Self::Line => "l",
            Self::Curve => "c",
            Self::OffCurve => "o",
            Self::QCurve => "q",
        }
    }
}

// --------------------------------------------------------------------------
// Shared plist reads
// --------------------------------------------------------------------------

struct FontAxis {
    tag: String,
    name: String,
}

struct MasterInfo {
    id: String,
    name: String,
    values: Vec<f64>,
}

fn font_axes(root: &Dict) -> Result<Vec<FontAxis>, JsError> {
    let axes = root
        .get("axes")
        .and_then(Plist::as_array)
        .ok_or_else(|| err("no axes array (Glyphs 3 sources only)"))?;
    axes.iter()
        .map(|a| {
            let d = a
                .as_dict()
                .ok_or_else(|| err("axis entry is not a dictionary"))?;
            let tag = d
                .get("tag")
                .or_else(|| d.get("Tag"))
                .and_then(Plist::as_str)
                .ok_or_else(|| err("axis entry without a tag"))?;
            let name = d
                .get("name")
                .or_else(|| d.get("Name"))
                .and_then(Plist::as_str)
                .unwrap_or(tag);
            Ok(FontAxis {
                tag: tag.to_string(),
                name: name.to_string(),
            })
        })
        .collect()
}

fn font_masters(root: &Dict) -> Result<Vec<MasterInfo>, JsError> {
    let masters = root
        .get("fontMaster")
        .and_then(Plist::as_array)
        .ok_or_else(|| err("no fontMaster array"))?;
    masters
        .iter()
        .map(|m| {
            let d = m
                .as_dict()
                .ok_or_else(|| err("fontMaster entry is not a dictionary"))?;
            let id = d
                .get("id")
                .and_then(Plist::as_str)
                .ok_or_else(|| err("fontMaster entry without an id"))?;
            let name = d.get("name").and_then(Plist::as_str).unwrap_or(id);
            let values = d
                .get("axesValues")
                .and_then(Plist::as_array)
                .map(|a| a.iter().map(|v| v.as_f64().unwrap_or(0.0)).collect())
                .unwrap_or_default();
            Ok(MasterInfo {
                id: id.to_string(),
                name: name.to_string(),
                values,
            })
        })
        .collect()
}

/// The "Variable Font Origin" custom parameter names the default master by
/// id (glyphs-reader `default_master_idx`); absent → first master.
fn default_master_idx(root: &Dict, masters: &[MasterInfo]) -> usize {
    if let Some(cps) = root.get("customParameters").and_then(Plist::as_array) {
        for cp in cps {
            let Some(d) = cp.as_dict() else { continue };
            if d.get("name").and_then(Plist::as_str) != Some("Variable Font Origin") {
                continue;
            }
            if let Some(id) = d.get("value").and_then(Plist::as_str) {
                if let Some(i) = masters.iter().position(|m| m.id == id) {
                    return i;
                }
            }
        }
    }
    0
}

/// (axis name, location) pairs from "Virtual Master" custom parameters —
/// they extend the axis ranges fontc derives (glyphs2fontir ir_axes).
fn virtual_master_locations(root: &Dict) -> Vec<(String, f64)> {
    let mut out = Vec::new();
    if let Some(cps) = root.get("customParameters").and_then(Plist::as_array) {
        for cp in cps {
            let Some(d) = cp.as_dict() else { continue };
            if d.get("name").and_then(Plist::as_str) != Some("Virtual Master") {
                continue;
            }
            if let Some(value) = d.get("value").and_then(Plist::as_array) {
                for entry in value {
                    let Some(ed) = entry.as_dict() else { continue };
                    if let (Some(name), Some(loc)) = (
                        ed.get("Axis").and_then(Plist::as_str),
                        ed.get("Location").and_then(Plist::as_f64),
                    ) {
                        out.push((name.to_string(), loc));
                    }
                }
            }
        }
    }
    out
}

/// A layer's brace coordinates (`attr.coordinates`), or None for master
/// layers and layers without them (the studio's brace rule).
fn layer_coordinates(layer: &Dict) -> Option<Vec<f64>> {
    let coords = layer
        .get("attr")?
        .as_dict()?
        .get("coordinates")?
        .as_array()?;
    if coords.is_empty() {
        return None;
    }
    coords.iter().map(Plist::as_f64).collect()
}

/// A Glyphs point value: `(x, y)` as an array (v3) or a string (v2).
fn point_value(p: Option<&Plist>) -> Result<(f64, f64), JsError> {
    match p {
        Some(Plist::Array(a)) if a.len() == 2 => {
            let x = a[0].as_f64().ok_or_else(|| err("bad point x"))?;
            let y = a[1].as_f64().ok_or_else(|| err("bad point y"))?;
            Ok((x, y))
        }
        Some(Plist::String(s)) => {
            let t = s.trim().trim_matches(['(', ')', '{', '}']);
            let mut parts = t.split(',');
            let (x, y) = (
                parts.next().and_then(|v| v.trim().parse::<f64>().ok()),
                parts.next().and_then(|v| v.trim().parse::<f64>().ok()),
            );
            match (x, y) {
                (Some(x), Some(y)) => Ok((x, y)),
                _ => Err(err(format!("bad point string '{s}'"))),
            }
        }
        _ => Ok((0.0, 0.0)),
    }
}

/// The glyphsLib sidecar name for a node type letter/name, dropping any
/// smooth suffix (as glyphsLib's `str(node.type)` does).
fn node_type_name(t: &str) -> Result<&'static str, JsError> {
    match t {
        "l" | "ls" | "LINE" | "LINE SMOOTH" => Ok("line"),
        "c" | "cs" | "CURVE" | "CURVE SMOOTH" => Ok("curve"),
        "o" | "OFFCURVE" => Ok("offcurve"),
        "q" | "qs" | "QCURVE" | "QCURVE SMOOTH" => Ok("qcurve"),
        _ => Err(err(format!("unknown node type '{t}'"))),
    }
}

/// cos/sin of a Glyphs 3 component angle (degrees), cardinal angles
/// snapped to exact values (glyphs-reader `normalized_rotation`).
fn snap_rotation(angle: f64) -> (f64, f64) {
    let a = angle.rem_euclid(360.0);
    if a == 0.0 {
        (0.0, 1.0)
    } else if a == 90.0 {
        (1.0, 0.0)
    } else if a == 180.0 {
        (0.0, -1.0)
    } else if a == 270.0 {
        (-1.0, 0.0)
    } else {
        a.to_radians().sin_cos()
    }
}

fn path_model(d: &Dict) -> Result<serde_json::Value, JsError> {
    // glyphs-reader RawShape: `closed` defaults to false when absent.
    let closed = d
        .get("closed")
        .and_then(Plist::as_i64)
        .map(|v| v != 0)
        .unwrap_or(false);
    let mut nodes = Vec::new();
    if let Some(arr) = d.get("nodes").and_then(Plist::as_array) {
        for n in arr {
            let (x, y, t) = match n {
                Plist::Array(a) if a.len() >= 3 => {
                    let x = a[0].as_f64().ok_or_else(|| err("bad node x"))?;
                    let y = a[1].as_f64().ok_or_else(|| err("bad node y"))?;
                    (x, y, a[2].as_str().unwrap_or("l"))
                }
                // v2 nodes are strings: "x y TYPE" (glyphs-reader
                // parse_node_from_string).
                Plist::String(s) => {
                    let mut parts = s.splitn(3, ' ');
                    let x = parts
                        .next()
                        .and_then(|v| v.parse::<f64>().ok())
                        .ok_or_else(|| err(format!("bad node string '{s}'")))?;
                    let y = parts
                        .next()
                        .and_then(|v| v.parse::<f64>().ok())
                        .ok_or_else(|| err(format!("bad node string '{s}'")))?;
                    let t = parts
                        .next()
                        .unwrap_or("l")
                        .split('{')
                        .next()
                        .unwrap()
                        .trim_end();
                    (x, y, t)
                }
                _ => return Err(err("malformed node")),
            };
            nodes.push(serde_json::json!([x, y, node_type_name(t)?]));
        }
    }
    Ok(serde_json::json!({"closed": closed, "nodes": nodes}))
}

/// A component as `{"name", "transform": [xx, xy, yx, yy, dx, dy]}`
/// (fontTools order, the sidecar convention).
fn component_model(d: &Dict) -> Result<serde_json::Value, JsError> {
    let name = d
        .get("ref")
        .or_else(|| d.get("name"))
        .and_then(Plist::as_str)
        .ok_or_else(|| err("component without a glyph reference"))?;
    let t = if let Some(s) = d.get("transform").and_then(Plist::as_str) {
        // v2: "{a, b, c, d, e, f}" in kurbo coefficient order (glyphs-
        // reader's Affine parser) → fontTools order.
        let vals: Vec<f64> = s
            .trim()
            .trim_matches(['{', '}'])
            .split(',')
            .map(|v| v.trim().parse::<f64>())
            .collect::<Result<_, _>>()
            .map_err(|_| err(format!("bad transform string '{s}'")))?;
        if vals.len() != 6 {
            return Err(err(format!("bad transform string '{s}'")));
        }
        [vals[0], vals[2], vals[1], vals[3], vals[4], vals[5]]
    } else {
        // v3: pos / angle / scale composed as glyphs-reader reads them:
        // translate(pos) ∘ rotate(angle) ∘ scale(sx, sy).
        let (dx, dy) = point_value(d.get("pos"))?;
        let angle = d.get("angle").and_then(Plist::as_f64).unwrap_or(0.0);
        let (sx, sy) = match d.get("scale").and_then(Plist::as_array) {
            Some(a) if a.len() == 2 => (a[0].as_f64().unwrap_or(1.0), a[1].as_f64().unwrap_or(1.0)),
            _ => (1.0, 1.0),
        };
        let (sin, cos) = snap_rotation(angle);
        // `+ 0.0` normalizes -0.0 → 0.0 so the JSON matches the desktop's.
        [
            sx * cos + 0.0,
            -sy * sin + 0.0,
            sx * sin + 0.0,
            sy * cos + 0.0,
            dx,
            dy,
        ]
    };
    Ok(serde_json::json!({"name": name, "transform": t}))
}

fn outline_model(layer: &Dict, width: f64) -> Result<serde_json::Value, JsError> {
    let mut paths = Vec::new();
    let mut components = Vec::new();
    if let Some(shapes) = layer.get("shapes").and_then(Plist::as_array) {
        for shape in shapes {
            let Some(d) = shape.as_dict() else { continue };
            // A shape with a glyph reference is a component (glyphs-reader
            // RawShape: `ref` in v3, `name` in v2); else a path.
            if d.get("ref").and_then(Plist::as_str).is_some()
                || (d.get("name").and_then(Plist::as_str).is_some() && !d.contains_key("nodes"))
            {
                components.push(component_model(d)?);
            } else {
                paths.push(path_model(d)?);
            }
        }
    } else {
        // Glyphs 2: paths and components in separate arrays.
        if let Some(arr) = layer.get("paths").and_then(Plist::as_array) {
            for p in arr {
                if let Some(d) = p.as_dict() {
                    paths.push(path_model(d)?);
                }
            }
        }
        if let Some(arr) = layer.get("components").and_then(Plist::as_array) {
            for c in arr {
                if let Some(d) = c.as_dict() {
                    components.push(component_model(d)?);
                }
            }
        }
    }
    let mut anchors = Vec::new();
    if let Some(arr) = layer.get("anchors").and_then(Plist::as_array) {
        for a in arr {
            let Some(d) = a.as_dict() else { continue };
            let name = d.get("name").and_then(Plist::as_str).unwrap_or("");
            let (x, y) = point_value(d.get("pos").or_else(|| d.get("position")))?;
            anchors.push(serde_json::json!({"name": name, "x": x, "y": y}));
        }
    }
    Ok(serde_json::json!({
        "width": width,
        "paths": paths,
        "components": components,
        "anchors": anchors,
    }))
}

fn layer_model(layer: &Dict) -> Result<serde_json::Value, JsError> {
    let layer_id = layer.get("layerId").and_then(Plist::as_str).unwrap_or("");
    let name = layer.get("name").and_then(Plist::as_str);
    let associated = layer.get("associatedMasterId").and_then(Plist::as_str);
    let coordinates = layer_coordinates(layer).unwrap_or_default();
    // glyphs-reader's default when the key is absent (RawLayer::build).
    let width = layer.get("width").and_then(Plist::as_f64).unwrap_or(600.0);
    Ok(serde_json::json!({
        "layerId": layer_id,
        "name": name,
        "associatedMasterId": associated,
        "coordinates": coordinates,
        "width": width,
        "outline": outline_model(layer, width)?,
    }))
}

/// Extract one glyph's source data (all layers: masters + brace layers)
/// plus the font's axes and masters, as JSON for the browser editor.
pub(crate) fn glyph_model(source: &str, glyph_name: &str) -> Result<String, JsError> {
    let plist = Plist::parse(source).map_err(|e| err(format!("could not parse .glyphs: {e}")))?;
    let root = plist
        .as_dict()
        .ok_or_else(|| err(".glyphs root is not a dictionary"))?;

    let axes = font_axes(root)?;
    let masters = font_masters(root)?;
    if masters.is_empty() {
        return Err(err("no fontMaster entries"));
    }
    let default_idx = default_master_idx(root, &masters);

    // Axis ranges: extremes over the masters, extended by Virtual Master
    // pins (glyphs2fontir ir_axes); the default is the default master's.
    let vms = virtual_master_locations(root);
    let mut axes_json = Vec::with_capacity(axes.len());
    for (i, axis) in axes.iter().enumerate() {
        let default = masters[default_idx].values.get(i).copied().unwrap_or(0.0);
        let mut min = default;
        let mut max = default;
        for v in masters
            .iter()
            .filter_map(|m| m.values.get(i))
            .copied()
            .chain(
                vms.iter()
                    .filter(|(name, _)| name == &axis.name)
                    .map(|(_, v)| *v),
            )
        {
            min = min.min(v);
            max = max.max(v);
        }
        axes_json.push(serde_json::json!({
            "tag": axis.tag,
            "name": axis.name,
            "min": min,
            "default": default,
            "max": max,
        }));
    }
    let masters_json: Vec<_> = masters
        .iter()
        .map(|m| serde_json::json!({"id": m.id, "name": m.name, "axesValues": m.values}))
        .collect();

    let glyphs = root
        .get("glyphs")
        .and_then(Plist::as_array)
        .ok_or_else(|| err("no glyphs array"))?;
    let glyph = glyphs
        .iter()
        .filter_map(Plist::as_dict)
        .find(|g| g.get("glyphname").and_then(Plist::as_str) == Some(glyph_name))
        .ok_or_else(|| err(format!("glyph '{glyph_name}' not found")))?;
    let mut layers_json = Vec::new();
    if let Some(layers) = glyph.get("layers").and_then(Plist::as_array) {
        for layer in layers {
            if let Some(ld) = layer.as_dict() {
                layers_json.push(layer_model(ld)?);
            }
        }
    }

    let model = serde_json::json!({
        "axes": axes_json,
        "masters": masters_json,
        "glyph": {"name": glyph_name, "layers": layers_json},
    });
    serde_json::to_string(&model).map_err(|e| err(format!("model json: {e}")))
}

// --------------------------------------------------------------------------
// compile_with_overlays: splice axes + brace layers at the Plist level
// --------------------------------------------------------------------------

/// Numbers go in as Integer when whole (the .glyphs house style), Float
/// otherwise — Plist's serializer prints Float via Rust's shortest
/// round-trip Display.
fn num_plist(v: f64) -> Plist {
    if v == v.trunc() && v.abs() < 9.0e15 {
        Plist::Integer(v as i64)
    } else {
        Plist::Float(v.into())
    }
}

/// The studio's brace-layer name (control_axes.py `_fmt_coord`):
/// "{x, y, ...}" with whole values bare.
fn brace_name(coords: &[f64]) -> String {
    let mut s = String::from("{");
    for (i, v) in coords.iter().enumerate() {
        if i > 0 {
            s.push_str(", ");
        }
        if v.fract() == 0.0 && v.abs() < 9.0e15 {
            write!(s, "{}", *v as i64).unwrap();
        } else {
            write!(s, "{v}").unwrap();
        }
    }
    s.push('}');
    s
}

fn fnv1a(seed: u64, bytes: &[u8]) -> u64 {
    let mut h = seed;
    for b in bytes {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

/// Brace layerId: a content hash (glyph, coordinates, outline) formatted
/// as a UUID. Deterministic, so recompiling an unchanged request gives
/// byte-identical output; the desktop studio's layers get random UUIDs
/// from glyphsLib instead.
fn brace_layer_id(glyph: &str, coords: &[f64], outline: &Outline) -> String {
    let mut buf = Vec::new();
    buf.extend_from_slice(glyph.as_bytes());
    buf.push(0xff);
    for c in coords {
        buf.extend_from_slice(&c.to_le_bytes());
    }
    buf.extend_from_slice(&outline.width.unwrap_or(0.0).to_le_bytes());
    for p in &outline.paths {
        buf.push(p.closed.unwrap_or(true) as u8);
        for n in &p.nodes {
            buf.extend_from_slice(&n.x.to_le_bytes());
            buf.extend_from_slice(&n.y.to_le_bytes());
            buf.extend_from_slice(n.kind.letter().as_bytes());
        }
    }
    for c in &outline.components {
        buf.extend_from_slice(c.name.as_bytes());
        for v in c.transform.unwrap_or([1.0, 0.0, 0.0, 1.0, 0.0, 0.0]) {
            buf.extend_from_slice(&v.to_le_bytes());
        }
    }
    for a in &outline.anchors {
        buf.extend_from_slice(a.name.as_bytes());
        buf.extend_from_slice(&a.x.unwrap_or(0.0).to_le_bytes());
        buf.extend_from_slice(&a.y.unwrap_or(0.0).to_le_bytes());
    }
    let h1 = fnv1a(0xcbf29ce484222325, &buf);
    let h2 = fnv1a(0x84222325cbf29ce4, &buf);
    let mut b = [0u8; 16];
    b[..8].copy_from_slice(&h1.to_be_bytes());
    b[8..].copy_from_slice(&h2.to_be_bytes());
    b[6] = (b[6] & 0x0f) | 0x40; // version 4
    b[8] = (b[8] & 0x3f) | 0x80; // variant
    let mut s = String::with_capacity(36);
    for (i, byte) in b.iter().enumerate() {
        if matches!(i, 4 | 6 | 8 | 10) {
            s.push('-');
        }
        write!(s, "{byte:02X}").unwrap();
    }
    s
}

fn outline_shapes(outline: &Outline) -> Plist {
    let mut shapes = Vec::new();
    for path in &outline.paths {
        let mut d = Dict::new();
        d.insert(
            SmolStr::from("closed"),
            Plist::Integer(if path.closed.unwrap_or(true) { 1 } else { 0 }),
        );
        d.insert(
            SmolStr::from("nodes"),
            Plist::Array(
                path.nodes
                    .iter()
                    .map(|n| {
                        Plist::Array(vec![
                            num_plist(n.x),
                            num_plist(n.y),
                            Plist::String(n.kind.letter().into()),
                        ])
                    })
                    .collect(),
            ),
        );
        shapes.push(Plist::Dictionary(d));
    }
    for comp in &outline.components {
        shapes.push(component_shape(comp));
    }
    Plist::Array(shapes)
}

/// A component as a Glyphs 3 shape dict (`ref` + pos/angle/scale when
/// non-default). The fontTools-order transform is decomposed as
/// translate ∘ rotate(θ) ∘ scale(sx, sy) — the inverse of glyphs-reader's
/// read. A sheared matrix loses its shear: the Glyphs 3 component model
/// has no shear term (the desktop studio has the same limit via
/// glyphsLib).
fn component_shape(comp: &OutlineComponent) -> Plist {
    let [xx, xy, yx, yy, dx, dy] = comp.transform.unwrap_or([1.0, 0.0, 0.0, 1.0, 0.0, 0.0]);
    let mut d = Dict::new();
    d.insert(SmolStr::from("ref"), Plist::String(comp.name.clone()));
    if dx != 0.0 || dy != 0.0 {
        d.insert(
            SmolStr::from("pos"),
            Plist::Array(vec![num_plist(dx), num_plist(dy)]),
        );
    }
    let sx = (xx * xx + yx * yx).sqrt();
    let mut sy = (xy * xy + yy * yy).sqrt();
    if xx * yy - xy * yx < 0.0 {
        sy = -sy;
    }
    let angle = if sx > 0.0 {
        yx.atan2(xx).to_degrees()
    } else {
        0.0
    };
    if angle != 0.0 {
        d.insert(SmolStr::from("angle"), num_plist(angle));
    }
    if sx != 1.0 || sy != 1.0 {
        d.insert(
            SmolStr::from("scale"),
            Plist::Array(vec![num_plist(sx), num_plist(sy)]),
        );
    }
    Plist::Dictionary(d)
}

fn outline_anchors(outline: &Outline) -> Plist {
    Plist::Array(
        outline
            .anchors
            .iter()
            .map(|a| {
                let mut d = Dict::new();
                d.insert(SmolStr::from("name"), Plist::String(a.name.clone()));
                d.insert(
                    SmolStr::from("pos"),
                    Plist::Array(vec![
                        num_plist(a.x.unwrap_or(0.0)),
                        num_plist(a.y.unwrap_or(0.0)),
                    ]),
                );
                Plist::Dictionary(d)
            })
            .collect(),
    )
}

/// Replace a layer's geometry (shapes, anchors, width) with the outline;
/// the identity keys (layerId, associatedMasterId, name, attr) stay.
fn set_layer_outline(layer: &mut Dict, outline: &Outline) {
    layer.insert(SmolStr::from("shapes"), outline_shapes(outline));
    if outline.anchors.is_empty() {
        layer.remove("anchors");
    } else {
        layer.insert(SmolStr::from("anchors"), outline_anchors(outline));
    }
    layer.insert(
        SmolStr::from("width"),
        num_plist(outline.width.unwrap_or(0.0)),
    );
}

/// A new brace layer dict, mirroring the studio's shadow writer
/// (control_axes.py `regenerate_shadow`): associated with the default
/// master, coordinates in `attr`, "{...}" positional name.
fn brace_layer(master_id: &str, coords: &[f64], outline: &Outline, glyph: &str) -> Plist {
    let mut d = Dict::new();
    d.insert(
        SmolStr::from("associatedMasterId"),
        Plist::String(master_id.into()),
    );
    let mut attr = Dict::new();
    attr.insert(
        SmolStr::from("coordinates"),
        Plist::Array(coords.iter().map(|v| num_plist(*v)).collect()),
    );
    d.insert(SmolStr::from("attr"), Plist::Dictionary(attr));
    d.insert(
        SmolStr::from("layerId"),
        Plist::String(brace_layer_id(glyph, coords, outline)),
    );
    d.insert(SmolStr::from("name"), Plist::String(brace_name(coords)));
    set_layer_outline(&mut d, outline);
    Plist::Dictionary(d)
}

fn as_dict_mut<'a>(p: &'a mut Plist, context: &str) -> Result<&'a mut Dict, JsError> {
    match p {
        Plist::Dictionary(d) => Ok(d),
        _ => Err(err(format!("{context}: expected a dictionary"))),
    }
}

/// Append the request's new axes to the font: axis declarations, master
/// axesValues extension (studio `regenerate_shadow`), padding of existing
/// brace coordinates, and Virtual Master pins giving each new axis a real
/// range (without one, all masters sit at the default and the axis is
/// zero-width — braces on it would land outside the box).
/// Returns the full axis list as (tags, names) in font order, plus the
/// appended axes' defaults.
fn splice_axes(
    root: &mut Dict,
    specs: &[AxisSpec],
) -> Result<(Vec<String>, Vec<String>, Vec<f64>), JsError> {
    let mut tags: Vec<String> = Vec::new();
    let mut names: Vec<String> = Vec::new();
    match root.get("axes") {
        Some(Plist::Array(axes)) => {
            for a in axes {
                let Some(d) = a.as_dict() else { continue };
                let tag = d
                    .get("tag")
                    .or_else(|| d.get("Tag"))
                    .and_then(Plist::as_str)
                    .ok_or_else(|| err("axis entry without a tag"))?;
                let name = d
                    .get("name")
                    .or_else(|| d.get("Name"))
                    .and_then(Plist::as_str)
                    .unwrap_or(tag);
                tags.push(tag.to_string());
                names.push(name.to_string());
            }
        }
        _ => return Err(err("no axes array (Glyphs 3 sources only)")),
    }

    let mut new: Vec<&AxisSpec> = Vec::new();
    for spec in specs {
        let known = tags.iter().any(|t| t.eq_ignore_ascii_case(&spec.tag))
            || new.iter().any(|s| s.tag.eq_ignore_ascii_case(&spec.tag));
        if !known {
            new.push(spec);
        }
    }
    let new_defaults: Vec<f64> = new.iter().map(|s| s.default).collect();

    if !new.is_empty() {
        let Some(Plist::Array(axes)) = root.get_mut("axes") else {
            return Err(err("axes is not an array"));
        };
        for spec in &new {
            let mut d = Dict::new();
            d.insert(
                SmolStr::from("name"),
                Plist::String(spec.name.clone().unwrap_or_else(|| spec.tag.clone())),
            );
            d.insert(SmolStr::from("tag"), Plist::String(spec.tag.clone()));
            axes.push(Plist::Dictionary(d));
            tags.push(spec.tag.clone());
            names.push(spec.name.clone().unwrap_or_else(|| spec.tag.clone()));
        }

        if let Some(Plist::Array(masters)) = root.get_mut("fontMaster") {
            for m in masters.iter_mut() {
                if let Plist::Dictionary(md) = m {
                    if let Some(Plist::Array(values)) = md.get_mut("axesValues") {
                        for spec in &new {
                            values.push(num_plist(spec.default));
                        }
                    }
                }
            }
        }

        // Pad existing brace coordinates with the new axes' defaults so
        // their vectors match the (grown) axis count (studio
        // `regenerate_shadow` does the same).
        if let Some(Plist::Array(glyphs)) = root.get_mut("glyphs") {
            for g in glyphs.iter_mut() {
                if let Plist::Dictionary(gd) = g {
                    if let Some(Plist::Array(layers)) = gd.get_mut("layers") {
                        for l in layers.iter_mut() {
                            if let Plist::Dictionary(ld) = l {
                                if let Some(Plist::Dictionary(attr)) = ld.get_mut("attr") {
                                    if let Some(Plist::Array(coords)) = attr.get_mut("coordinates")
                                    {
                                        if !coords.is_empty() {
                                            for spec in &new {
                                                coords.push(num_plist(spec.default));
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // Virtual Masters, one per extreme that differs from the default
        // (studio `regenerate_shadow`), built on the first master's
        // (extended) coordinates.
        let base: Option<Vec<f64>> = match root.get("fontMaster").and_then(Plist::as_array) {
            Some([Plist::Dictionary(first), ..]) => first
                .get("axesValues")
                .and_then(Plist::as_array)
                .map(|a| a.iter().map(|v| v.as_f64().unwrap_or(0.0)).collect()),
            _ => None,
        };
        if let Some(base) = base {
            let first_new = tags.len() - new.len();
            let mut vms = Vec::new();
            for (i, spec) in new.iter().enumerate() {
                let mut extremes = Vec::new();
                if spec.min < spec.default {
                    extremes.push(spec.min);
                }
                if spec.max > spec.default {
                    extremes.push(spec.max);
                }
                for extreme in extremes {
                    let mut point = base.clone();
                    point[first_new + i] = extreme;
                    let value: Vec<Plist> = names
                        .iter()
                        .zip(&point)
                        .map(|(n, v)| {
                            let mut d = Dict::new();
                            d.insert(SmolStr::from("Axis"), Plist::String(n.clone()));
                            d.insert(SmolStr::from("Location"), num_plist(*v));
                            Plist::Dictionary(d)
                        })
                        .collect();
                    let mut cp = Dict::new();
                    cp.insert(
                        SmolStr::from("name"),
                        Plist::String("Virtual Master".into()),
                    );
                    cp.insert(SmolStr::from("value"), Plist::Array(value));
                    vms.push(Plist::Dictionary(cp));
                }
            }
            if !vms.is_empty() {
                match root.get_mut("customParameters") {
                    Some(Plist::Array(cps)) => cps.extend(vms),
                    Some(_) => return Err(err("customParameters is not an array")),
                    None => {
                        root.insert(SmolStr::from("customParameters"), Plist::Array(vms));
                    }
                }
            }
        }
    }
    Ok((tags, names, new_defaults))
}

/// Compile a .glyphs source with editor overlays spliced in: new axes are
/// appended (masters extended, Virtual Masters pinned) and each overlay
/// replaces the brace layer at its coordinates — or inserts a new brace
/// layer on the default master — carrying the editor's outline.
pub(crate) fn compile_with_overlays(source: &str, request_json: &str) -> Result<Vec<u8>, JsError> {
    let request: OverlayRequest = serde_json::from_str(request_json)
        .map_err(|e| err(format!("bad overlay request JSON: {e}")))?;
    let mut plist =
        Plist::parse(source).map_err(|e| err(format!("could not parse .glyphs: {e}")))?;

    // Read the masters first (for the default master id + defaults), then
    // splice the axes.
    let (masters, default_idx) = {
        let root = plist
            .as_dict()
            .ok_or_else(|| err(".glyphs root is not a dictionary"))?;
        let masters = font_masters(root)?;
        if masters.is_empty() {
            return Err(err("no fontMaster entries"));
        }
        let default_idx = default_master_idx(root, &masters);
        (masters, default_idx)
    };
    let default_master_id = masters[default_idx].id.clone();

    let (tags, _names, new_defaults) = {
        let root = as_dict_mut(&mut plist, ".glyphs root")?;
        splice_axes(root, &request.axes)?
    };

    // Default location: the default master's coordinates, extended with
    // the new axes' defaults (splice_axes grew the file's axesValues but
    // not our read-only copy).
    let mut defaults = masters[default_idx].values.clone();
    defaults.extend(new_defaults);

    for overlay in &request.overlays {
        // Sparse tag-keyed location → full positional vector in font axis
        // order (missing tag → that axis's default).
        let mut target = defaults.clone();
        for (tag, value) in &overlay.location {
            let idx = tags
                .iter()
                .position(|t| t.eq_ignore_ascii_case(tag))
                .ok_or_else(|| {
                    err(format!(
                        "overlay on '{}': unknown axis '{tag}'",
                        overlay.glyph
                    ))
                })?;
            target[idx] = *value;
        }

        let root = as_dict_mut(&mut plist, ".glyphs root")?;
        let glyphs = match root.get_mut("glyphs") {
            Some(Plist::Array(g)) => g,
            _ => return Err(err("no glyphs array")),
        };
        let glyph = glyphs
            .iter_mut()
            .find(|g| g.get("glyphname").and_then(Plist::as_str) == Some(overlay.glyph.as_str()))
            .ok_or_else(|| {
                err(format!(
                    "overlay glyph '{}' not in the source",
                    overlay.glyph
                ))
            })?;
        let gd = as_dict_mut(glyph, "glyph")?;
        let layers = match gd.get_mut("layers") {
            Some(Plist::Array(l)) => l,
            Some(_) => {
                return Err(err(format!(
                    "glyph '{}': layers is not an array",
                    overlay.glyph
                )))
            }
            None => {
                gd.insert(SmolStr::from("layers"), Plist::Array(Vec::new()));
                match gd.get_mut("layers") {
                    Some(Plist::Array(l)) => l,
                    _ => unreachable!(),
                }
            }
        };
        // Replace the layer at these coordinates, else insert a new brace.
        let mut replaced = false;
        for l in layers.iter_mut() {
            let coords = match l {
                Plist::Dictionary(ld) => layer_coordinates(ld),
                _ => None,
            };
            if coords.as_deref() == Some(target.as_slice()) {
                if let Plist::Dictionary(ld) = l {
                    set_layer_outline(ld, &overlay.outline);
                }
                replaced = true;
                break;
            }
        }
        if !replaced {
            layers.push(brace_layer(
                &default_master_id,
                &target,
                &overlay.outline,
                &overlay.glyph,
            ));
        }
    }

    let spliced = plist.to_string();
    let input = fontc::Input::from_glyphs(spliced);
    let source = input
        .create_source()
        .map_err(|e| JsError::new(&e.to_string()))?;
    let options = fontc::Options::default();
    fontc::generate_font(source, options).map_err(|e| JsError::new(&e.to_string()))
}
