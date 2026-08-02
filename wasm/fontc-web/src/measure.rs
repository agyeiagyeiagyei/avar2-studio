//! Outline-area probe for the coverage audit (Layer B): a stem-darkness
//! proxy measured on the TRUE outline (skrifa draws the glyph at the
//! location; we integrate the filled area), not rasterization — canvas
//! 2D `fontVariationSettings` proved unreliable in Chrome, and DOM
//! rasterization doesn't exist. Batched: total filled area across the
//! given glyphs for each location.

use read_fonts::{FontRef, TableProvider};
use skrifa::outline::{DrawSettings, OutlinePen};
use skrifa::{GlyphId, MetadataProvider};
use std::collections::HashMap;
use wasm_bindgen::prelude::*;

use crate::err;

// Subdivisions per curve when flattening to polygons. The metric only
// needs monotonicity, not sub-unit exactness.
const FLATTEN: usize = 16;

/// Shoelace accumulator: signed area per contour, summed with sign so
/// counters subtract (nonzero winding).
struct AreaPen {
    total: f64,
    contour: f64,
    cx: f64,
    cy: f64,
    sx: f64,
    sy: f64,
}

impl AreaPen {
    fn new() -> Self {
        Self { total: 0.0, contour: 0.0, cx: 0.0, cy: 0.0, sx: 0.0, sy: 0.0 }
    }

    fn segment(&mut self, x: f64, y: f64) {
        self.contour += self.cx * y - x * self.cy;
        self.cx = x;
        self.cy = y;
    }

    fn finish(mut self) -> f64 {
        if self.contour != 0.0 {
            self.total += self.contour;
        }
        self.total.abs() * 0.5
    }
}

impl OutlinePen for AreaPen {
    fn move_to(&mut self, x: f32, y: f32) {
        if self.contour != 0.0 {
            self.total += self.contour;
            self.contour = 0.0;
        }
        self.cx = x as f64;
        self.cy = y as f64;
        self.sx = self.cx;
        self.sy = self.cy;
    }

    fn line_to(&mut self, x: f32, y: f32) {
        self.segment(x as f64, y as f64);
    }

    fn quad_to(&mut self, cx: f32, cy: f32, x: f32, y: f32) {
        let (x0, y0) = (self.cx, self.cy);
        for i in 1..=FLATTEN {
            let t = i as f64 / FLATTEN as f64;
            let mt = 1.0 - t;
            let px = mt * mt * x0 + 2.0 * mt * t * cx as f64 + t * t * x as f64;
            let py = mt * mt * y0 + 2.0 * mt * t * cy as f64 + t * t * y as f64;
            self.segment(px, py);
        }
    }

    fn curve_to(&mut self, cx0: f32, cy0: f32, cx1: f32, cy1: f32, x: f32, y: f32) {
        let (x0, y0) = (self.cx, self.cy);
        for i in 1..=FLATTEN {
            let t = i as f64 / FLATTEN as f64;
            let mt = 1.0 - t;
            let px = mt * mt * mt * x0
                + 3.0 * mt * mt * t * cx0 as f64
                + 3.0 * mt * t * t * cx1 as f64
                + t * t * t * x as f64;
            let py = mt * mt * mt * y0
                + 3.0 * mt * mt * t * cy0 as f64
                + 3.0 * mt * t * t * cy1 as f64
                + t * t * t * y as f64;
            self.segment(px, py);
        }
    }

    fn close(&mut self) {
        self.segment(self.sx, self.sy);
        self.total += self.contour;
        self.contour = 0.0;
    }
}

#[derive(serde::Deserialize)]
struct MeasureRequest {
    glyphs: Vec<String>,
    locations: Vec<HashMap<String, f64>>,
}

/// Sum of filled outline area (font units² at the font's upm) across
/// `glyphs`, per location (user coords, fvar tags). One entry per
/// location, in order.
#[wasm_bindgen]
pub fn measure_at(font_bytes: Vec<u8>, request_json: String) -> Result<Vec<f64>, JsError> {
    let font = FontRef::new(&font_bytes).map_err(|e| err(format!("parse font: {e}")))?;
    // skrifa pins read-fonts 0.37 (ours is 0.40) — its FontRef is a
    // different type, so the outline side parses the bytes again.
    let skrifa_font = skrifa::FontRef::new(&font_bytes).map_err(|e| err(format!("parse font: {e}")))?;
    let request: MeasureRequest =
        serde_json::from_str(&request_json).map_err(|e| err(format!("request json: {e}")))?;

    let fvar = font.fvar().map_err(|e| err(format!("missing fvar: {e}")))?;
    let axes: Vec<(String, f64, f64, f64)> = fvar
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

    let post = font.post().map_err(|e| err(format!("missing post: {e}")))?;
    let num_glyphs = font
        .maxp()
        .map_err(|e| err(format!("missing maxp: {e}")))?
        .num_glyphs();
    let mut gids = Vec::new();
    for name in &request.glyphs {
        // Missing glyphs are skipped (probe glyphs are a heuristic
        // set; small fonts won't have them all).
        if let Some(gid) = (0..num_glyphs).find(|&g| {
            post.glyph_name(read_fonts::types::GlyphId16::new(g))
                .map(|n| n.to_string())
                .as_deref()
                == Some(name.as_str())
        }) {
            gids.push(GlyphId::new(gid as u32));
        }
    }

    let outlines = skrifa_font.outline_glyphs();
    let size = skrifa::prelude::Size::new(1000.0);
    let mut out = Vec::with_capacity(request.locations.len());
    for loc in &request.locations {
        let coords: Vec<skrifa::prelude::NormalizedCoord> = axes
            .iter()
            .map(|(tag, min, default, max)| {
                let v = loc.get(tag).copied().unwrap_or(*default);
                skrifa::prelude::NormalizedCoord::from_f32(
                    crate::normalize(v, *min, *default, *max) as f32
                )
            })
            .collect();
        let mut total = 0.0;
        for gid in &gids {
            if let Some(glyph) = outlines.get(*gid) {
                let mut pen = AreaPen::new();
                if glyph
                    .draw(DrawSettings::unhinted(size, coords.as_slice()), &mut pen)
                    .is_ok()
                {
                    total += pen.finish();
                }
            }
        }
        out.push(total);
    }
    Ok(out)
}
