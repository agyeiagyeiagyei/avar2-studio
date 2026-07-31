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
 */
export function add_avar2(font_bytes: Uint8Array, mappings_csv: string): Uint8Array;

export function compile_glyphs(source: string): Uint8Array;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly add_avar2: (a: number, b: number, c: number, d: number) => [number, number, number, number];
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
