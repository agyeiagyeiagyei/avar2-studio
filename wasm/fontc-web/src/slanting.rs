//! Slanting — source-level port of the desktop slant engine
//! (`avar2_studio/transforms/slanting.py`): build-time italics on a
//! `slnt` axis, applied to a .glyphs (format 3) source before fontc
//! compiles it, so the static demo builds the same font the desktop
//! studio does.
//!
//! Semantics mirror the Python engine rule for rule: shear about
//! y = x-height/2, LSB (node bounds) and advance preserved, x snapped
//! to the grid; per-master angles linear in mean advance between the
//! Narrowest/Widest dials with per-master overrides; brace layers
//! interpolate their angle on the parametric plane (fontdrasil's
//! VariationModel); every master gains a SLANTED twin at the axis end
//! (slnt = -max angle), deterministic ids throughout.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde::Deserialize;

use fontdrasil::coords::NormalizedLocation;
use fontdrasil::variations::{ModelDeltas, RoundingBehaviour, VariationModel};
use font_types::Tag;
use glyphs_reader::Plist;
use smol_str::SmolStr;

use crate::round_corners::{as_dict_mut, det_uuid, extend_axis, layer_coordinates, num_plist};

type Dict = BTreeMap<SmolStr, Plist>;

const PLANE_TAGS: [&str; 3] = ["XOPQ", "XTRA", "YOPQ"];

fn d_narrow() -> f64 { 11.0 }
fn d_wide() -> f64 { 6.0 }

#[derive(Deserialize)]
struct Params {
    #[serde(default = "d_narrow")]
    narrow_angle: f64,
    #[serde(default = "d_wide")]
    wide_angle: f64,
    #[serde(default)]
    master_angles: BTreeMap<String, serde_json::Value>,
}

fn json_f64(v: &serde_json::Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str().and_then(|s| s.trim().parse::<f64>().ok()))
}

struct MasterInfo {
    id: String,
    name: String,
    values: Vec<f64>,
    xheight: f64,
}

fn read_masters(root: &Dict) -> Result<Vec<MasterInfo>, String> {
    let metric_names: Vec<String> = root
        .get("metrics")
        .and_then(Plist::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|m| {
                    let d = m.as_dict()?;
                    d.get("type")
                        .or_else(|| d.get("name"))
                        .and_then(Plist::as_str)
                        .map(|s| s.to_string())
                })
                .collect()
        })
        .unwrap_or_default();
    let xh_idx = metric_names.iter().position(|n| n == "x-height");
    let mut out = Vec::new();
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
            let mut xheight = d.get("xHeight").and_then(Plist::as_f64).unwrap_or(0.0);
            if xheight == 0.0 {
                if let Some(i) = xh_idx {
                    xheight = d
                        .get("metricValues")
                        .and_then(Plist::as_array)
                        .and_then(|a| a.get(i))
                        .and_then(Plist::as_dict)
                        .and_then(|md| md.get("pos"))
                        .and_then(Plist::as_f64)
                        .unwrap_or(0.0);
                }
            }
            out.push(MasterInfo { id, name, values, xheight });
        }
    }
    if out.is_empty() {
        return Err("no fontMaster entries".into());
    }
    Ok(out)
}

fn mean_advances(root: &Dict, masters: &[MasterInfo]) -> HashMap<String, f64> {
    let mut sums: HashMap<String, (f64, u64)> = HashMap::new();
    let ids: HashSet<&str> = masters.iter().map(|m| m.id.as_str()).collect();
    if let Some(Plist::Array(glyphs)) = root.get("glyphs") {
        for glyph in glyphs {
            let Some(gd) = glyph.as_dict() else { continue };
            let Some(Plist::Array(layers)) = gd.get("layers") else { continue };
            for layer in layers {
                let Some(ld) = layer.as_dict() else { continue };
                let lid = ld.get("layerId").and_then(Plist::as_str).unwrap_or("");
                if !ids.contains(lid) {
                    continue;
                }
                // glyphsLib's GSLayer.width defaults to 600 when absent.
                let w = ld.get("width").and_then(Plist::as_f64).unwrap_or(600.0);
                let e = sums.entry(lid.to_string()).or_insert((0.0, 0));
                e.0 += w;
                e.1 += 1;
            }
        }
    }
    masters
        .iter()
        .map(|m| {
            let (s, n) = sums.get(&m.id).copied().unwrap_or((0.0, 0));
            (m.id.clone(), if n > 0 { s / n as f64 } else { 0.0 })
        })
        .collect()
}

fn master_angles(
    root: &Dict,
    masters: &[MasterInfo],
    params: &Params,
) -> Result<HashMap<String, f64>, String> {
    let names: HashSet<&str> = masters.iter().map(|m| m.name.as_str()).collect();
    let unknown: Vec<&str> = params
        .master_angles
        .keys()
        .map(String::as_str)
        .filter(|k| !names.contains(k))
        .collect();
    if !unknown.is_empty() {
        let mut valid: Vec<&str> = names.into_iter().collect();
        valid.sort_unstable();
        return Err(format!(
            "slant: master angle(s) name no master: {} (masters: {})",
            unknown.join(", "),
            valid.join(", ")
        ));
    }
    let means = mean_advances(root, masters);
    let lo = means.values().cloned().fold(f64::INFINITY, f64::min);
    let hi = means.values().cloned().fold(f64::NEG_INFINITY, f64::max);
    let span = hi - lo;
    let mut out = HashMap::new();
    for m in masters {
        let mut a = if span > 0.0 {
            let t = (means[&m.id] - lo) / span;
            params.narrow_angle + (params.wide_angle - params.narrow_angle) * t
        } else {
            params.narrow_angle
        };
        if let Some(v) = params.master_angles.get(&m.name).and_then(json_f64) {
            a = v;
        }
        out.insert(m.id.clone(), a.clamp(0.0, 45.0));
    }
    Ok(out)
}

struct AngleModel {
    tags: Vec<String>,
    origin: Vec<f64>,
    spans: Vec<(f64, f64)>,
    model: VariationModel,
    deltas: ModelDeltas<f64>,
}

impl AngleModel {
    fn norm(&self, j: usize, v: f64) -> f64 {
        let d = v - self.origin[j];
        let span = if d > 0.0 {
            self.spans[j].1 - self.origin[j]
        } else {
            self.origin[j] - self.spans[j].0
        };
        if span > 0.0 { d / span } else { 0.0 }
    }

    fn at(&self, loc: &HashMap<String, f64>) -> f64 {
        let pos: Vec<(String, f64)> = self
            .tags
            .iter()
            .enumerate()
            .map(|(j, tag)| {
                let v = loc.get(tag).copied().unwrap_or(self.origin[j]);
                (tag.clone(), self.norm(j, v).clamp(-1.0, 1.0))
            })
            .collect();
        let refs: Vec<(&str, f64)> = pos.iter().map(|(t, v)| (t.as_str(), *v)).collect();
        let nloc = NormalizedLocation::for_pos(&refs);
        self.model
            .interpolate_from_deltas(&nloc, &self.deltas)
            .first()
            .copied()
            .unwrap_or(0.0)
    }
}

fn angle_model(
    axis_tags: &[String],
    masters: &[MasterInfo],
    angles: &HashMap<String, f64>,
) -> Result<(Vec<(usize, String)>, AngleModel), String> {
    let plane: Vec<(usize, String)> = axis_tags
        .iter()
        .enumerate()
        .filter(|(_, t)| PLANE_TAGS.contains(&t.as_str()))
        .map(|(i, t)| (i, t.clone()))
        .collect();
    let mut order: Vec<Vec<f64>> = Vec::new();
    let mut vals: Vec<f64> = Vec::new();
    for m in masters {
        let loc: Vec<f64> = plane
            .iter()
            .map(|(i, _)| m.values.get(*i).copied().unwrap_or(0.0))
            .collect();
        if order.contains(&loc) {
            continue; // first master at a parametric point wins, like Python
        }
        order.push(loc);
        vals.push(angles[&m.id]);
    }
    let tags: Vec<String> = plane.iter().map(|(_, t)| t.clone()).collect();
    let origin = order[0].clone();
    let spans: Vec<(f64, f64)> = (0..tags.len())
        .map(|j| {
            let v: Vec<f64> = order.iter().map(|l| l[j]).collect();
            (v.iter().cloned().fold(f64::INFINITY, f64::min),
             v.iter().cloned().fold(f64::NEG_INFINITY, f64::max))
        })
        .collect();
    let tmp = AngleModel {
        tags: tags.clone(),
        origin: origin.clone(),
        spans: spans.clone(),
        model: VariationModel::empty(),
        deltas: Vec::new(),
    };
    let mut locations = HashSet::new();
    let mut seqs: HashMap<NormalizedLocation, Vec<f64>> = HashMap::new();
    for (i, loc) in order.iter().enumerate() {
        let pos: Vec<(String, f64)> = tags
            .iter()
            .enumerate()
            .map(|(j, t)| (t.clone(), tmp.norm(j, loc[j])))
            .collect();
        let refs: Vec<(&str, f64)> = pos.iter().map(|(t, v)| (t.as_str(), *v)).collect();
        let nloc = NormalizedLocation::for_pos(&refs);
        locations.insert(nloc.clone());
        seqs.insert(nloc, vec![vals[i]]);
    }
    let axis_order: Vec<Tag> = tags
        .iter()
        .map(|t| Tag::new_checked(t.as_bytes()).map_err(|e| format!("bad plane tag '{t}': {e}")))
        .collect::<Result<_, _>>()?;
    let model = VariationModel::new(locations, axis_order);
    let deltas = model
        .deltas_with_rounding::<f64, f64>(&seqs, RoundingBehaviour::None)
        .map_err(|e| format!("slant angle model failed: {e:?}"))?;
    Ok((plane, AngleModel { tags, origin, spans, model, deltas }))
}

/// Shear a twin layer dict about y = y0: all path nodes, anchors and
/// component positions; LSB (node bounds) restored, x snapped.
fn shear_layer_dict(ld: &mut Dict, tan_a: f64, y0: f64) -> Result<(), String> {
    // Pass 1: sheared node x's (floats) + bounds.
    let mut sheared: Vec<Vec<Option<(f64, f64)>>> = Vec::new();
    let mut min_before: Option<f64> = None;
    let mut min_after: Option<f64> = None;
    if let Some(Plist::Array(shapes)) = ld.get("shapes") {
        for shape in shapes {
            let Some(sd) = shape.as_dict() else {
                sheared.push(Vec::new());
                continue;
            };
            let Some(Plist::Array(nodes)) = sd.get("nodes") else {
                sheared.push(Vec::new());
                continue;
            };
            let mut row = Vec::with_capacity(nodes.len());
            for n in nodes {
                let Plist::Array(a) = n else {
                    return Err("glyphs 2 node strings are not supported in the browser — build with the desktop studio".into());
                };
                let x = a.first().and_then(Plist::as_f64).ok_or("bad node x")?;
                let y = a.get(1).and_then(Plist::as_f64).ok_or("bad node y")?;
                let nx = x + (y - y0) * tan_a;
                min_before = Some(min_before.map_or(x, |v: f64| v.min(x)));
                min_after = Some(min_after.map_or(nx, |v: f64| v.min(nx)));
                row.push(Some((nx, y)));
            }
            sheared.push(row);
        }
    }
    let shift = match (min_before, min_after) {
        (Some(b), Some(a)) => b - a,
        _ => 0.0,
    };
    // Pass 2: write back (node array extras preserved), components, anchors.
    if let Some(Plist::Array(shapes)) = ld.get_mut("shapes") {
        for (si, shape) in shapes.iter_mut().enumerate() {
            let Ok(sd) = as_dict_mut(shape, "shape") else { continue };
            if let Some(Plist::Array(nodes)) = sd.get_mut("nodes") {
                for (ni, n) in nodes.iter_mut().enumerate() {
                    if let (Plist::Array(a), Some(Some((nx, _y)))) =
                        (n, sheared.get(si).and_then(|r| r.get(ni)))
                    {
                        a[0] = num_plist((nx + shift + 0.5).floor());
                    }
                }
            } else if sd.contains_key("ref") {
                // A component: pos gains the translation compensation
                // dx += dy*tan (plus the LSB shift); its 2x2 stays as
                // drawn — the Glyphs component model has no shear term.
                let (dx, dy) = sd
                    .get("pos")
                    .and_then(Plist::as_array)
                    .map(|p| {
                        (p.first().and_then(Plist::as_f64).unwrap_or(0.0),
                         p.get(1).and_then(Plist::as_f64).unwrap_or(0.0))
                    })
                    .unwrap_or((0.0, 0.0));
                let nx = (dx + dy * tan_a + shift + 0.5).floor();
                sd.insert(
                    SmolStr::from("pos"),
                    Plist::Array(vec![num_plist(nx), num_plist(dy)]),
                );
            }
        }
    }
    if let Some(Plist::Array(anchors)) = ld.get_mut("anchors") {
        for anc in anchors.iter_mut() {
            let Ok(ad) = as_dict_mut(anc, "anchor") else { continue };
            let (x, y) = ad
                .get("pos")
                .and_then(Plist::as_array)
                .map(|p| {
                    (p.first().and_then(Plist::as_f64).unwrap_or(0.0),
                     p.get(1).and_then(Plist::as_f64).unwrap_or(0.0))
                })
                .unwrap_or((0.0, 0.0));
            let nx = (x + (y - y0) * tan_a + shift + 0.5).floor();
            ad.insert(
                SmolStr::from("pos"),
                Plist::Array(vec![num_plist(nx), num_plist(y)]),
            );
        }
    }
    Ok(())
}

/// Slant a .glyphs (format 3) source in place. Returns the transformed
/// source text and a summary line.
pub(crate) fn slant_source(source: &str, params_json: &str) -> Result<(String, String), String> {
    let params: Params =
        serde_json::from_str(params_json).map_err(|e| format!("bad slant params: {e}"))?;
    let mut plist = Plist::parse(source).map_err(|e| format!("could not parse .glyphs: {e}"))?;
    let root = as_dict_mut(&mut plist, ".glyphs root")?;

    let masters = read_masters(root)?;
    let angles = master_angles(root, &masters, &params)?;
    let max_angle = angles.values().cloned().fold(0.0f64, f64::max);
    if max_angle <= 0.0 {
        return Ok((source.to_string(), "slant: every angle is 0 — nothing to slant".into()));
    }
    let axis_tags: Vec<String> = root
        .get("axes")
        .and_then(Plist::as_array)
        .map(|axes| {
            axes.iter()
                .filter_map(|a| {
                    a.as_dict()
                        .and_then(|d| d.get("tag").or_else(|| d.get("Tag")))
                        .and_then(Plist::as_str)
                        .map(|s| s.to_string())
                })
                .collect()
        })
        .unwrap_or_default();
    let (plane, amodel) = angle_model(&axis_tags, &masters, &angles)?;
    let master_ids: HashSet<String> = masters.iter().map(|m| m.id.clone()).collect();
    let xheights: HashMap<String, f64> =
        masters.iter().map(|m| (m.id.clone(), m.xheight)).collect();
    let first_id = masters[0].id.clone();

    let mut twin_master_id: HashMap<String, String> = HashMap::new();
    extend_axis(root, "slnt", "Slant", -max_angle, "Slanted", &mut twin_master_id)?;

    let mut twins_count: u64 = 0;
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
        let Some(Plist::Array(layers)) = gd.get_mut("layers") else { continue };
        let mut twins: Vec<Plist> = Vec::new();
        for lp in layers.iter() {
            let Some(ld) = lp.as_dict() else { continue };
            let lid = ld
                .get("layerId")
                .and_then(Plist::as_str)
                .unwrap_or("")
                .to_string();
            let coords = layer_coordinates(ld);
            let is_master = master_ids.contains(&lid);
            if !is_master && coords.is_none() {
                continue; // backups stay single
            }
            let mut td = ld.clone();
            let (angle, y0) = if is_master {
                let tid = twin_master_id.get(&lid).ok_or("twin master missing")?;
                td.insert(SmolStr::from("layerId"), Plist::String(tid.clone().into()));
                // No associatedMasterId on a master twin — glyphs-reader
                // drops any layer carrying one as a backup (see the same
                // note in round_corners.rs).
                td.remove("associatedMasterId");
                (angles[&lid], xheights[&lid] / 2.0)
            } else {
                let tid = det_uuid(format!("{gname}:{lid}:slnt-twin").as_bytes());
                td.insert(SmolStr::from("layerId"), Plist::String(tid.into()));
                let assoc = ld
                    .get("associatedMasterId")
                    .and_then(Plist::as_str)
                    .unwrap_or("")
                    .to_string();
                if let Some(t) = twin_master_id.get(&assoc) {
                    td.insert(SmolStr::from("associatedMasterId"), Plist::String(t.clone().into()));
                }
                let c = coords.as_ref().unwrap();
                let key = if td.contains_key("attr") { "attr" } else { "attributes" };
                if let Some(Plist::Dictionary(ad)) = td.get_mut(key) {
                    if let Some(Plist::Array(cv)) = ad.get_mut("coordinates") {
                        if let Some(last) = cv.last_mut() {
                            *last = num_plist(-max_angle);
                        }
                    }
                }
                let loc: HashMap<String, f64> = plane
                    .iter()
                    .filter(|(i, _)| *i < c.len())
                    .map(|(i, t)| (t.clone(), c[*i]))
                    .collect();
                let a = amodel.at(&loc).clamp(0.0, 45.0);
                let y0m = xheights.get(&assoc).copied().unwrap_or(xheights[&first_id]);
                (a, y0m / 2.0)
            };
            shear_layer_dict(&mut td, angle.to_radians().tan(), y0)?;
            twins.push(Plist::Dictionary(td));
        }
        twins_count += twins.len() as u64;
        layers.extend(twins);
    }
    let summary = format!(
        "slant: slnt axis 0 to -{max_angle} over {} master(s), {twins_count} twin layers",
        masters.len()
    );
    Ok((plist.to_string(), summary))
}
