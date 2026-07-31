/* tslint:disable */
/* eslint-disable */

/**
 * Add new user axes + an avar v2 table (and the supporting VarStore
 * padding) to a compiled variable font, mirroring
 * `gftools.scripts.gen_avar2.gen_avar2_mapping`.
 *
 * `mappings_csv` has a first "Instance Name" column (ignored); the
 * remaining column headers are axis tags. Columns that already exist in
 * fvar are output (parametric) axes; the rest become new fvar axes.
 * Empty cells mean "axis default" and are dropped from the locations.
 *
 * Returns the font with updated fvar/name/avar/gvar/HVAR/GDEF/MVAR,
 * repacked as a valid TTF.
 *
 * `axis_metadata_json` (optional) is a JSON object
 * `{TAG: {min, default, max}}` overriding the CSV-derived range for
 * new user axes (the studio's avar2-axis-metadata.json semantics).
 */
export function add_avar2(font_bytes: Uint8Array, mappings_csv: string, axis_metadata_json?: string | null): Uint8Array;

/**
 * Add control (secondary parametric) axes from a config bundle to a
 * compiled variable font: one fvar axis per entry, and a computed gvar
 * brace tuple per layer (see braces.rs).
 *
 * `control_json` is the bundle's `control_axes.axes` array.
 */
export function apply_control_axes(font_bytes: Uint8Array, control_json: string): Uint8Array;

/**
 * Add the GRAD grade axis from a config bundle: fvar axis (−10/0/+10)
 * plus equalised light/dark brace tuples per graded instance
 * (see braces.rs; model ported from grade.py / grade_shadow.py).
 *
 * `grade_json` is the bundle's `grade` object; `instance_coords_json`
 * maps instance name → its base parametric coords `{XTRA, XOPQ, YOPQ}`.
 */
export function apply_grade(font_bytes: Uint8Array, grade_json: string, instance_coords_json: string): Uint8Array;

/**
 * Apply the bundle's post-build transforms (the SPAC injectors,
 * `transforms.transforms` with `enabled: true`) to a compiled variable
 * font: SPAC fvar axis + gvar phantom tuples + rebuilt HVAR
 * (see spac.rs; ported from gftools gen-spac and the studio's
 * width-aware variant).
 *
 * `transforms_json` is the bundle's `transforms.transforms` array;
 * `avar2_csv` is the bundle's avar2 mappings CSV (a SPAC column, when
 * present, pins per-instance SPAC coordinates).
 */
export function apply_transforms(font_bytes: Uint8Array, transforms_json: string, avar2_csv: string): Uint8Array;

export function compile_glyphs(source: string): Uint8Array;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly add_avar2: (a: number, b: number, c: number, d: number, e: number, f: number) => [number, number, number, number];
    readonly apply_control_axes: (a: number, b: number, c: number, d: number) => [number, number, number, number];
    readonly apply_grade: (a: number, b: number, c: number, d: number, e: number, f: number) => [number, number, number, number];
    readonly apply_transforms: (a: number, b: number, c: number, d: number, e: number, f: number) => [number, number, number, number];
    readonly compile_glyphs: (a: number, b: number) => [number, number, number, number];
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __externref_table_dealloc: (a: number) => void;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
