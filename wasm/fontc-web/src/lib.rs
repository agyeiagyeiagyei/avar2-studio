//! fontc → WASM: compile a .glyphs source (as an in-memory string) to
//! TTF bytes in the browser. Part of the avar2-studio GitHub Pages
//! migration (docs/migration-github-pages.md, Phase 2).
//!
//! JS side: `compile_glyphs(source: string) -> Uint8Array` (throws on
//! compiler error).

use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn compile_glyphs(source: String) -> Result<Vec<u8>, JsError> {
    let input = fontc::Input::from_glyphs(source);
    let source = input
        .create_source()
        .map_err(|e| JsError::new(&e.to_string()))?;
    let options = fontc::Options::default();
    fontc::generate_font(source, options).map_err(|e| JsError::new(&e.to_string()))
}
