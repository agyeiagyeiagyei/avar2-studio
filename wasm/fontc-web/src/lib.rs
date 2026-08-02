//! fontc → WASM: compile a .glyphs source (as an in-memory string) to
//! TTF bytes in the browser. Part of the avar2-studio GitHub Pages
//! migration (docs/migration-github-pages.md, Phase 2).
//!
//! JS side: `compile_glyphs(source: string) -> Uint8Array` (throws on
//! compiler error).
//!
//! Also exports `add_avar2(font_bytes, mappings_csv)`: adds parametric
//! (avar2) axes to a compiled variable TTF, mirroring
//! `fontTools.varLib._add_avar` + `gftools.scripts.gen_avar2`.

use std::collections::{HashMap, HashSet};
use std::str::FromStr;

use wasm_bindgen::prelude::*;

use font_types::{F2Dot14, Fixed, NameId, Tag};
use fontdrasil::coords::{NormalizedCoord, NormalizedLocation};
use fontdrasil::variations::{Tent, VariationModel};
use read_fonts::{FontRef, TableProvider};
use write_fonts::from_obj::ToOwnedTable;
use write_fonts::tables::avar as w_avar;
use write_fonts::tables::fvar as w_fvar;
use write_fonts::tables::gdef as w_gdef;
use write_fonts::tables::hvar as w_hvar;
use write_fonts::tables::mvar as w_mvar;
use write_fonts::tables::name as w_name;
use write_fonts::tables::variations::ivs_builder::VariationStoreBuilder;
use write_fonts::tables::variations::{
    DeltaSetIndexMap, ItemVariationStore, RegionAxisCoordinates, VariationRegion,
};
use write_fonts::FontBuilder;

mod braces;
mod measure;
mod spac;
mod stat;
mod stat_registry;

pub use measure::measure_at;

#[wasm_bindgen]
pub fn compile_glyphs(source: String) -> Result<Vec<u8>, JsError> {
    let input = fontc::Input::from_glyphs(source);
    let source = input
        .create_source()
        .map_err(|e| JsError::new(&e.to_string()))?;
    let options = fontc::Options::default();
    fontc::generate_font(source, options).map_err(|e| JsError::new(&e.to_string()))
}

/// Apply the bundle's post-build transforms (the SPAC injectors,
/// `transforms.transforms` with `enabled: true`) to a compiled variable
/// font: SPAC fvar axis + gvar phantom tuples + rebuilt HVAR
/// (see spac.rs; ported from gftools gen-spac and the studio's
/// width-aware variant).
///
/// `transforms_json` is the bundle's `transforms.transforms` array;
/// `avar2_csv` is the bundle's avar2 mappings CSV (a SPAC column, when
/// present, pins per-instance SPAC coordinates).
#[wasm_bindgen]
pub fn apply_transforms(
    font_bytes: Vec<u8>,
    transforms_json: &str,
    avar2_csv: &str,
) -> Result<Vec<u8>, JsError> {
    spac::apply_transforms(font_bytes, transforms_json, avar2_csv)
}

/// Add control (secondary parametric) axes from a config bundle to a
/// compiled variable font: one fvar axis per entry, and a computed gvar
/// brace tuple per layer (see braces.rs).
///
/// `control_json` is the bundle's `control_axes.axes` array.
#[wasm_bindgen]
pub fn apply_control_axes(font_bytes: Vec<u8>, control_json: &str) -> Result<Vec<u8>, JsError> {
    braces::apply_control_axes(font_bytes, control_json)
}

/// Add the GRAD grade axis from a config bundle: fvar axis (−10/0/+10)
/// plus equalised light/dark brace tuples per graded instance
/// (see braces.rs; model ported from grade.py / grade_shadow.py).
///
/// `grade_json` is the bundle's `grade` object; `instance_coords_json`
/// maps instance name → its base parametric coords `{XTRA, XOPQ, YOPQ}`.
#[wasm_bindgen]
pub fn apply_grade(
    font_bytes: Vec<u8>,
    grade_json: &str,
    instance_coords_json: &str,
) -> Result<Vec<u8>, JsError> {
    braces::apply_grade(font_bytes, grade_json, instance_coords_json)
}

const TAG_AVAR: Tag = Tag::new(b"avar");
const TAG_FVAR: Tag = Tag::new(b"fvar");
const TAG_GDEF: Tag = Tag::new(b"GDEF");
const TAG_GVAR: Tag = Tag::new(b"gvar");
const TAG_HVAR: Tag = Tag::new(b"HVAR");
const TAG_MVAR: Tag = Tag::new(b"MVAR");
const TAG_NAME: Tag = Tag::new(b"name");

fn err(msg: impl std::fmt::Display) -> JsError {
    #[cfg(not(target_arch = "wasm32"))]
    eprintln!("fontc-web err: {}", msg);
    JsError::new(&msg.to_string())
}

/// fontTools `fl2fi(v, 14)`: `otRound(v * 16384)` where `otRound(x) =
/// floor(x + 0.5)`.
fn fl2fi(v: f64) -> i32 {
    (v * 16384.0 + 0.5).floor() as i32
}

/// fontTools `models.normalizeValue`: piecewise-linear normalization to
/// -1..1 with the default at 0.
fn normalize(v: f64, min: f64, default: f64, max: f64) -> f64 {
    if v == default {
        0.0
    } else if v < default {
        (v - default) / (default - min)
    } else {
        (v - default) / (max - default)
    }
}

/// One parsed CSV row: normalized input (in-axis) and output (out-axis)
/// locations. Empty cells are omitted, matching gftools.
struct MappingRow {
    input: NormalizedLocation,
    input_values: HashMap<Tag, f64>,
    output_values: HashMap<Tag, f64>,
}

/// A new fvar axis declared by a CSV column that is not already an fvar
/// axis (gftools `gen_fvar_axes`: default == min, unless the optional
/// axis-metadata JSON overrides it).
struct NewAxis {
    tag: Tag,
    /// Column header, used for the axis-metadata lookup.
    col_name: String,
    /// Name-table string: the axis tag (gen_avar2 semantics), or the
    /// authored display_name when axis metadata provides one.
    name: String,
    min: f64,
    default: f64,
    max: f64,
}

/// Column-name → registered axis tag (csv_io.normalize_in_axis_name):
/// traditional axes map onto lowercase registered tags; anything else
/// (custom columns, parametric tags) stays verbatim.
fn normalize_in_axis_name(name: &str) -> &str {
    match name.to_ascii_uppercase().as_str() {
        "WGHT" => "wght",
        "WDTH" => "wdth",
        "OPSZ" => "opsz",
        "CONTRAST" | "CNTR" => "cntr",
        _ => name,
    }
}

/// Add new user axes + an avar v2 table (and the supporting VarStore
/// padding) to a compiled variable font, mirroring
/// `gftools.scripts.gen_avar2.gen_avar2_mapping`.
///
/// `mappings_csv` has a first "Instance Name" column (ignored); the
/// remaining column headers are axis tags. Columns that already exist in
/// fvar are output (parametric) axes; the rest become new fvar axes.
/// Empty cells mean "axis default" and are dropped from the locations.
///
/// Returns the font with updated fvar/name/avar/gvar/HVAR/GDEF/MVAR,
/// repacked as a valid TTF.
///
/// `axis_metadata_json` (optional) is a JSON object
/// `{TAG: {min, default, max}}` overriding the CSV-derived range for
/// new user axes (the studio's avar2-axis-metadata.json semantics).
/// `parametric_tags_json` (optional) overrides the in/out split when
/// re-generating onto a font whose fvar already carries user axes
/// (the default-location rebuild): columns matching these tags are
/// outputs, everything else is an input.
#[wasm_bindgen]
pub fn add_avar2(font_bytes: Vec<u8>, mappings_csv: &str, axis_metadata_json: Option<String>, parametric_tags_json: Option<String>) -> Result<Vec<u8>, JsError> {
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;

    // Existing fvar axes, in font order: tag + (min, default, max).
    let mut fvar: w_fvar::Fvar = font
        .fvar()
        .map_err(|e| err(format!("missing/invalid fvar: {e}")))?
        .to_owned_table();
    let existing_axes: Vec<(Tag, f64, f64, f64)> = fvar
        .axis_instance_arrays
        .axes
        .iter()
        .map(|a| {
            (
                a.axis_tag,
                a.min_value.to_f64(),
                a.default_value.to_f64(),
                a.max_value.to_f64(),
            )
        })
        .collect();
    let existing_tags: HashSet<Tag> = existing_axes.iter().map(|a| a.0).collect();

    // ---- Parse the CSV ------------------------------------------------
    let csv = mappings_csv.trim_start_matches('\u{feff}');
    let mut lines = csv.lines().filter(|l| !l.trim().is_empty());
    let header_line = lines.next().ok_or_else(|| err("empty mappings CSV"))?;
    let headers: Vec<&str> = header_line.split(',').map(str::trim).collect();
    if headers.len() < 2 {
        return Err(err("mappings CSV needs an instance column plus axis columns"));
    }
    let col_names: Vec<&str> = headers[1..].to_vec();
    let mut col_tags = Vec::with_capacity(col_names.len());
    for name in &col_names {
        col_tags.push(
            Tag::from_str(name).map_err(|e| err(format!("bad axis tag '{name}': {e}")))?,
        );
    }
    // rows[i][c] = value of axis column c in row i (None = empty cell)
    let mut csv_rows: Vec<Vec<Option<f64>>> = Vec::new();
    for line in lines {
        let cells: Vec<&str> = line.split(',').collect();
        let mut row = Vec::with_capacity(col_tags.len());
        for (c, name) in col_names.iter().enumerate() {
            let cell = cells.get(c + 1).map(|s| s.trim()).unwrap_or("");
            row.push(if cell.is_empty() {
                None
            } else {
                Some(cell.parse::<f64>().map_err(|e| {
                    err(format!("bad value '{cell}' for axis '{name}': {e}"))
                })?)
            });
        }
        csv_rows.push(row);
    }
    if csv_rows.is_empty() {
        return Err(err("mappings CSV has no instance rows"));
    }

    // Studio in/out classification (config_generator.validate_csv_structure):
    // a column is an avar2 INPUT when its normalized tag differs from the
    // column name (registered traditional axis) or it is not an fvar axis
    // at all; otherwise it is a parametric OUTPUT column. When regenerating
    // onto a font that already carries user axes (default-location rebuild),
    // parametric_tags_json supplies the OUTPUT set explicitly instead.
    let output_tags: HashSet<Tag> = match parametric_tags_json.as_deref() {
        Some(json) => serde_json::from_str::<Vec<String>>(json)
            .map_err(|e| err(format!("invalid parametric tags JSON: {e}")))?
            .iter()
            .map(|t| Tag::from_str(t).map_err(|e| err(format!("bad tag '{t}': {e}"))))
            .collect::<Result<_, JsError>>()?,
        None => existing_tags.clone(),
    };
    let col_axis: Vec<(Tag, Tag, bool)> = col_tags
        .iter()
        .map(|&raw| {
            let axis = Tag::from_str(normalize_in_axis_name(&raw.to_string()).as_ref())
                .map_err(|e| err(format!("bad axis tag '{raw}': {e}")))?;
            let is_input = axis != raw || !output_tags.contains(&raw);
            Ok((raw, axis, is_input))
        })
        .collect::<Result<_, JsError>>()?;

    // ---- New (input) axes ---------------------------------------------
    // Axis order and ranges follow gftools gen_fvar_axes: axes appear in
    // order of first non-empty cell (rows top-to-bottom, columns
    // left-to-right); min/max span that column's non-empty values.
    let mut new_axes: Vec<NewAxis> = Vec::new();
    for row in &csv_rows {
        for (c, cell) in row.iter().enumerate() {
            let (_, axis_tag, is_input) = col_axis[c];
            if cell.is_none()
                || !is_input
                || existing_tags.contains(&axis_tag)
                || new_axes.iter().any(|a| a.tag == axis_tag)
            {
                continue;
            }
            let (mut min, mut max) = (f64::MAX, f64::MIN);
            for r in &csv_rows {
                if let Some(v) = r[c] {
                    min = min.min(v);
                    max = max.max(v);
                }
            }
            new_axes.push(NewAxis {
                tag: axis_tag,
                col_name: col_names[c].to_string(),
                // gen_avar2 names new axes by their tag; an authored
                // display_name (axis metadata) wins when present.
                name: axis_tag.to_string(),
                min,
                default: min, // gftools: default == min (metadata may override below)
                max,
            });
        }
    }

    // Optional axis-metadata overrides: explicit min/default/max for
    // declared user axes, replacing the CSV-derived (min, default=min,
    // max) — the studio's avar2-axis-metadata.json semantics.
    if let Some(json) = axis_metadata_json.as_deref() {
        let meta: serde_json::Value = serde_json::from_str(json)
            .map_err(|e| err(format!("invalid axis metadata JSON: {e}")))?;
        if let Some(obj) = meta.as_object() {
            for a in new_axes.iter_mut() {
                // Metadata is keyed by CSV column header (uppercase).
                if let Some(m) = obj.get(&a.col_name) {
                    let get = |k: &str| m.get(k).and_then(serde_json::Value::as_f64);
                    if let Some(v) = get("min") {
                        a.min = v;
                    }
                    if let Some(v) = get("default") {
                        a.default = v;
                    }
                    if let Some(v) = get("max") {
                        a.max = v;
                    }
                    if let Some(dn) = m.get("display_name").and_then(serde_json::Value::as_str) {
                        if !dn.is_empty() {
                            a.name = dn.to_string();
                        }
                    }
                }
            }
        }
    }

    // Normalization triples per axis tag: existing axes from fvar, new
    // axes (min, default=min, max) from the CSV.
    let mut triples: HashMap<Tag, (f64, f64, f64)> = existing_axes
        .iter()
        .map(|(tag, min, default, max)| (*tag, (*min, *default, *max)))
        .collect();
    for a in &new_axes {
        triples.insert(a.tag, (a.min, a.default, a.max));
    }

    // ---- Normalized mapping rows --------------------------------------
    // Zero-valued entries are dropped right away: fontTools'
    // VariationModel does `{k: v for k, v in loc.items() if v != 0.0}`
    // on every input location before use, so an explicit `OPSZ=<min>`
    // cell and an empty cell are the same thing.
    let mut rows: Vec<MappingRow> = Vec::with_capacity(csv_rows.len() + 1);
    for csv_row in &csv_rows {
        let mut input = NormalizedLocation::new();
        let mut input_values = HashMap::new();
        let mut output_values = HashMap::new();
        for (c, cell) in csv_row.iter().enumerate() {
            let Some(v) = cell else { continue };
            let (raw_tag, axis_tag, is_input) = col_axis[c];
            let (min, default, max) = triples[if is_input { &axis_tag } else { &raw_tag }];
            let nv = normalize(*v, min, default, max);
            if is_input {
                if nv != 0.0 {
                    input_values.insert(axis_tag, nv);
                    input.insert(axis_tag, NormalizedCoord::new(nv));
                }
            } else {
                output_values.insert(raw_tag, nv);
            }
        }
        rows.push(MappingRow {
            input,
            input_values,
            output_values,
        });
    }
    // If the base master is missing, insert it (empty in/out) at zero.
    // (fontTools checks "any location with all-zero values"; with zeros
    // stripped above, that is any row with an empty input location.)
    if !rows.iter().any(|r| r.input_values.is_empty()) {
        rows.insert(
            0,
            MappingRow {
                input: NormalizedLocation::new(),
                input_values: HashMap::new(),
                output_values: HashMap::new(),
            },
        );
    }
    // fontTools' VariationModel raises "Locations must be unique" when
    // two rows share a (zero-stripped) input location — which
    // CrispyMini-avar.csv genuinely does (e.g. "Ultra Wide Thin 144"
    // with empty OPSZ/WGHT vs "Ultra Wide Thin 12" with OPSZ=12: both
    // are {WDTH:max}). Instead of erroring, dedup with Python
    // dict-overwrite semantics: the LAST row for an input location
    // wins, keyed in first-occurrence order.
    let mut winner: HashMap<NormalizedLocation, usize> = HashMap::new();
    let mut order: Vec<NormalizedLocation> = Vec::new();
    for (i, r) in rows.iter().enumerate() {
        if !winner.contains_key(&r.input) {
            order.push(r.input.clone());
        }
        winner.insert(r.input.clone(), i);
    }
    let mut rows_opt: Vec<Option<MappingRow>> = rows.into_iter().map(Some).collect();
    let mut rows: Vec<MappingRow> = Vec::with_capacity(order.len());
    for loc in &order {
        rows.push(rows_opt[winner[loc]].take().expect("each index taken once"));
    }

    // Final axis order: existing fvar axes (font order) + new axes.
    let mut axis_order: Vec<Tag> = existing_axes.iter().map(|a| a.0).collect();
    axis_order.extend(new_axes.iter().map(|a| a.tag));
    let total_axes = axis_order.len() as u16;

    // ---- Variation model + item variation store -----------------------
    let locations: HashSet<NormalizedLocation> =
        rows.iter().map(|r| r.input.clone()).collect();
    let model = VariationModel::new(locations, axis_order.clone());

    let mut store_builder = VariationStoreBuilder::new(total_axes);
    let mut temp_ids = Vec::with_capacity(axis_order.len());
    for tag in &axis_order {
        // Master value per location pair: vo[tag] - vi[tag] (0 when the
        // tag is absent from the output location), as 14-bit fixed.
        let mut point_seqs: HashMap<NormalizedLocation, Vec<f64>> = HashMap::new();
        for r in &rows {
            let master = match r.output_values.get(tag) {
                Some(vo) => fl2fi(vo - r.input_values.get(tag).copied().unwrap_or(0.0)) as f64,
                None => 0.0,
            };
            point_seqs.insert(r.input.clone(), vec![master]);
        }
        let deltas = model
            .deltas::<f64, f64>(&point_seqs)
            .map_err(|e| err(format!("variation model failed for axis {tag}: {e:?}")))?;
        let zeroes = Tent::zeroes();
        let mut axis_deltas = Vec::new();
        for (region, values) in deltas {
            if region.is_default() {
                continue;
            }
            let region_axes = axis_order
                .iter()
                .map(|t| region.get(t).unwrap_or(&zeroes).to_region_axis_coords())
                .collect();
            axis_deltas.push((VariationRegion { region_axes }, values[0] as i32));
        }
        temp_ids.push(store_builder.add_deltas(axis_deltas));
    }
    let (var_store, varidx_map) = store_builder.build();
    let axis_index_map: DeltaSetIndexMap = temp_ids
        .iter()
        .map(|id| {
            varidx_map
                .get(*id)
                .unwrap_or_else(|| panic!("missing remap for {id:?}"))
        })
        .collect();

    let avar = w_avar::Avar {
        axis_segment_maps: vec![w_avar::SegmentMaps::default(); total_axes as usize],
        axis_index_map: Some(axis_index_map).into(),
        var_store: Some(var_store).into(),
    };

    // ---- fvar: append new axes, grow instance coordinates -------------
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
    // gftools addMultilingualName(minNameID=256): first free id >= 256.
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
        for a in &new_axes {
            inst.coordinates.push(Fixed::from_f64(a.default));
        }
    }

    // ---- gvar/HVAR/GDEF/MVAR axis-count surgery ------------------------
    // gvar: write-fonts has no full gvar roundtrip, so patch the binary
    // directly. gftools just sets `font["gvar"].axisCount`, but fontTools
    // then RECOMPILES the whole table from decompiled form, padding every
    // tuple coordinate record with 0.0 for the new axes. This does the
    // equivalent byte-level rewrite (a 0.0 coordinate means the axis does
    // not participate in the tuple).
    let gvar_bytes = match font.data_for_tag(TAG_GVAR) {
        Some(d) => Some(patch_gvar(d.as_bytes(), total_axes, &HashMap::new())?),
        None => None,
    };

    let mut replacements: HashMap<Tag, Vec<u8>> = grown_varstore_replacements(&font, total_axes)?;

    let fvar_bytes = write_fonts::dump_table(&fvar).map_err(|e| err(format!("fvar: {e}")))?;
    let name_bytes = write_fonts::dump_table(&name).map_err(|e| err(format!("name: {e}")))?;
    let avar_bytes = write_fonts::dump_table(&avar).map_err(|e| err(format!("avar: {e}")))?;

    // ---- Repack the sfnt -----------------------------------------------
    replacements.insert(TAG_FVAR, fvar_bytes);
    replacements.insert(TAG_NAME, name_bytes);
    replacements.insert(TAG_AVAR, avar_bytes);
    if let Some(b) = gvar_bytes {
        replacements.insert(TAG_GVAR, b);
    }
    Ok(repack(&font, replacements))
}

/// Rebuild the font's STAT table from its fvar, mirroring
/// `axisregistry.build_stat(ttFont, [])` (gftools gen_stat_tables for a
/// single font with no siblings): GF axis-registry fallbacks per fvar
/// axis, registry elidable defaults, linked values (wght 400→700,
/// ital 0→1), and style-token axes from the family/subfamily names.
/// Avar2-added user axes (registered ones like opsz/wght/wdth and GF
/// customs like XOPQ/GRAD) get Google-Fonts-ready STAT records; axes
/// absent from the GF registry are skipped. See stat.rs.
#[wasm_bindgen]
pub fn regen_stat(font_bytes: Vec<u8>) -> Result<Vec<u8>, JsError> {
    stat::regen_stat(font_bytes)
}

/// HVAR/GDEF/MVAR VarStores grown to `total_axes`: bump the region-list
/// axis count and pad every region with a null tent
/// {start: -1, peak: 0, end: 1} per added axis (gftools gen_fvar_axes).
fn grown_varstore_replacements(
    font: &FontRef,
    total_axes: u16,
) -> Result<HashMap<Tag, Vec<u8>>, JsError> {
    let mut replacements = HashMap::new();
    if let Ok(hvar) = font.hvar() {
        let mut hvar: w_hvar::Hvar = hvar.to_owned_table();
        pad_var_store(&mut hvar.item_variation_store, total_axes);
        replacements.insert(TAG_HVAR, dump_replacement(&hvar, "HVAR")?);
    }
    if let Ok(gdef) = font.gdef() {
        let mut gdef: w_gdef::Gdef = gdef.to_owned_table();
        if let Some(store) = gdef.item_var_store.as_mut() {
            pad_var_store(store, total_axes);
        }
        replacements.insert(TAG_GDEF, dump_replacement(&gdef, "GDEF")?);
    }
    if let Ok(mvar) = font.mvar() {
        let mut mvar: w_mvar::Mvar = mvar.to_owned_table();
        if let Some(store) = mvar.item_variation_store.as_mut() {
            pad_var_store(store, total_axes);
        }
        replacements.insert(TAG_MVAR, dump_replacement(&mvar, "MVAR")?);
    }
    Ok(replacements)
}

/// Grow a VarStore's region list to `total_axes`, padding every region
/// with a null tent per added axis (a null tent never alters the
/// scalar, so variation behavior along the new axes is unchanged).
fn pad_var_store(store: &mut ItemVariationStore, total_axes: u16) {
    let null_tent = RegionAxisCoordinates::new(
        F2Dot14::from_f32(-1.0),
        F2Dot14::from_f32(0.0),
        F2Dot14::from_f32(1.0),
    );
    store.variation_region_list.axis_count = total_axes;
    for region in store.variation_region_list.variation_regions.iter_mut() {
        while region.region_axes.len() < total_axes as usize {
            region.region_axes.push(null_tent.clone());
        }
    }
}

fn dump_replacement<T>(table: &T, context: &str) -> Result<Vec<u8>, JsError>
where
    T: write_fonts::FontWrite + write_fonts::validate::Validate,
{
    write_fonts::dump_table(table).map_err(|e| err(format!("{context}: {e}")))
}

/// Repack the sfnt with `replacements` swapped in. FontBuilder
/// recomputes offsets, per-table checksums, the search parameters and
/// head.checkSumAdjustment. Any replacement whose tag the font lacks is
/// appended.
fn repack(font: &FontRef, mut replacements: HashMap<Tag, Vec<u8>>) -> Vec<u8> {
    let mut builder = FontBuilder::new();
    for record in font.table_directory().table_records() {
        let tag = record.tag();
        if tag == TAG_AVAR && !replacements.contains_key(&TAG_AVAR) {
            continue; // replaced by the freshly built avar below
        }
        if let Some(bytes) = replacements.remove(&tag) {
            builder.add_raw(tag, bytes);
        } else if let Some(data) = font.data_for_tag(tag) {
            builder.add_raw(tag, data.as_bytes());
        }
    }
    for (tag, bytes) in replacements {
        builder.add_raw(tag, bytes);
    }
    builder.build()
}

// Tuple variation header flags (gvar / OT TupleVariation).
const GVAR_TUPLE_COUNT_MASK: u16 = 0x0FFF;
const GVAR_EMBEDDED_PEAK_TUPLE: u16 = 0x8000;
const GVAR_INTERMEDIATE_REGION: u16 = 0x4000;
const GVAR_PRIVATE_POINT_NUMBERS: u16 = 0x2000;
// tupleVariationCount flag (glyph variation data level).
const GVAR_TUPLES_SHARE_POINT_NUMBERS: u16 = 0x8000;
// Packed-delta run header flags.
const DELTAS_ARE_WORDS: u8 = 0x40;
const DELTA_RUN_COUNT_MASK: u8 = 0x3F;
// Packed point-number run flags.
const POINTS_ARE_WORDS: u8 = 0x80;
const POINT_RUN_COUNT_MASK: u8 = 0x7F;

/// Byte length of a packed point-numbers blob (fontTools
/// `decompilePoints_` without materializing the list — only the
/// consumed length matters for slicing the tuple data section).
fn packed_points_len(data: &[u8]) -> Result<usize, JsError> {
    let mut pos = 0usize;
    let first = *data
        .get(pos)
        .ok_or_else(|| gvar_err("packed points truncated"))?;
    pos += 1;
    let mut count = (first & POINT_RUN_COUNT_MASK) as usize;
    if first & POINTS_ARE_WORDS != 0 {
        count = (count << 8)
            | *data
                .get(pos)
                .ok_or_else(|| gvar_err("packed points truncated"))? as usize;
        pos += 1;
    }
    if count == 0 {
        return Ok(pos); // all points
    }
    let mut seen = 0usize;
    while seen < count {
        let run = *data
            .get(pos)
            .ok_or_else(|| gvar_err("packed points run truncated"))?;
        pos += 1;
        let n = (run & POINT_RUN_COUNT_MASK) as usize + 1;
        pos += n * if run & POINTS_ARE_WORDS != 0 { 2 } else { 1 };
        seen += n;
    }
    if pos > data.len() {
        return Err(gvar_err("packed points run out of range"));
    }
    Ok(pos)
}

/// A new gvar tuple to inject into a glyph's variation data
/// (brace-layer effect; built by braces.rs). Coordinates are per-axis
/// normalized values for the FULL (post-grow) axis list; zeros mark
/// non-participating axes. `start`/`end` are written as an intermediate
/// region when they differ from the peak's inferred region
/// (fontTools `compileIntermediateCoord`).
pub(crate) struct NewTuple {
    pub peak: Vec<f64>,
    pub start: Vec<f64>,
    pub end: Vec<f64>,
    /// (x, y) per point, including the 4 phantom points.
    pub deltas: Vec<(i16, i16)>,
}

impl NewTuple {
    /// Serialize as (tuple header, serialized delta data), mirroring
    /// fontTools `TupleVariation.compile`: embedded peak, private point
    /// numbers (a single 0 byte = all points), deltas as int16 runs.
    fn serialize(&self) -> (Vec<u8>, Vec<u8>) {
        let mut aux = vec![0u8]; // all points in the glyph
        pack_deltas(&mut aux, self.deltas.iter().map(|d| d.0));
        pack_deltas(&mut aux, self.deltas.iter().map(|d| d.1));

        let intermediate = self.peak.iter().enumerate().any(|(i, &p)| {
            let (s, e) = (self.start[i], self.end[i]);
            (s, e) != (p.min(0.0), p.max(0.0))
        });
        let mut flags = GVAR_EMBEDDED_PEAK_TUPLE | GVAR_PRIVATE_POINT_NUMBERS;
        let mut coords = Vec::with_capacity(self.peak.len() * 2);
        for &p in &self.peak {
            coords.extend_from_slice(&(fl2fi(p) as i16).to_be_bytes());
        }
        if intermediate {
            flags |= GVAR_INTERMEDIATE_REGION;
            for &s in &self.start {
                coords.extend_from_slice(&(fl2fi(s) as i16).to_be_bytes());
            }
            for &e in &self.end {
                coords.extend_from_slice(&(fl2fi(e) as i16).to_be_bytes());
            }
        }
        let mut header = Vec::with_capacity(4 + coords.len());
        header.extend_from_slice(&(aux.len() as u16).to_be_bytes());
        header.extend_from_slice(&flags.to_be_bytes());
        header.extend_from_slice(&coords);
        (header, aux)
    }
}

/// Packed delta values as int16 runs of ≤ 64 (DELTAS_ARE_WORDS).
fn pack_deltas(out: &mut Vec<u8>, deltas: impl Iterator<Item = i16>) {
    let deltas: Vec<i16> = deltas.collect();
    for chunk in deltas.chunks((DELTA_RUN_COUNT_MASK + 1) as usize) {
        out.push(DELTAS_ARE_WORDS | (chunk.len() as u8 - 1));
        for d in chunk {
            out.extend_from_slice(&d.to_be_bytes());
        }
    }
}

fn gvar_err(context: &str) -> JsError {
    err(format!("gvar table malformed: {context}"))
}

fn be_u16(bytes: &[u8], off: usize, context: &str) -> Result<u16, JsError> {
    bytes
        .get(off..off + 2)
        .map(|s| u16::from_be_bytes([s[0], s[1]]))
        .ok_or_else(|| gvar_err(context))
}

fn be_u32(bytes: &[u8], off: usize, context: &str) -> Result<u32, JsError> {
    bytes
        .get(off..off + 4)
        .map(|s| u32::from_be_bytes([s[0], s[1], s[2], s[3]]))
        .ok_or_else(|| gvar_err(context))
}

/// Rewrite `gvar` for `total_axes` axes: bump `axisCount` and pad every
/// tuple coordinate record (shared tuples, embedded peak tuples and
/// intermediate start/end tuples in glyph variation data) with 0.0
/// (F2DOT14) per added axis, shifting offsets accordingly. Then append
/// `extras` (gid → new tuples) to the glyphs' variation data.
///
/// This mirrors what gftools' `font["gvar"].axisCount = n` produces:
/// fontTools recompiles gvar from decompiled form, writing every tuple's
/// coordinates for the full (grown) axis list with missing axes at 0.0.
/// A zero coordinate means the tuple does not participate in that axis,
/// so glyph variation behavior along the new axes is unchanged.
fn patch_gvar(
    bytes: &[u8],
    total_axes: u16,
    extras: &HashMap<u16, Vec<NewTuple>>,
) -> Result<Vec<u8>, JsError> {
    if bytes.len() < 20 {
        return Err(gvar_err("header truncated"));
    }
    let old_axes = be_u16(bytes, 4, "axisCount")?;
    let shared_count = be_u16(bytes, 6, "sharedTupleCount")? as usize;
    let off_shared = be_u32(bytes, 8, "offsetToSharedTuples")? as usize;
    let glyph_count = be_u16(bytes, 12, "glyphCount")? as usize;
    let flags = be_u16(bytes, 14, "flags")?;
    let off_glyph_data = be_u32(bytes, 16, "offsetToGlyphVariationData")? as usize;

    let new_axes = total_axes as usize;
    let old_axes = old_axes as usize;
    let n_new = new_axes.saturating_sub(old_axes);
    if n_new == 0 && extras.is_empty() {
        // Nothing to pad; keep the table byte-identical (axisCount
        // already matches the new fvar).
        let mut out = bytes.to_vec();
        out[4..6].copy_from_slice(&total_axes.to_be_bytes());
        return Ok(out);
    }

    // Glyph variation data offsets (relative to the data array start;
    // stored halved when the long-format flag is clear).
    let long_offsets = flags & 1 != 0;
    let mut glyph_offsets = Vec::with_capacity(glyph_count + 1);
    for i in 0..=glyph_count {
        let off = if long_offsets {
            be_u32(bytes, 20 + i * 4, "glyph offsets")? as usize
        } else {
            be_u16(bytes, 20 + i * 2, "glyph offsets")? as usize * 2
        };
        glyph_offsets.push(off);
    }

    // Rebuild each glyph's variation data, padding tuple headers and
    // appending any injected tuples.
    let mut new_glyph_data: Vec<u8> = Vec::new();
    let mut new_offsets: Vec<u32> = Vec::with_capacity(glyph_count + 1);
    let zero_coord = [0u8; 2]; // F2DOT14 0.0
    for i in 0..glyph_count {
        new_offsets.push(new_glyph_data.len() as u32);
        let gid = i as u16;
        let start = off_glyph_data
            .checked_add(glyph_offsets[i])
            .ok_or_else(|| gvar_err("glyph offset overflow"))?;
        let end = off_glyph_data
            .checked_add(glyph_offsets[i + 1])
            .ok_or_else(|| gvar_err("glyph offset overflow"))?;
        let gid_extras = extras.get(&gid);
        if start == end && gid_extras.is_none() {
            continue; // no variation data for this glyph
        }
        let (count_flags, mut headers, data_section, trailing) = if start == end {
            (0u16, Vec::new(), &[][..], &[][..])
        } else {
            let g = bytes
                .get(start..end)
                .ok_or_else(|| gvar_err("glyph variation data out of range"))?;
            let count_flags = be_u16(g, 0, "tupleVariationCount")?;
            let tuple_count = (count_flags & GVAR_TUPLE_COUNT_MASK) as usize;
            let off_data = be_u16(g, 2, "offsetToData")? as usize;

            let mut headers: Vec<u8> = Vec::new();
            let mut pos = 4usize;
            // Byte length of the old tuple data (shared point numbers +
            // each tuple's serialized deltas). Anything after it in the
            // glyph's data slice is trailing alignment padding, which
            // must stay at the END — injected tuple data goes before it,
            // since the spec reads tuple data sequentially.
            let mut old_data_len = if count_flags & GVAR_TUPLES_SHARE_POINT_NUMBERS != 0 {
                packed_points_len(
                    g.get(off_data..)
                        .ok_or_else(|| gvar_err("shared points out of range"))?,
                )?
            } else {
                0
            };
            for _ in 0..tuple_count {
                let data_size = be_u16(g, pos, "tuple header")? as usize;
                old_data_len += data_size;
                let tflags = be_u16(g, pos + 2, "tuple header")?;
                headers.extend_from_slice(
                    g.get(pos..pos + 4)
                        .ok_or_else(|| gvar_err("tuple header truncated"))?,
                );
                pos += 4;
                if tflags & GVAR_EMBEDDED_PEAK_TUPLE != 0 {
                    let len = old_axes * 2;
                    headers.extend_from_slice(
                        g.get(pos..pos + len)
                            .ok_or_else(|| gvar_err("embedded peak truncated"))?,
                    );
                    for _ in 0..n_new {
                        headers.extend_from_slice(&zero_coord);
                    }
                    pos += len;
                }
                if tflags & GVAR_INTERMEDIATE_REGION != 0 {
                    // start coords then end coords; pad each separately.
                    let len = old_axes * 2;
                    let start_coords = g
                        .get(pos..pos + len)
                        .ok_or_else(|| gvar_err("intermediate start truncated"))?;
                    let end_coords = g
                        .get(pos + len..pos + 2 * len)
                        .ok_or_else(|| gvar_err("intermediate end truncated"))?;
                    headers.extend_from_slice(start_coords);
                    for _ in 0..n_new {
                        headers.extend_from_slice(&zero_coord);
                    }
                    headers.extend_from_slice(end_coords);
                    for _ in 0..n_new {
                        headers.extend_from_slice(&zero_coord);
                    }
                    pos += 2 * len;
                }
            }
            let data_end = off_data
                .checked_add(old_data_len)
                .ok_or_else(|| gvar_err("tuple data section overflow"))?;
            let data_section = g
                .get(off_data..data_end)
                .ok_or_else(|| gvar_err("tuple data section out of range"))?;
            let trailing = g.get(data_end..).unwrap_or(&[]);
            (count_flags, headers, data_section, trailing)
        };

        // Injected tuples for this glyph.
        let mut added: u16 = 0;
        let mut added_data: Vec<u8> = Vec::new();
        if let Some(tuples) = gid_extras {
            for tuple in tuples {
                let (header, aux) = tuple.serialize();
                headers.extend_from_slice(&header);
                added_data.extend_from_slice(&aux);
                added += 1;
            }
        }
        let new_count_flags =
            (count_flags & !GVAR_TUPLE_COUNT_MASK) | (added + (count_flags & GVAR_TUPLE_COUNT_MASK));

        new_glyph_data.extend_from_slice(&new_count_flags.to_be_bytes());
        new_glyph_data.extend_from_slice(&(4 + headers.len() as u16).to_be_bytes());
        new_glyph_data.extend_from_slice(&headers);
        new_glyph_data.extend_from_slice(data_section);
        new_glyph_data.extend_from_slice(&added_data);
        new_glyph_data.extend_from_slice(trailing);
        if !new_glyph_data.len().is_multiple_of(2) {
            new_glyph_data.push(0); // keep glyph starts even (short offsets)
        }
    }
    new_offsets.push(new_glyph_data.len() as u32);

    // Re-encode the offsets array, keeping the source format unless it
    // can no longer represent the (slightly grown) data.
    let fits_short = new_offsets
        .iter()
        .all(|o| o % 2 == 0 && o / 2 <= u16::MAX as u32);
    let use_long = long_offsets || !fits_short;
    let new_flags = if use_long { flags | 1 } else { flags & !1 };
    let offsets_len = new_offsets.len() * if use_long { 4 } else { 2 };

    // Shared tuples: pad each with 0.0 per new axis. Any padding between
    // the shared region and the glyph data is preserved. The offsets
    // array may itself grow (short→long), shifting everything after it.
    let old_shared_len = shared_count * old_axes * 2;
    let new_shared_len = shared_count * new_axes * 2;
    let shared_end = off_shared
        .checked_add(old_shared_len)
        .ok_or_else(|| gvar_err("shared tuples overflow"))?;
    if shared_end > off_glyph_data || off_glyph_data > bytes.len() || off_shared > bytes.len() {
        return Err(gvar_err("shared tuple region out of range"));
    }
    let gap_len = off_glyph_data - shared_end;
    let new_off_shared = 20 + offsets_len;
    let new_off_glyph_data = new_off_shared + new_shared_len + gap_len;

    let mut out = Vec::with_capacity(new_off_glyph_data + new_glyph_data.len());
    out.extend_from_slice(&bytes[..20]);
    for off in &new_offsets {
        if use_long {
            out.extend_from_slice(&off.to_be_bytes());
        } else {
            out.extend_from_slice(&((*off / 2) as u16).to_be_bytes());
        }
    }
    debug_assert_eq!(out.len(), new_off_shared);
    for i in 0..shared_count {
        let t = off_shared + i * old_axes * 2;
        out.extend_from_slice(&bytes[t..t + old_axes * 2]);
        for _ in 0..n_new {
            out.extend_from_slice(&zero_coord);
        }
    }
    out.extend_from_slice(&bytes[shared_end..off_glyph_data]); // inter-region padding
    out.extend_from_slice(&new_glyph_data);

    out[4..6].copy_from_slice(&total_axes.to_be_bytes());
    out[8..12].copy_from_slice(&(new_off_shared as u32).to_be_bytes());
    out[14..16].copy_from_slice(&new_flags.to_be_bytes());
    out[16..20].copy_from_slice(&(new_off_glyph_data as u32).to_be_bytes());
    Ok(out)
}

// ---- Export options (default-location rebuild + hidden axes) ----------

/// Patch fvar axis records in place (20 bytes each: tag(4) min(4)
/// default(4) max(4) flags(2) nameID(2)) — no repack needed for the
/// four-byte default / two-byte flag writes.
fn fvar_patch(
    font_bytes: &[u8],
    mut patch: impl FnMut(&str, &mut [u8]),
) -> Result<Vec<u8>, JsError> {
    let mut out = font_bytes.to_vec();
    let (table_off, axes_off, axis_size, axis_count) = {
        let font = FontRef::new(font_bytes).map_err(|e| err(format!("invalid font: {e}")))?;
        let rec = font
            .table_directory()
            .table_records()
            .iter()
            .find(|r| r.tag() == Tag::new(b"fvar"))
            .ok_or_else(|| err("no fvar table"))?;
        let fvar = font.fvar().map_err(|e| err(format!("invalid fvar: {e}")))?;
        (
            rec.offset() as usize,
            fvar.axis_instance_arrays_offset().to_u32() as usize,
            fvar.axis_size() as usize,
            fvar.axis_count() as usize,
        )
    };
    let base = table_off + axes_off;
    for i in 0..axis_count {
        let rec_off = base + i * axis_size;
        if rec_off + 20 > out.len() {
            return Err(err("fvar axis record out of bounds"));
        }
        let tag = std::str::from_utf8(&out[rec_off..rec_off + 4])
            .map_err(|_| err("bad fvar axis tag bytes"))?
            .to_string();
        patch(&tag, &mut out[rec_off..rec_off + axis_size]);
    }
    Ok(out)
}

/// Rebuild the export so its resting state IS the current location:
/// fvar defaults move to the given location (user values + mapped
/// parametric values, resolved JS-side) and the avar2 table regenerates
/// around that origin. Axis ranges stay intact.
#[wasm_bindgen]
pub fn set_default_location(
    font_bytes: Vec<u8>,
    default_location_json: &str,
    mappings_csv: &str,
    axis_metadata_json: Option<String>,
    parametric_tags_json: Option<String>,
) -> Result<Vec<u8>, JsError> {
    let location: HashMap<String, f64> = serde_json::from_str(default_location_json)
        .map_err(|e| err(format!("invalid default location JSON: {e}")))?;
    let patched = fvar_patch(&font_bytes, |tag, rec| {
        if let Some(&v) = location.get(tag) {
            let fixed = (v * 65536.0).round() as i32;
            rec[8..12].copy_from_slice(&fixed.to_be_bytes());
        }
    })?;
    add_avar2(patched, mappings_csv, axis_metadata_json, parametric_tags_json)
}

/// Flag fvar axes as hidden in the exported font (fvar axis flags bit
/// 0x0001): they keep working via font-variation-settings but don't
/// appear in font pickers or design apps.
#[wasm_bindgen]
pub fn set_hidden_axes(font_bytes: Vec<u8>, hidden_tags_json: &str) -> Result<Vec<u8>, JsError> {
    let hidden: HashSet<String> = serde_json::from_str(hidden_tags_json)
        .map_err(|e| err(format!("invalid hidden tags JSON: {e}")))?;
    fvar_patch(&font_bytes, |tag, rec| {
        if hidden.contains(tag) {
            let flags = u16::from_be_bytes([rec[16], rec[17]]) | 0x0001;
            rec[16..18].copy_from_slice(&flags.to_be_bytes());
        }
    })
}
