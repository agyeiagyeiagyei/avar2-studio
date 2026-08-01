//! STAT table regeneration: rebuild a variable font's STAT table from
//! its fvar, so avar2-added user axes get Google-Fonts-ready records.
//!
//! Port of `axisregistry.build_stat(ttFont, [])` — the single-font,
//! no-siblings case of gftools `gen_stat_tables` — on top of fontTools
//! `buildStatTable(ttFont, axes, macNames=False)` with the default
//! `elidedFallbackName=2`. Per fvar axis that exists in the GF axis
//! registry (stat_registry.rs), the STAT gets one axis record (registry
//! display name, ordering = position) plus one Format 1 axis value
//! record per registry fallback inside the axis range; the fallback at
//! the registry default is flagged elidable (0x2), and wght 400 / ital 0
//! become Format 3 records linked to 700 / 1 when the link target is
//! also in range. Style tokens in the family/subfamily names that match
//! a registry fallback (e.g. "Regular" when wght is not an fvar axis)
//! add a single-value axis record. Axes absent from the GF registry get
//! no STAT records (the Python rule: warn and skip).

use std::collections::{HashMap, HashSet};

use font_types::{Fixed, NameId, Tag};
use read_fonts::{FontRef, TableProvider};
use wasm_bindgen::JsError;
use write_fonts::from_obj::ToOwnedTable;
use write_fonts::tables::name as w_name;
use write_fonts::tables::stat as w_stat;
use write_fonts::tables::stat::AxisValueTableFlags;

use crate::stat_registry::{AxisFallback, RegistryAxis, AXIS_REGISTRY};
use crate::{dump_replacement, err, repack, TAG_AVAR, TAG_NAME};

const TAG_STAT: Tag = Tag::new(b"STAT");

pub(crate) fn regen_stat(font_bytes: Vec<u8>) -> Result<Vec<u8>, JsError> {
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
    // axisregistry: assert is_variable(ttFont), "not a VF!"
    let fvar = font
        .fvar()
        .map_err(|e| err(format!("not a variable font (no fvar): {e}")))?;
    let fvar_arrays = fvar
        .axis_instance_arrays()
        .map_err(|e| err(format!("invalid fvar: {e}")))?;
    let fvar_axes: Vec<(String, f64, f64)> = fvar_arrays
        .axes()
        .iter()
        .map(|a| {
            (
                a.axis_tag().to_string(),
                a.min_value().to_f64(),
                a.max_value().to_f64(),
            )
        })
        .collect();

    // NameIDs that must survive the old-STAT cleanup: 0..=25 plus fvar
    // axis and instance (subfamily) name IDs.
    let mut keep: HashSet<u16> = (0..=25u16).collect();
    keep.extend(
        fvar_arrays
            .axes()
            .iter()
            .map(|a| a.axis_name_id().to_u16()),
    );
    for inst in fvar_arrays.instances().iter() {
        let inst = inst.map_err(|e| err(format!("invalid fvar instance: {e}")))?;
        keep.insert(inst.subfamily_name_id.to_u16());
    }

    let mut name: w_name::Name = font
        .name()
        .map_err(|e| err(format!("missing/invalid name table: {e}")))?
        .to_owned_table();

    // Style tokens are read from the ORIGINAL name table: build_stat
    // collects fallbacks_in_name_table before removing old STAT names.
    let family = first_debug_name(&name.name_record, &[21, 16, 1])
        .ok_or_else(|| err("no family name (nameID 21/16/1) in name table"))?;
    let subfamily = first_debug_name(&name.name_record, &[22, 17, 2])
        .ok_or_else(|| err("no subfamily name (nameID 22/17/2) in name table"))?;

    // ---- Remove the old STAT's name records (the table itself is
    // dropped by not carrying it into the repack replacements). --------
    if let Ok(stat) = font.stat() {
        let mut delete_ids: HashSet<u16> = HashSet::new();
        if let Ok(axes) = stat.design_axes() {
            for axis in axes {
                let id = axis.axis_name_id().to_u16();
                if !keep.contains(&id) {
                    delete_ids.insert(id);
                }
            }
        }
        if let Some(values) = stat.offset_to_axis_values().transpose().ok().flatten() {
            for value in values.axis_values().iter().flatten() {
                let id = value.value_name_id().to_u16();
                if !keep.contains(&id) {
                    delete_ids.insert(id);
                }
            }
        }
        name.name_record
            .retain(|r| !delete_ids.contains(&r.name_id.to_u16()));
    }

    // ---- fallbacks_in_fvar: registry fallbacks within each fvar
    // axis's range, in fvar order. Axes not in the GF registry are
    // skipped (Python logs a warning); axes with no in-range fallback
    // produce no STAT record (defaultdict: no key without an append).
    let mut fvar_fallbacks: Vec<(&'static RegistryAxis, Vec<&'static AxisFallback>)> = Vec::new();
    for (tag, min, max) in &fvar_axes {
        let Some(rax) = registry_axis(tag) else {
            continue;
        };
        let fallbacks: Vec<_> = rax
            .fallbacks
            .iter()
            .filter(|f| f.value >= *min && f.value <= *max)
            .collect();
        if fallbacks.is_empty() {
            continue;
        }
        fvar_fallbacks.push((rax, fallbacks));
    }

    // ---- fallbacks_in_name_table: style tokens (family name minus its
    // first word, then subfamily words) matching a registry fallback
    // whose axis is not an fvar axis; equal fallbacks are added once.
    let fvar_tags: HashSet<&str> = fvar_axes.iter().map(|(t, _, _)| t.as_str()).collect();
    let mut name_fallbacks: Vec<(&'static RegistryAxis, &'static AxisFallback)> = Vec::new();
    for token in family
        .split_whitespace()
        .skip(1)
        .chain(subfamily.split_whitespace())
    {
        let Some((rax, fb)) = get_fallback(token) else {
            continue;
        };
        if fvar_tags.contains(rax.tag)
            || name_fallbacks
                .iter()
                .any(|(_, f)| f.name == fb.name && f.value == fb.value)
        {
            continue;
        }
        name_fallbacks.push((rax, fb));
    }

    // ---- buildStatTable: axis records (ordering = position) + value
    // records, names via _addName (windows-only, reuse-or-append).
    let elidable = AxisValueTableFlags::ELIDABLE_AXIS_VALUE_NAME;
    let mut design_axes: Vec<w_stat::AxisRecord> = Vec::new();
    let mut axis_values: Vec<w_stat::AxisValue> = Vec::new();
    let mut seen_axes: HashSet<&str> = HashSet::new();

    for (rax, fallbacks) in &fvar_fallbacks {
        seen_axes.insert(rax.tag);
        let axis_index = add_axis_record(&mut name.name_record, &mut design_axes, rax)?;
        for fb in fallbacks {
            let flags = if fb.value == rax.default_value {
                elidable
            } else {
                AxisValueTableFlags::empty()
            };
            let name_id = find_or_add_name(&mut name.name_record, fb.name, 0);
            // LINKED_VALUES, kept only when the link target is also a
            // fallback of this axis (e.g. wght 400 → 700 needs both in
            // range).
            let linked = linked_value(rax.tag, fb.value)
                .filter(|lv| fallbacks.iter().any(|f| f.value == *lv));
            axis_values.push(axis_value(axis_index, flags, name_id, fb.value, linked));
        }
    }

    for (rax, fb) in &name_fallbacks {
        if !seen_axes.insert(rax.tag) {
            continue;
        }
        let axis_index = add_axis_record(&mut name.name_record, &mut design_axes, rax)?;
        let name_id = find_or_add_name(&mut name.name_record, fb.name, 0);
        // build_stat links name-table values unconditionally.
        let linked = linked_value(rax.tag, fb.value);
        axis_values.push(axis_value(
            axis_index,
            AxisValueTableFlags::empty(),
            name_id,
            fb.value,
            linked,
        ));
    }

    let stat = w_stat::Stat::new(design_axes, axis_values, NameId::new(2));
    let mut stat_bytes = write_fonts::dump_table(&stat).map_err(|e| err(format!("STAT: {e}")))?;
    // write-fonts always emits STAT 1.2; fontTools buildStatTable emits
    // 1.1 when there are no Format 4 records (always the case here —
    // build_stat passes no `locations`). Same header layout; patch down.
    stat_bytes[0..4].copy_from_slice(&[0, 1, 0, 1]);

    // fontTools names.sort(): stable, by (platform, encoding, language,
    // nameID) — the string is not part of the key.
    name.name_record.sort_by(|a, b| {
        (a.platform_id, a.encoding_id, a.language_id, a.name_id.to_u16()).cmp(&(
            b.platform_id,
            b.encoding_id,
            b.language_id,
            b.name_id.to_u16(),
        ))
    });

    let mut replacements = HashMap::new();
    replacements.insert(TAG_NAME, dump_replacement(&name, "name")?);
    replacements.insert(TAG_STAT, stat_bytes);
    // repack drops avar when it is not among the replacements (the
    // avar2 path always rebuilds it); carry it through verbatim here.
    if let Some(data) = font.data_for_tag(TAG_AVAR) {
        replacements.insert(TAG_AVAR, data.as_bytes().to_vec());
    }
    Ok(repack(&font, replacements))
}

fn registry_axis(tag: &str) -> Option<&'static RegistryAxis> {
    AXIS_REGISTRY.iter().find(|a| a.tag == tag)
}

/// AxisRegistry.get_fallback: the first axis in registry order with a
/// fallback of this name (registry order is the Python package's — see
/// stat_registry.rs).
fn get_fallback(name: &str) -> Option<(&'static RegistryAxis, &'static AxisFallback)> {
    AXIS_REGISTRY
        .iter()
        .find_map(|a| a.fallbacks.iter().find(|f| f.name == name).map(|f| (a, f)))
}

/// axisregistry LINKED_VALUES: wght Regular → Bold, ital Roman → Italic.
fn linked_value(axis: &str, value: f64) -> Option<f64> {
    if axis == "wght" && value == 400.0 {
        Some(700.0)
    } else if axis == "ital" && value == 0.0 {
        Some(1.0)
    } else {
        None
    }
}

/// fontTools `getDebugName`: the first English record ((platform 1,
/// lang 0) or (platform 3, lang 0x409)) in table order wins; otherwise
/// the last record carrying the id; empty strings count as missing.
/// (to_owned_table pre-decodes strings, so every record is decodable.)
fn debug_name(records: &[w_name::NameRecord], name_id: u16) -> Option<String> {
    let mut english: Option<String> = None;
    let mut some: Option<String> = None;
    for r in records.iter().filter(|r| r.name_id.to_u16() == name_id) {
        some = Some(r.string.to_string());
        if (r.platform_id == 1 && r.language_id == 0)
            || (r.platform_id == 3 && r.language_id == 0x409)
        {
            english = Some(r.string.to_string());
            break;
        }
    }
    english
        .filter(|s| !s.is_empty())
        .or(some)
        .filter(|s| !s.is_empty())
}

/// fontTools `getFirstDebugName` (getBestFamilyName/getBestSubFamilyName).
fn first_debug_name(records: &[w_name::NameRecord], ids: &[u16]) -> Option<String> {
    ids.iter().find_map(|id| debug_name(records, *id))
}

/// fontTools `_addName({en: string}, minNameID, windows=True,
/// mac=False)`: reuse the smallest nameID >= min_id with a matching
/// (3,1,0x409) record; else assign `_findUnusedNameID` (1 + max of all
/// nameIDs and 255) and append a (3,1,0x409) record.
fn find_or_add_name(records: &mut Vec<w_name::NameRecord>, string: &str, min_id: u16) -> NameId {
    let reuse = records
        .iter()
        .filter(|r| {
            r.platform_id == 3
                && r.encoding_id == 1
                && r.language_id == 0x409
                && r.name_id.to_u16() >= min_id
                && r.string.as_str() == string
        })
        .map(|r| r.name_id.to_u16())
        .min();
    if let Some(id) = reuse {
        return NameId::new(id);
    }
    let new_id = records
        .iter()
        .map(|r| r.name_id.to_u16())
        .max()
        .unwrap_or(0)
        .max(255)
        + 1;
    records.push(w_name::NameRecord::new(
        3,
        1,
        0x409,
        NameId::new(new_id),
        string.to_string().into(),
    ));
    NameId::new(new_id)
}

/// Push an axis record with the registry display name (`_addName` with
/// minNameID=256); ordering is the record's position.
fn add_axis_record(
    records: &mut Vec<w_name::NameRecord>,
    design_axes: &mut Vec<w_stat::AxisRecord>,
    rax: &RegistryAxis,
) -> Result<u16, JsError> {
    let tag = Tag::new_checked(rax.tag.as_bytes())
        .map_err(|e| err(format!("bad registry axis tag '{}': {e}", rax.tag)))?;
    let name_id = find_or_add_name(records, rax.display_name, 256);
    let ordering = design_axes.len() as u16;
    design_axes.push(w_stat::AxisRecord::new(tag, name_id, ordering));
    Ok(ordering)
}

/// Format 1 (single location) or Format 3 (location + linked value).
fn axis_value(
    axis_index: u16,
    flags: AxisValueTableFlags,
    name_id: NameId,
    value: f64,
    linked: Option<f64>,
) -> w_stat::AxisValue {
    let value = Fixed::from_f64(value);
    match linked {
        Some(lv) => w_stat::AxisValue::format_3(axis_index, flags, name_id, value, Fixed::from_f64(lv)),
        None => w_stat::AxisValue::format_1(axis_index, flags, name_id, value),
    }
}
