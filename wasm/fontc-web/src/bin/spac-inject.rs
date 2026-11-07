//! Native SPAC injector — the same engine the static demo's wasm uses
//! (spac.rs), exposed as a CLI so the desktop studio can inject the
//! SPAC axis without round-tripping gvar through fontTools, whose
//! re-encoding overflows gvar's 64KB per-glyph header ceiling on
//! twin-heavy fonts (slant x rounding x grade x relocation).
//!
//! usage: spac-inject <in.ttf> <out.ttf> <transforms.json> [avar.csv]
//!        spac-inject --rule
//!
//! `--rule` prints the width-aware amount rule this build implements. The
//! studio asks before it trusts the binary (builtin_spac_widthaware.py
//! `NATIVE_RULE`): a build for an older rule would read the same JSON and
//! write different amounts.
//!
//! transforms.json is the studio's transform list shape, e.g.
//!   [{"type": "spac_widthaware", "enabled": true,
//!     "params": {"min": -100, "max": 100, "bias": 1.0, "scale": 1.25,
//!                "instance_spac": {"Narrow Thin 144": 10.0},
//!                "master_locations": [{}, {"XTRA": 1715.0, "XOPQ": 1.0}]}}]
//! Per-instance SPAC coordinates ride the JSON (`params.instance_spac`),
//! parsed from the avar2 CSV by the caller (csv_io). So do the master
//! locations the width-aware amounts are computed at
//! (`params.master_locations`: the designspace's masters in user units,
//! the default as `{}`); without them the corners are inferred from the
//! font's gvar, as the Python transform falls back. The optional 4th
//! argument — the avar.csv path — is RETAINED for positional-argument
//! compatibility with older callers but IGNORED (never read).

use std::{env, fs, process::exit};

/// The width-aware amount rule: per-master corner amounts (E2i-c), with a SPAC
/// unit measured against the masters' average width.
const RULE: &str = "e2i-c-masters";

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() == 2 && args[1] == "--rule" {
        println!("{RULE}");
        return;
    }
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
