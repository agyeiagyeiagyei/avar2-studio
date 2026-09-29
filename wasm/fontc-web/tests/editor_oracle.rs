//! Oracle for the editor module (Phase 0 source-level glyph data path),
//! on the CrispyMini fixture:
//!   a. Plist round-trip fidelity: `Plist::parse` + `to_string` with no
//!      edits (an empty overlay request) must compile to the same bytes
//!      as the untouched source.
//!   b. `glyph_model` extraction: default-master node counts for
//!      e/v/a/n/o.
//!   c. `compile_with_overlays`: an lcwd brace on 'e' at one parametric
//!      corner must change the outline THERE and nowhere else.
//!   d. A second overlay at the same coordinates replaces the first.

use std::path::Path;

fn fixture_source() -> String {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    std::fs::read_to_string(manifest.join("../../examples/crispy-mini/sources/CrispyMini.glyphs"))
        .expect("read CrispyMini fixture")
}

/// fontc stamps head.modified from the clock; pin it so byte comparisons
/// are meaningful (fontbe head.rs current_timestamp).
fn pin_build_date() {
    std::env::set_var("SOURCE_DATE_EPOCH", "1456796400");
}

/// The model's default master: the one whose axesValues equal the axes'
/// defaults (CrispyMini's "Variable Font Origin").
fn default_master_layer<'a>(model: &'a serde_json::Value) -> &'a serde_json::Value {
    let axes = model["axes"].as_array().expect("axes");
    let defaults: Vec<f64> = axes
        .iter()
        .map(|a| a["default"].as_f64().expect("axis default"))
        .collect();
    let master = model["masters"]
        .as_array()
        .expect("masters")
        .iter()
        .find(|m| {
            m["axesValues"]
                .as_array()
                .expect("axesValues")
                .iter()
                .map(|v| v.as_f64().expect("axis value"))
                .eq(defaults.iter().copied())
        })
        .expect("default master");
    let master_id = master["id"].as_str().expect("master id");
    model["glyph"]["layers"]
        .as_array()
        .expect("layers")
        .iter()
        .find(|l| l["layerId"].as_str() == Some(master_id))
        .expect("default master layer")
}

#[test]
fn plist_roundtrip_compiles_identically() {
    pin_build_date();
    let source = fixture_source();
    let expected = fontc_web::compile_glyphs(source.clone()).expect("compile original");
    let roundtrip = fontc_web::compile_with_overlays(source, r#"{"axes":[],"overlays":[]}"#)
        .expect("compile round-tripped");
    assert_eq!(
        expected, roundtrip,
        "Plist parse + to_string with no edits changed the compiled font"
    );
}

#[test]
fn glyph_model_extracts_default_master_counts() {
    let source = fixture_source();
    for (glyph, want) in [("e", 44), ("v", 20), ("a", 45), ("n", 28), ("o", 48)] {
        let model: serde_json::Value = serde_json::from_str(
            &fontc_web::glyph_model(source.clone(), glyph.to_string()).expect("glyph_model"),
        )
        .expect("model json");
        let layer = default_master_layer(&model);
        let nodes: usize = layer["outline"]["paths"]
            .as_array()
            .expect("paths")
            .iter()
            .map(|p| p["nodes"].as_array().expect("nodes").len())
            .sum();
        assert_eq!(nodes, want, "glyph {glyph} default-master node count");
    }
}

/// Draw 'e' at a user-space location (fvar tags), returning the outline
/// as a flat point list in font units.
fn draw_e(font_bytes: &[u8], user_loc: &[(&str, f64)]) -> Vec<(f32, f32)> {
    use skrifa::outline::OutlinePen;
    use skrifa::prelude::*;

    struct PointPen {
        points: Vec<(f32, f32)>,
    }
    impl OutlinePen for PointPen {
        fn move_to(&mut self, x: f32, y: f32) {
            self.points.push((x, y));
        }
        fn line_to(&mut self, x: f32, y: f32) {
            self.points.push((x, y));
        }
        fn quad_to(&mut self, cx: f32, cy: f32, x: f32, y: f32) {
            self.points.push((cx, cy));
            self.points.push((x, y));
        }
        fn curve_to(&mut self, cx0: f32, cy0: f32, cx1: f32, cy1: f32, x: f32, y: f32) {
            self.points.push((cx0, cy0));
            self.points.push((cx1, cy1));
            self.points.push((x, y));
        }
        fn close(&mut self) {}
    }

    fn normalize(v: f64, min: f64, default: f64, max: f64) -> f64 {
        if v == default {
            0.0
        } else if v < default {
            (v - default) / (default - min)
        } else {
            (v - default) / (max - default)
        }
    }

    let rf = read_fonts::FontRef::new(font_bytes).expect("parse font");
    use read_fonts::TableProvider;
    let axes: Vec<(String, f64, f64, f64)> = rf
        .fvar()
        .expect("fvar")
        .axes()
        .unwrap_or_default()
        .iter()
        .map(|a| {
            (
                a.axis_tag().to_string(),
                a.min_value.get().to_f64(),
                a.default_value.get().to_f64(),
                a.max_value.get().to_f64(),
            )
        })
        .collect();
    let coords: Vec<NormalizedCoord> = axes
        .iter()
        .map(|(tag, min, default, max)| {
            let v = user_loc
                .iter()
                .find(|(t, _)| t == tag)
                .map(|(_, v)| *v)
                .unwrap_or(*default);
            NormalizedCoord::from_f32(normalize(v, *min, *default, *max) as f32)
        })
        .collect();

    let font = FontRef::new(font_bytes).expect("parse font");
    let gid = font.charmap().map('e').expect("glyph e");
    let glyph = font.outline_glyphs().get(gid).expect("outline e");
    let mut pen = PointPen { points: Vec::new() };
    glyph
        .draw(
            skrifa::outline::DrawSettings::unhinted(Size::unscaled(), coords.as_slice()),
            &mut pen,
        )
        .expect("draw e");
    pen.points
}

/// The fixture's axis (min, max) by tag, read from the glyph model.
fn axis_range(model: &serde_json::Value, tag: &str) -> (f64, f64) {
    let axis = model["axes"]
        .as_array()
        .expect("axes")
        .iter()
        .find(|a| a["tag"].as_str() == Some(tag))
        .expect("axis");
    (
        axis["min"].as_f64().expect("min"),
        axis["max"].as_f64().expect("max"),
    )
}

/// 'e's default-master outline with the first node moved +500 in x.
fn edited_e_outline(source: &str, move_x: f64) -> serde_json::Value {
    let model: serde_json::Value = serde_json::from_str(
        &fontc_web::glyph_model(source.to_string(), "e".to_string()).expect("glyph_model"),
    )
    .expect("model json");
    let mut outline = default_master_layer(&model)["outline"].clone();
    let node = &mut outline["paths"][0]["nodes"][0];
    node[0] = serde_json::json!(node[0].as_f64().expect("node x") + move_x);
    outline
}

#[test]
fn overlay_brace_moves_only_its_corner() {
    pin_build_date();
    let source = fixture_source();
    let model: serde_json::Value = serde_json::from_str(
        &fontc_web::glyph_model(source.clone(), "e".to_string()).expect("glyph_model"),
    )
    .expect("model json");
    let (xtra_min, xtra_max) = axis_range(&model, "XTRA");
    let (_, xopq_max) = axis_range(&model, "XOPQ");
    let (_, yopq_max) = axis_range(&model, "YOPQ");

    // Brace 'e' at {XTRA max, XOPQ max, YOPQ max, lcwd max} with a visibly
    // moved node. (The brief's corner used XTRA MIN, but in this fixture
    // every axis default sits at its min, and a brace at an axis's default
    // cannot be scoped to it: a normalized-0 peak means "axis does not
    // participate" in gvar/varLib, so the delta bleeds across that axis —
    // the same limit braces.rs documents for the studio's binary path.
    // Pinning at the non-default extremes keeps the corner fully scoped.)
    let outline = edited_e_outline(&source, 500.0);
    let request = serde_json::json!({
        "axes": [{"tag": "lcwd", "name": "Lowercase width", "min": 0, "default": 0, "max": 100}],
        "overlays": [{"glyph": "e",
                      "location": {"XTRA": xtra_max, "XOPQ": xopq_max, "YOPQ": yopq_max, "lcwd": 100},
                      "outline": outline}],
    });
    let overlaid = fontc_web::compile_with_overlays(source.clone(), &request.to_string())
        .expect("compile with overlay");
    let base = fontc_web::compile_glyphs(source).expect("compile base");

    // At the pinned corner the model reproduces the overlay outline — the
    // default-master outline with one node moved +500 in x (up to gvar's
    // integer rounding of the interpolated prediction, ±1 unit).
    let pinned = draw_e(
        &overlaid,
        &[
            ("XTRA", xtra_max),
            ("XOPQ", xopq_max),
            ("YOPQ", yopq_max),
            ("lcwd", 100.0),
        ],
    );
    let default = draw_e(&overlaid, &[]);
    assert_eq!(pinned.len(), default.len(), "point count at pinned corner");
    let big: Vec<_> = pinned
        .iter()
        .zip(default.iter())
        .map(|(p, d)| (p.0 - d.0, p.1 - d.1))
        .filter(|(dx, dy)| dx.abs() > 100.0 || dy.abs() > 100.0)
        .collect();
    assert_eq!(big.len(), 1, "exactly the moved node should move: {big:?}");
    assert!(
        (big[0].0 - 500.0).abs() <= 1.0 && big[0].1.abs() <= 1.0,
        "moved node should shift by (500, 0), got {:?}",
        big[0]
    );

    // The opposite XTRA corner (XTRA min, lcwd engaged) must be untouched.
    let opposite = draw_e(
        &overlaid,
        &[
            ("XTRA", xtra_min),
            ("XOPQ", xopq_max),
            ("YOPQ", yopq_max),
            ("lcwd", 100.0),
        ],
    );
    let base_opposite = draw_e(
        &base,
        &[("XTRA", xtra_min), ("XOPQ", xopq_max), ("YOPQ", yopq_max)],
    );
    assert_eq!(
        opposite, base_opposite,
        "opposite XTRA corner must be untouched"
    );
}

#[test]
fn second_overlay_at_same_location_replaces_first() {
    pin_build_date();
    let source = fixture_source();
    let model: serde_json::Value = serde_json::from_str(
        &fontc_web::glyph_model(source.clone(), "e".to_string()).expect("glyph_model"),
    )
    .expect("model json");
    let (_, xtra_max) = axis_range(&model, "XTRA");
    let (_, xopq_max) = axis_range(&model, "XOPQ");
    let (_, yopq_max) = axis_range(&model, "YOPQ");
    let location =
        serde_json::json!({"XTRA": xtra_max, "XOPQ": xopq_max, "YOPQ": yopq_max, "lcwd": 100});

    let first = edited_e_outline(&source, 500.0);
    let second = edited_e_outline(&source, 700.0);
    let request = serde_json::json!({
        "axes": [{"tag": "lcwd", "name": "Lowercase width", "min": 0, "default": 0, "max": 100}],
        "overlays": [
            {"glyph": "e", "location": location, "outline": first},
            {"glyph": "e", "location": location, "outline": second},
        ],
    });
    let overlaid = fontc_web::compile_with_overlays(source, &request.to_string())
        .expect("compile with overlays");
    let pinned = draw_e(
        &overlaid,
        &[
            ("XTRA", xtra_max),
            ("XOPQ", xopq_max),
            ("YOPQ", yopq_max),
            ("lcwd", 100.0),
        ],
    );
    let default = draw_e(&overlaid, &[]);
    let big: Vec<f32> = pinned
        .iter()
        .zip(default.iter())
        .map(|(p, d)| p.0 - d.0)
        .filter(|dx| dx.abs() > 100.0)
        .collect();
    assert_eq!(
        big.len(),
        1,
        "only the moved node should differ at the corner: {big:?}"
    );
    assert!(
        (big[0] - 700.0).abs() <= 1.0,
        "second overlay should replace the first (move = {})",
        big[0]
    );
}
