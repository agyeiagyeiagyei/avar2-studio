//! VF→VF fixer transforms — ports of the gftools fixers the full app
//! exposes as post-build transforms: `fix_unhinted` (gasp + prep for
//! smooth unhinted rendering) and `fix_instances` (rebuild the fvar
//! named-instance list per the Google Fonts instance spec).

use std::collections::{HashMap, HashSet};

use font_types::{Fixed, Tag};
use read_fonts::{FontRef, TableProvider};
use wasm_bindgen::JsError;
use write_fonts::from_obj::ToOwnedTable;
use write_fonts::tables::fvar as w_fvar;
use write_fonts::tables::name as w_name;

use crate::stat_registry::AXIS_REGISTRY;
use crate::{err, repack, TAG_FVAR, TAG_NAME};

const TAG_GASP: Tag = Tag::new(b"gasp");
const TAG_PREP: Tag = Tag::new(b"prep");
const TAG_FPGM: Tag = Tag::new(b"fpgm");

/// gftools `fix_unhinted_font`: add a gasp table that smooths at every
/// size and the unhinted prep program — unless the font carries an
/// fpgm (real hinting programs) or both tables already exist.
pub(crate) fn fix_unhinted(font_bytes: Vec<u8>) -> Result<Vec<u8>, JsError> {
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let has = |tag: Tag| font.data_for_tag(tag).is_some();
    if has(TAG_FPGM) || (has(TAG_GASP) && has(TAG_PREP)) {
        return Ok(font_bytes);
    }
    // gasp v1: a single range 0xFFFF → behavior 15 (all smoothing flags).
    let gasp: [u8; 8] = [0x00, 0x01, 0x00, 0x01, 0xFF, 0xFF, 0x00, 0x0F];
    // prep program: PUSHW[1] 511; SCANCTRL; PUSHB[1] 4; SCANTYPE
    // (opcode-encoded counts — 0xB8 = PUSHW[1], 0xB0 = PUSHB[1]).
    let prep: [u8; 7] = [0xB8, 0x01, 0xFF, 0x85, 0xB0, 0x04, 0x8D];
    let mut replacements = HashMap::new();
    replacements.insert(TAG_GASP, gasp.to_vec());
    replacements.insert(TAG_PREP, prep.to_vec());
    Ok(repack(&font, replacements))
}

/// gftools `fix_fvar_instances`: replace the fvar instance list with
/// one instance per in-range wght registry fallback (Thin…Black), each
/// at the axis defaults + its wght value. A font with a `slnt`/`ital`
/// axis gets the gftools doubling: the Roman set plus an Italic set
/// ("Thin Italic"…, "Regular Italic" → "Italic") pinned at that axis's
/// minValue; a family whose style name says Italic (and has no such
/// axis) gets the Italic names only. No-ops on fonts without an fvar
/// or without a wght axis.
pub(crate) fn fix_fvar_instances(font_bytes: Vec<u8>) -> Result<Vec<u8>, JsError> {
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let Ok(fvar) = font.fvar() else {
        return Ok(font_bytes);
    };
    let arrays = fvar
        .axis_instance_arrays()
        .map_err(|e| err(format!("invalid fvar: {e}")))?;
    let axes = arrays.axes();
    let Some(wght_idx) = axes.iter().position(|a| a.axis_tag() == Tag::new(b"wght")) else {
        return Ok(font_bytes);
    };

    let mut name: w_name::Name = font
        .name()
        .map_err(|e| err(format!("missing/invalid name table: {e}")))?
        .to_owned_table();
    let subfamily = crate::stat::first_debug_name(&name.name_record, &[22, 17, 2]).unwrap_or_default();
    let is_italic = subfamily.contains("Italic");
    // An ital/slnt axis doubles the instance list (axisregistry's
    // build_fvar_instances): the Roman set plus an Italic set pinned at
    // the axis minimum.
    let ital_idx = axes.iter().position(|a| a.axis_tag() == Tag::new(b"ital"));
    let slnt_idx = axes.iter().position(|a| a.axis_tag() == Tag::new(b"slnt"));
    let is_roman_and_italic = ital_idx.is_some() || slnt_idx.is_some();

    let wght = &axes[wght_idx];
    let (wmin, wmax) = (wght.min_value().to_f64(), wght.max_value().to_f64());
    let registry_wght = AXIS_REGISTRY
        .iter()
        .find(|a| a.tag == "wght")
        .ok_or_else(|| err("stat registry missing wght"))?;
    let fallbacks: Vec<_> = registry_wght
        .fallbacks
        .iter()
        .filter(|fb| fb.value >= wmin && fb.value <= wmax)
        .collect();
    if fallbacks.is_empty() {
        return Ok(font_bytes);
    }

    // STAT-shared name records must survive the old-instance cleanup.
    let mut protected: HashSet<u16> = [2u16, 17].into_iter().collect();
    if let Ok(stat) = font.stat() {
        if let Ok(design_axes) = stat.design_axes() {
            protected.extend(design_axes.iter().map(|a| a.axis_name_id().to_u16()));
        }
        if let Ok(Some(values)) = stat.offset_to_axis_values().transpose() {
            for value in values.axis_values().iter().flatten() {
                protected.insert(value.value_name_id().to_u16());
            }
        }
    }
    let mut fvar_owned: w_fvar::Fvar = fvar.to_owned_table();
    for inst in &fvar_owned.axis_instance_arrays.instances {
        let sf = inst.subfamily_name_id.to_u16();
        if !protected.contains(&sf) {
            name.name_record.retain(|r| r.name_id.to_u16() != sf);
        }
        if let Some(ps) = inst.post_script_name_id {
            let ps = ps.to_u16();
            if ps != 6 {
                name.name_record.retain(|r| r.name_id.to_u16() != ps);
            }
        }
    }

    let defaults: Vec<f64> = axes.iter().map(|a| a.default_value().to_f64()).collect();
    // Which sets to emit, mirroring axisregistry's branching: an
    // ital/slnt axis means Roman + Italic; an Italic style name alone
    // means Italic names only; otherwise Roman only.
    let sets: &[bool] = if is_roman_and_italic {
        &[false, true]
    } else if is_italic {
        &[true]
    } else {
        &[false]
    };
    let italic_pin = ital_idx.or(slnt_idx).map(|i| (i, axes[i].min_value().to_f64()));
    let mut instances = Vec::with_capacity(fallbacks.len() * sets.len());
    for &italic in sets {
        for fb in &fallbacks {
            let mut coords = defaults.clone();
            coords[wght_idx] = fb.value;
            let inst_name = if italic {
                if let Some((i, min)) = italic_pin {
                    coords[i] = min;
                }
                if fb.name == "Regular" {
                    "Italic".to_string()
                } else {
                    format!("{} Italic", fb.name)
                }
            } else {
                fb.name.to_string()
            };
            instances.push(w_fvar::InstanceRecord {
                subfamily_name_id: crate::stat::find_or_add_name(&mut name.name_record, &inst_name, 0),
                flags: 0,
                coordinates: coords.iter().map(|v| Fixed::from_f64(*v)).collect(),
                post_script_name_id: None,
            });
        }
    }
    fvar_owned.axis_instance_arrays.instances = instances;

    let mut replacements = HashMap::new();
    replacements.insert(TAG_FVAR, crate::dump_replacement(&fvar_owned, "fvar")?);
    replacements.insert(TAG_NAME, crate::dump_replacement(&name, "name")?);
    Ok(repack(&font, replacements))
}

/// Per-style rounding (round_corners): stamp each named instance's ROND
/// coordinate — the style's percent when `style_pcts` names it, the
/// broad `default_pct` otherwise. Mirrors the desktop studio's
/// RoundCornersTransform.apply().
pub(crate) fn stamp_rond_instances(
    font_bytes: Vec<u8>,
    default_pct: f64,
    style_pcts: &HashMap<String, f64>,
) -> Result<Vec<u8>, JsError> {
    // ROND runs 0-100, so the coordinate IS the percent.
    stamp_pct_instances(font_bytes, Tag::new(b"ROND"), default_pct, style_pcts, false)
}

pub(crate) fn stamp_slnt_instances(
    font_bytes: Vec<u8>,
    default_pct: f64,
    style_pcts: &HashMap<String, f64>,
) -> Result<Vec<u8>, JsError> {
    // slnt runs 0 to -maxAngle: the coordinate is min * pct / 100.
    stamp_pct_instances(font_bytes, Tag::new(b"slnt"), default_pct, style_pcts, true)
}

/// A style named "… Italic"/"… Slanted" is italic BY NAME: the percent
/// model never stamps it — it keeps its own slnt coordinate (gftools'
/// doubled Italic set sits at the axis minimum, i.e. fully slanted).
/// Mirrors the desktop SlantTransform.apply().
pub(crate) fn is_slant_exempt(subfamily: &str) -> bool {
    let lower = subfamily.to_lowercase();
    lower.contains("italic") || lower.contains("slanted")
}

/// Re-stamp one axis coordinate on every fvar named instance from a
/// per-style percent map (grade-style: style_pcts overrides, else the
/// default). `scale_by_min`: coordinate = axis minValue * pct/100
/// (slnt); otherwise the coordinate is the percent itself (ROND). For
/// slnt, styles named Italic/Slanted are exempt (is_slant_exempt).
fn stamp_pct_instances(
    font_bytes: Vec<u8>,
    tag: Tag,
    default_pct: f64,
    style_pcts: &HashMap<String, f64>,
    scale_by_min: bool,
) -> Result<Vec<u8>, JsError> {
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    let Ok(fvar) = font.fvar() else {
        return Ok(font_bytes);
    };
    let arrays = fvar
        .axis_instance_arrays()
        .map_err(|e| err(format!("invalid fvar: {e}")))?;
    let Some(axis_idx) = arrays
        .axes()
        .iter()
        .position(|a| a.axis_tag() == tag)
    else {
        return Ok(font_bytes);
    };
    let min_value = arrays.axes()[axis_idx].min_value().to_f64();
    let name: w_name::Name = font
        .name()
        .map_err(|e| err(format!("missing/invalid name table: {e}")))?
        .to_owned_table();
    let mut fvar_owned: w_fvar::Fvar = fvar.to_owned_table();
    for inst in &mut fvar_owned.axis_instance_arrays.instances {
        let sub = crate::stat::first_debug_name(&name.name_record, &[inst.subfamily_name_id.to_u16()])
            .unwrap_or_default();
        if scale_by_min && is_slant_exempt(&sub) {
            continue;
        }
        let pct = style_pcts
            .get(&sub)
            .copied()
            .unwrap_or(default_pct)
            .clamp(0.0, 100.0);
        let value = if scale_by_min { min_value * pct / 100.0 } else { pct };
        if let Some(c) = inst.coordinates.get_mut(axis_idx) {
            *c = Fixed::from_f64(value);
        }
    }
    let mut replacements = HashMap::new();
    replacements.insert(TAG_FVAR, crate::dump_replacement(&fvar_owned, "fvar")?);
    Ok(repack(&font, replacements))
}
