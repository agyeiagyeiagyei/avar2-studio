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
//!     "params": {"min": -100, "max": 100, "bias": 1.0, "scale": 1.25,
//!                "instance_spac": {"Narrow Thin 144": 10.0}}}]
//! Per-instance SPAC coordinates ride the JSON (`params.instance_spac`),
//! parsed from the avar2 CSV by the caller (csv_io). The optional 4th
//! argument — the avar.csv path — is RETAINED for positional-argument
//! compatibility with older callers but IGNORED (never read).

use std::{env, fs, process::exit};

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 4 {
        eprintln!("usage: spac-inject <in.ttf> <out.ttf> <transforms.json> [avar.csv]");
        eprintln!("note: the optional avar.csv argument is retained for compatibility and ignored;");
        eprintln!("      per-instance SPAC coordinates ride the JSON (params.instance_spac).");
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
    match fontc_web::apply_transforms(font, &transforms, "") {
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
