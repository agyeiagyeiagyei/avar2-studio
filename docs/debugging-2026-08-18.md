# Debugging record — 18 Aug 2026 (avar2 mapping, reflection, grade)

A one-day hunt through the static app while the designer authored
crispy-demo-14aug's mapping (wght/wdth, then opsz, then grade + SPAC).
Every bug below shipped fixed the same day; each section is
symptom → diagnosis → root cause → fix → proof, with the commit.
Kept as a reference because several fixes overturn claims made
elsewhere in the docs (noted inline), and because the diagnosis
*methods* are reusable.

The working branch is `github-pages` (push = deploy). All commits
below are on it, `e06f7b0..8a556e4`.

---

## 1. The dead default cross

**Symptom.** User axes (wght 100–900, wdth 5–200, defaults 400/100)
"started working" only past their defaults; at the default location the
parametric sliders and outlines sat at the hairline (47/1/1); dragging
wght at wdth 100 did nothing, then everything snapped alive past it.

**Diagnosis.** Replayed the exact mappings CSV through fontTools'
`VariationModel` — the model `add_avar2` ports — and printed the mapped
surface over a grid of locations (`fontTools.varLib.models`, ~40-line
script). No code reading, just numbers.

**Root cause.** avar2 is an ItemVariationStore: deltas from the default,
weighted by tents built from the CSV rows' normalized input locations.
With rows only at the four extreme corners, every tent's near edge sat
exactly at normalized 0 — so any location with *either* axis at its
default scored zero on *every* tent. The whole default cross was
unmapped, and the default location itself is structurally pinned to the
compiled origin master (a VarStore has no base-value slot).

Two corollaries proved numerically at the same time:

- A "Regular" row authored at the all-default input location is
  **silently discarded** by `add_avar2` (`is_default()` region skip in
  `lib.rs`) *and* skews every other row, because fontTools computes all
  deltas relative to that base row.
- With the input defaults at the range minimum instead (the derived
  default when no axis metadata is given), four corner rows fully cover
  a 2-axis space — the cross only dies when a default sits mid-range.

**Fix.** Author the `{min, default, max}ⁿ` grid minus the centre. For
the 2-axis font: 8 rows, the four new ones bilinear interpolants of the
authored corners (visually a no-op that makes the mid-lines
addressable).

**Proof.** Max |authored − rendered| over all 8 rows = 0.000 under both
default scenarios; the previously dead points render their interpolated
values.

---

## 2. Authoring guard rails (`avar2-lint.js`)

Not a bug fix — the tooling the bug demanded. A pure-JS lint that
mirrors `add_avar2`'s normalization and range-derivation exactly:

- `avar2-default-row` — a row at the all-default input location whose
  outputs would be discarded (and skew the rest). Move the origin in
  the source instead.
- `unmapped-mapping-point` — missing `{min, default, max}ⁿ` grid
  points.

Wired into upload, session restore, pin refresh, every authoring regen,
and config-bundle import warnings; findings land in the Space tab rail.
Deliberately a warning rail, not a hard error — CrispyMini's shipped
CSV genuinely contains the default-row wart (`Narrow Thin 144`,
HANDOVER §4.10). 20-case spec: `e2e/avar2-lint.spec.mjs`.
Commit `09b5b5c`; refined in §10 below (`8a556e4`).

---

## 3. Stale `mappingsCsv` — the vanishing opsz axis

**Symptom.** "I don't see the opsz axis in preview — is this because of
the missing corner?" (It wasn't: a missing mapping corner can flatten
outputs but can never remove an axis.)

**Diagnosis.** The user's exported config bundle was the evidence: its
`avar2_csv` carried the OPSZ column, but `source.axes` had no opsz —
impossible unless two copies of the CSV had diverged. Traced every
read/write of `dataset.mappingsCsv` in `static-api.js`.

**Root cause.** `regenerateFont` (every authoring mutation) built the
avar2 store from a fresh `serializeMappingsCsv(instancesCsv)` but never
wrote it back to `dataset.mappingsCsv`. Full rebuilds
(`rebuildUploadFont`: transform toggles, grade edits, REBUILD) and
`refreshAxesFromFont`'s user-axis categorization read the stale copy —
so a newly added axis column survived only until the next full rebuild
silently dropped it from the fvar. The user's enabled width-aware SPAC
transform was the trigger.

**Fix.** Keep `dataset.mappingsCsv` in step in `regenerateFont`.
Shipped with three authoring nudges in the same commit (`8f99b01`):
config bundles carry an optional `axis_metadata` section (a non-min
default used to silently revert to min on reimport, inverting the
mapping's neutral plane); Add Axis scaffolds rows at the new axis's
non-default extreme(s) with outputs copied; unmapped-grid-point
findings get an **Add row** action that pre-fills the row with the
surface's current avar2-evaluated value (a behavior-pinning no-op to
edit).

---

## 4. Parametric reflection dead on the live site

**Symptom.** The Preview tab's parametric sliders (which display the
avar2-mapped location as user axes move) never moved — reported by the
user after being told, wrongly, that the path was verified. The code
path *was* wired; the bottom of the stack was broken.

**Diagnosis.** Playwright against the deployed site, driving the real
UI: upload the .glyphs, import the config, drag wght, read the slider
values. Reproduced immediately (sliders frozen). The failure was
invisible in earlier runs because it surfaced as `console.warn` — the
second run captured *all* console traffic:

    [PreviewTab] getMappedLocation failed:
    Error: parseAvar2: offset 330327 out of bounds (len=7084)

Then extracted the actual built font bytes from the live session (an
`addInitScript` hook on `URL.createObjectURL`), dumped the avar table
with fontTools for ground truth, and diffed the JS parser's structures
against it byte for byte.

**Root cause — three spec violations in `avar2-eval.js`:**

1. The parser assumed the DeltaSetIndexMap and ItemVariationStore were
   laid out **inline** after the v1 segment maps. The avar v2 header
   has two `Offset32` fields there (table-relative offsets to each
   structure). The parser read the offset fields *as map data* and
   wandered into garbage; the bounds check added during the Aug-14
   debug session turned that into a throw; PreviewTab's catch fell
   back to inputs. Sliders froze.
2. `tentFactor` lacked the OT rule that **peak = 0 means the axis does
   not participate** (factor 1). After fixing the parse, every
   multi-axis region still died the moment a non-participating axis
   sat off-default. (Same semantics as `coverage.js`'s
   `supportScalar`, which had it right.)
3. The DSIM decode ignored `entryFormat` (hardcoded nibble split) and
   didn't clamp axis indices to the last entry (write-fonts trims
   trailing duplicates). Also: axes appended after the avar table was
   written (SPAC/GRAD/control) now reflect their input instead of NaN,
   and `wordDeltaCount` masks the LONG_WORDS flag.

**Why no test caught it.** The suite's reflection check only verified
the *default* value displayed — it never moved a user axis, so a
parser that failed and fell back to inputs passed it. Section 7 now
drags wght and asserts the parametric display moves.

**Fix + proof.** Commit `feaeb7e`. Oracle spec
`e2e/avar2-eval.spec.mjs` pins six mapped locations on a real
wasm-built table against the fontTools model; the same Playwright test
then passed against production (exact model values: wght 900 →
XOPQ 143.5/YOPQ 193.6; +wdth 200 → 1665/700/275), including with SPAC
and GRAD grown into the fvar.

**Supersedes** HANDOVER §4.3's claim that `avar2-eval.js` "parses only
wasm-written avar2 tables": the parser is now spec-layout. Whether it
handles the server-written layout has not been re-verified — test
before relying on it.

---

## 5. Grade parity audit — five client gaps

Requested as "ensure we retained all the previous functionality" vs the
server app. The model itself (grade.py's pure-weight maths,
equalisation rounding, braces at GRAD ±10) was faithfully ported and
oracle-tested; the gaps were all in the static client
(commit `909088f`):

1. Graded instance rows previewed at `GRAD: pct` — 0.25 on a −10..+10
   axis, 1/40th of the grade (an Aug-14 change). Interim fix: GRAD max;
   final fix in §7.
2. `max_pct` slider caps were never computed (server:
   `_grade_state_payload` / `max_pct_for`). Ported to
   `grade-model.js`, oracle-tested against the Python to 1e-9;
   badges show "max N% here" again.
3. The workspace zip dropped grade state entirely (not written, not
   read). Now a `<stem>-grade.json` sidecar, the server convention.
4. Config import discarded the grade declaration when the toggle was
   off — grades must persist across the toggle (`save_all` semantics).
5. `deleteInstance` left orphaned grade entries; `setInstanceGrade`
   with pct omitted removed instead of seeding the default.

---

## 6. Grade at the parametric ceiling — no darkening, deformed spacing

**Symptom.** "The grade slider doesn't show any outline difference, and
at very high percentages actually deforms the spacing."

**Root cause.** `grade_coords` computed the follower move
(`dXTRA = COMP_RATIO × dXOPQ`) from the **requested** stem move, then
clamped each axis independently. Grading an instance at the parametric
box ceiling (Control Test, 1665/700/275): the drivers clamp to zero —
no darkening exists above XOPQ max — while XTRA still moved by the
full amount (pct 2.0 → XTRA 1665 → 265). The "dark" brace became a
pure counter-condense inside an equalisation-held advance: glyphs
squeeze, sidebearings balloon, worse as pct grows. `grade.py` had the
identical flaw — the server would deform the same way.

**Fix.** The follower tracks the **achieved** (clamped) driver move per
side, in both engines (`braces.rs` + `grade.py`), wasm rebuilt. At the
ceiling the grade degrades to a clean no-op; away from the edges the
achieved move equals the requested one, so existing values — and the
fontTools-instancer braces oracle — are unchanged by construction.

**Proof.** Commit `012ac99`; `e2e/grade-live.spec.mjs` drives the real
UI on the user's font: a mid-box grade (Bold Normal 30%) darkens with
the advance held to the pixel (703.1 → 703.1), and a ceiling grade at
the slider max changes nothing.

**Open seams, deliberately unchanged:** the `max_pct` cap filters zero
caps, so a zero-headroom instance still shows "max 200%" (now merely
misleading, no longer destructive — cap semantics are a model
decision); the static GRAD tuple is global along the axis (server
braces are scoped to the instance's parametric location), so multiple
grades sum — the designer prefers the global behavior for preview.

---

## 7. Graded rows rendering mangled at GRAD chip 0

**Symptom (screenshot).** A row graded 30% rendered with closed
counters while its chip said `GRAD: 0`.

**Cause: the §5.1 interim fix itself.** Rendering rows *at* their grade
(GRAD max) is truthful to the grade but contradicts the chip — and 30%
on Bold Normal is ~84% of its (YOPQ-bound) headroom, which legitimately
closes counters. Reverted to server behavior: rows render at GRAD 0,
the badge alone marks the grade, the Preview tab's GRAD slider is where
the grade is inspected. GRAD also moved from the parametric group into
**Transform axes** (it is transform-injected, like SPAC), per the
designer. Commit `2bf9f36`; pixel test asserts graded row ===
ungraded row.

Chasing the grouping closed a long-standing suite failure: §9's "SPAC
in the parametric group" was a **stale expectation** from the Aug-14
preview reorg (transform axes got their own section; the spec kept
looking in the old group and failed ever since). With that and the
export-modal sections passing, the e2e suite ran green end-to-end for
the first time.

---

## 8. "Inert axis" false positives in the Space rail

**Symptom.** Four "Inert axis" findings (wght/wdth/opsz/SPAC) above the
two real unmapped-grid findings.

**Cause.** `measure_at` hands raw normalized coords to skrifa's outline
drawing — it never evaluates avar2. User axes act *entirely through*
the mapping, so sweeping them measures nothing → false "inert" per
axis; SPAC moves advances, not outlines → area-inert by definition.

**Fix.** Sweeps cover only master-backed axes
(`gvarSweepAxes` in `static-api.js`). Commit `dfcc960`.
Possible upgrade, not taken: route `measure_at` through avar2 (skrifa
`Location`), which would make collapse detection meaningful in
user-axis terms. Wasm + oracle change; do it deliberately.

---

## 9. Lint over-claiming: unreachable vs unpinned grid points

**Symptom (user's catch).** "The font works without the two rows" —
the two remaining bold-caption corners rendered fine despite
`unmapped-mapping-point` findings claiming "falls back to the output
defaults."

**Cause.** "No row sits here" has two different outcomes the lint
conflated. When rows exist whose every *participating* (non-default)
coordinate sits exactly at the point's value, their tents have no
opinion on the point's other axes and still reach it — the surface is
the model's **additive extrapolation** of those rows (BC through wght
alone + Min Min Opsz through opsz alone). Plausible render, but
designer-unspecified, and it shifts silently when those rows change.
Only when *no* row reaches does the point truly collapse to the output
defaults. (A row at an intermediate position never reaches the exact
extreme — its tent ends there.)

**Fix.** Two severities: unreachable → `fail` (the dead cross);
reachable-but-unpinned → `info`, naming the rows it extrapolates from,
same Add-row action. Commit `8a556e4`.

---

## Regenerating the oracle fixtures (was undocumented)

The cargo oracle tests need fixtures under `/tmp` that existed on no
fresh machine:

```bash
# spike font: CrispyMini compiled without avar2 (fontc lives in the
# Crispy design repo's venv)
fontc --output-file /tmp/fontc-wasm-spike.ttf \
      examples/crispy-mini/sources/CrispyMini.glyphs

# gftools avar2 oracle
.venv/bin/python wasm/fontc-web/spike/build_oracle.py \
      /tmp/fontc-wasm-spike.ttf \
      examples/crispy-mini/sources/CrispyMini-avar.csv \
      /tmp/av2-oracle.ttf
```

Known fixture drift: `clamp_oracle.rs` compiles the *live*
`../../../Crispy/sources/Crispy.glyphs` and requires it to still
contain stranded sources — it fails vacuously as the design file
evolves. Environmental, not a code failure.

## Method notes

- **Reproduce with the real pipeline, never by code-reading alone**:
  fontTools model replays for mapping questions; Playwright driving
  the deployed UI for "it doesn't work" reports; extracting the actual
  font bytes from the live session when the table is the suspect.
- **Ground truth from a reference implementation** before editing:
  fontTools parse dumps, the Python grade model, the gftools/fontTools
  instancer oracles.
- **Fix every engine that shares the maths** (`grade.py` +
  `braces.rs`), and rebuild the wasm.
- **`git stash` bisection** cleanly separated new regressions (none)
  from pre-existing breakage (§7's stale spec, the export modal).
- **Every user report that contradicted a passing test exposed a
  vacuous test.** Three times: the reflection check that never moved a
  slider, the grade check that never looked at the pixels it asserted
  about, the group check aimed at a defunct section. Each fix shipped
  with the test that would have caught it.
