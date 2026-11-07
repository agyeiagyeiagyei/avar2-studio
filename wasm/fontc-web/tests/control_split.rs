//! Self-contained checks for the split control-axes build path (drawn
//! brace layers compiled at source level by `compile_with_overlays`,
//! computed layers added on the font bytes by `apply_control_axes`):
//!
//!   a. Applying a control axis twice no longer errors — the second run
//!      tolerates the existing fvar axis and re-adds the same tuples
//!      (idempotent at the request level; callers are expected to rebuild
//!      from source rather than stack applications, but the tolerance is
//!      what lets the split path mix source-declared and bytes-level axes).
//!   b. A layer carrying a drawn `outline` is SKIPPED by
//!      apply_control_axes (its effect was compiled in at source level),
//!      while a sibling computed layer on the same axis still gets a tuple.
//!   c. A request whose layers are all drawn on an already-declared axis
//!      is a byte-level no-op.

use read_fonts::{FontRef, TableProvider};

/// Minimal two-master source: one wght axis, one glyph "n" with a
/// rectangle per master (the Bold rectangle is wider/taller).
const SOURCE: &str = r#"{
.appVersion = "3406";
.formatVersion = 3;
axes = (
{
name = "Weight";
tag = wght;
}
);
familyName = "SplitTest";
customParameters = (
{
name = "Variable Font Origin";
value = "REGULAR01";
}
);
fontMaster = (
{
axesValues = (
400
);
id = "REGULAR01";
name = "Regular";
},
{
axesValues = (
700
);
id = "BOLD01";
name = "Bold";
}
);
glyphs = (
{
glyphname = n;
layers = (
{
layerId = "REGULAR01";
width = 500;
shapes = (
{
closed = 1;
nodes = (
(100,0,l),
(100,500,l),
(400,500,l),
(400,0,l)
);
}
);
},
{
layerId = "BOLD01";
width = 560;
shapes = (
{
closed = 1;
nodes = (
(80,0,l),
(80,560,l),
(440,560,l),
(440,0,l)
);
}
);
}
);
}
);
unitsPerEm = 1000;
}
"#;

/// One control axis on glyph n: a plain layer at wght=700 and a
/// correction layer pinned at wght=700 (a non-default value, so no
/// cancel companions) rendering as-if wght=400.
const CONTROL_JSON: &str = r#"[{"tag":"crbr","name":"Crossbar","min":0.0,"default":0.0,"max":100.0,"layers":[{"glyph":"n","location":{"wght":700.0,"crbr":100.0}},{"glyph":"n","location":{"wght":700.0,"crbr":50.0},"target":{"wght":400.0}}]}]"#;

fn fvar_axis_tags(font: &FontRef) -> Vec<String> {
    font.fvar()
        .expect("fvar")
        .axis_instance_arrays()
        .expect("axis array")
        .axes()
        .iter()
        .map(|a| a.axis_tag().to_string())
        .collect()
}

fn base_tuple_count() -> usize {
    let base = fontc_web::compile_glyphs(SOURCE.to_string()).expect("compile");
    let font = FontRef::new(&base).expect("parse base");
    gvar_tuple_count(&font, "n")
}

fn gvar_tuple_count(font: &FontRef, glyph_name: &str) -> usize {
    let post = font.post().expect("post");
    let gid = (0..font.maxp().expect("maxp").num_glyphs())
        .find(|&gid| post.glyph_name(gid.into()).map(|n| n.as_ref()) == Some(glyph_name))
        .expect("glyph n in font");
    font.gvar()
        .expect("gvar")
        .glyph_variation_data(gid.into())
        .expect("glyph variation data")
        .map(|d| d.tuples().count())
        .unwrap_or(0)
}

#[test]
fn reapplication_tolerates_existing_axis() {
    let base = fontc_web::compile_glyphs(SOURCE.to_string()).expect("compile");
    let once = fontc_web::apply_control_axes(base.clone(), CONTROL_JSON).expect("first apply");
    let once_font = FontRef::new(&once).expect("parse once");
    assert_eq!(fvar_axis_tags(&once_font), ["wght", "crbr"]);
    // The base font already carries the wght master tuple on n; the two
    // computed layers add one tuple each on top of it.
    let tuples_once = gvar_tuple_count(&once_font, "n") - base_tuple_count();
    assert_eq!(tuples_once, 2, "one tuple per computed layer");

    // Second application of the same request: the axis exists now.
    let twice =
        fontc_web::apply_control_axes(once.clone(), CONTROL_JSON).expect("re-apply tolerated");
    let twice_font = FontRef::new(&twice).expect("parse twice");
    assert_eq!(fvar_axis_tags(&twice_font), ["wght", "crbr"]);
    assert_eq!(
        gvar_tuple_count(&twice_font, "n") - base_tuple_count(),
        // The tuples ARE added again (stacking) — the tolerance exists so
        // the split path can add computed tuples onto a font whose axis
        // came from the source compile, not to make re-application a
        // no-op. Callers must not re-apply onto an already-applied font.
        tuples_once * 2,
        "tolerance re-adds tuples rather than erroring"
    );
}

#[test]
fn drawn_layers_are_skipped() {
    let base = fontc_web::compile_glyphs(SOURCE.to_string()).expect("compile");
    // Same request, but the plain layer carries a drawn outline (content
    // is irrelevant here — presence marks it as source-compiled).
    let with_drawn = r#"[{"tag":"crbr","name":"Crossbar","min":0.0,"default":0.0,"max":100.0,"layers":[{"glyph":"n","location":{"wght":700.0,"crbr":100.0},"outline":{"width":500,"paths":[]}},{"glyph":"n","location":{"wght":700.0,"crbr":50.0},"target":{"wght":400.0}}]}]"#;
    let out = fontc_web::apply_control_axes(base, with_drawn).expect("apply with drawn layer");
    let font = FontRef::new(&out).expect("parse");
    // Axis still declared (the drawn layer needs it at render time), but
    // only the correction layer produced tuples.
    assert_eq!(fvar_axis_tags(&font), ["wght", "crbr"]);
    assert_eq!(
        gvar_tuple_count(&font, "n") - base_tuple_count(),
        1,
        "only the computed layer"
    );
}

#[test]
fn all_drawn_on_existing_axis_is_a_no_op() {
    let base = fontc_web::compile_glyphs(SOURCE.to_string()).expect("compile");
    let once = fontc_web::apply_control_axes(base, CONTROL_JSON).expect("first apply");
    let all_drawn = r#"[{"tag":"crbr","name":"Crossbar","min":0.0,"default":0.0,"max":100.0,"layers":[{"glyph":"n","location":{"wght":700.0,"crbr":100.0},"outline":{"width":500,"paths":[]}}]}]"#;
    let again = fontc_web::apply_control_axes(once.clone(), all_drawn).expect("no-op apply");
    assert_eq!(
        again, once,
        "all-drawn on an existing axis must return the font unchanged"
    );
}
