//! Native SPAC injector — the same engine the static demo's wasm uses
//! (spac.rs), exposed as a CLI so the desktop studio can inject the
//! SPAC axis without round-tripping gvar through fontTools, whose
//! re-encoding overflows gvar's 64KB per-glyph header ceiling on
//! twin-heavy fonts (slant x rounding x grade x relocation).
//!
//! usage: spac-inject <in.ttf> <out.ttf> <transforms.json> [avar.csv]
//!
//! transforms.json is the studio's transform list shape, e.g.
//!   [{"type": "spac_widthaware", "enabled": true,
//!     "params": {"min": -100, "max": 100, "bias": 1.0, "scale": 1.25}}]
//! The optional CSV supplies per-instance SPAC coordinates (the same
//! `_instance_spac_overrides` contract as the Python transform).

use std::{env, fs, process::exit};

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 4 {
        eprintln!("usage: spac-inject <in.ttf> <out.ttf> <transforms.json> [avar.csv]");
        exit(2);
    }
    let font = fs::read(&args[1]).unwrap_or_else(|e| {
        eprintln!("spac-inject: read {}: {e}", args[1]);
        exit(1);
    });
    let transforms = fs::read_to_string(&args[3]).unwrap_or_else(|e| {
        eprintln!("spac-inject: read {}: {e}", args[3]);
        exit(1);
    });
    let csv = if args.len() > 4 {
        fs::read_to_string(&args[4]).unwrap_or_else(|e| {
            eprintln!("spac-inject: read {}: {e}", args[4]);
            exit(1);
        })
    } else {
        String::new()
    };
    match fontc_web::apply_transforms(font, &transforms, &csv) {
        Ok(out) => {
            if let Err(e) = fs::write(&args[2], out) {
                eprintln!("spac-inject: write {}: {e}", args[2]);
                exit(1);
            }
        }
        Err(e) => {
            eprintln!("spac-inject: {e:?}");
            exit(1);
        }
    }
}
