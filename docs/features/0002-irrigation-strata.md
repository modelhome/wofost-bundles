# Irrigation strata

## Outcome

`corn-yield/` runs on node 1's current twelve-region set instead of the ten it
was built against, and the two irrigated strata are simulated as irrigated corn
rather than as dryland corn that happens to sit in the High Plains.

Today the chain is broken. `agromet-bundles` commit `0618aed` split Nebraska and
Kansas into irrigated and rainfed strata, so node 1 now emits
`ne_irrigated, ne_rainfed, ks_irrigated, ks_rainfed` in place of `ne` and `ks`.
All four of node 2's committed tables are still keyed on the old ten, so a flow
run fails on the first unknown key it reaches:

```
input region 'ks_irrigated' is missing required soil data
```

When this is done, a four-step flow (weather -> yield -> price -> trade) gets
past node 2, and the yield signal for the west is no longer the single largest
known distortion in the chain. Node 2's committed baseline medians are ks 1,491
and ne 3,936 kg/ha against mn 10,131 -- dryland simulations of states whose corn
is substantially irrigated, recorded in `CLAUDE.md` as "a documented limitation,
not a bug". This brief is where that limitation stops being acceptable, because
node 1 has now given node 2 the information needed to fix it.

## Scope

### In scope

**Re-key the four committed tables** on node 1's current `region_key` set, read
from `agromet-bundles/crop-weather/regions.csv` rather than from memory:
`soils.csv`, `planting_dates.csv`, `climatology.csv`, `baseline_yields.csv`.
The eight unsplit states are unchanged; `ne` and `ks` each become two rows.

**A per-region water regime.** `soils.csv`, or a table beside it, gains a
declared regime per region so the runner knows which of its regions are
irrigated. The eight unsplit states and the two rainfed strata keep today's
behaviour exactly; only the two irrigated strata change. The regime is a
committed, sourced table row, not a heuristic on the region key's spelling.

**Irrigated simulation for the two irrigated strata.** Node 2 currently runs
`Wofost72_WLP_FD` for every region. The irrigated strata need a run that does
not starve them. Which mechanism -- potential production, a soil-moisture
trigger in agromanagement, or a fixed seasonal schedule -- is the plan's main
decision; see General guidance. Whatever is chosen is documented, parameterised
and cited, and the rainfed path is untouched.

**Rebuilt baselines for the changed regions.** A stratum's thirty-year
distribution has to be built under the same regime its projection uses, or the
anomaly compares two different models. `build_baselines.py` and
`build_climatology.py` are rerun for the four new keys. The baseline period stays
1995-2024: node 3 couples its `yield_history.csv` window to node 2's baseline
window and refuses to start on a mismatch, so the period is fixed by contract,
not by preference.

**New points, not reused ones.** The four strata have their own coordinates and
elevations in node 1's `regions.csv`, and Kansas's two sit 266 m apart in
elevation -- a 676 m High Plains irrigated point against a 410 m rainfed one.
Climatology and planting dates are rebuilt at the actual stratum points.

**Honest labelling of what irrigation does and does not protect.** The output
and the README say which regions were simulated irrigated, under what
assumption, and what that assumption leaves out -- at minimum that modelled
irrigation has no aquifer decline, no allocation limit and no pumping-capacity
ceiling in extreme heat, so the irrigated stratum's drought protection is an
upper bound.

**Validation that the split reproduces something known.** A committed check
that the two strata recombine sensibly and that the irrigated stratum's
simulated yield gap over the rainfed one has the right sign and rough size for
each state. NASS's operation-level classes put Kansas's irrigating operations
105 percent and Nebraska's 55 percent above non-irrigating ones; a simulation
that reverses or erases that gap has not fixed anything.

### Out of scope

- **Node 3's tables.** `corn-price/production_weights.csv` and
  `yield_history.csv` are also on the old ten keys and will fail loudly on the
  new ones. Splitting them is a separate brief in `ag-commodity-bundles`, and it
  cannot start until this one lands, because node 3 has to know what node 2
  emits. This brief must state the breaking change; it must not try to fix the
  other repo.
- **Node 1.** Its region set, its stratum weights and its apportionment method
  are node 1's and are taken as given.
- Splitting any state beyond Nebraska and Kansas. Node 1's 20 percent threshold
  decides that, and only those two qualify.
- The other follow-ups in the bundle README: gNATSGO/SSURGO soils, the
  parameterised silking-heat overlay, crop-reporting-district granularity.
- Any change to the output document's shape. Node 3 binds on node 2's required
  key set; more rows are fine, different keys are not.

## Acceptance criteria

- **AC-1** -- `soils.csv`, `planting_dates.csv`, `climatology.csv` and
  `baseline_yields.csv` are keyed on node 1's current twelve `region_key`
  values, taken from `agromet-bundles/crop-weather/regions.csv`. The eight
  unsplit states' rows are unchanged.
- **AC-2** -- A committed, sourced table declares each region's water regime.
  Nothing in the runner infers the regime from the spelling of a region key.
- **AC-3** -- The two irrigated strata are simulated under the chosen irrigated
  configuration; the eight unsplit states and the two rainfed strata produce
  output identical to today's for the same input, demonstrated by a committed
  check.
- **AC-4** -- `baseline_yields.csv` and `climatology.csv` are rebuilt for the
  four new keys at their own stratum coordinates, over the unchanged 1995-2024
  period, by the existing build scripts.
- **AC-5** -- The simulated irrigated-minus-rainfed yield gap is positive in
  both states and within a documented tolerance of the NASS operation-level gap
  (KS +105 percent, NE +55 percent), shown by a committed check. A failure here
  is a finding to report, not a number to tune.
- **AC-6** -- A run on a real node 1 twelve-region output succeeds end to end
  and produces a plausible DVS trajectory and anomaly for all twelve.
- **AC-7** -- An input `region_key` with no matching table row still fails the
  run loudly, in every table.
- **AC-8** -- The output metadata names, per region, the water regime used and
  the baseline vintage, so a reader can tell an irrigated run from a rainfed one
  without consulting the README.
- **AC-9** -- `docker build` from the bundle folder succeeds and `docker run`
  with `--network none` reproduces identical output apart from `generated_at`.
- **AC-10** -- The Modelfile's `validity_domain` no longer says the model is
  rainfed throughout, and states the new regime split and its limits. The
  validator passes with no annotation warnings.
- **AC-11** -- The bundle README documents the regime table and its source, the
  irrigation mechanism and its parameters, what the irrigation assumption leaves
  out, the rebuilt baselines and their period, and the breaking change for node
  3.
- **AC-12** -- `check_yield.py` passes in full, including the existing 82
  checks, with any that assumed a ten-region set updated rather than deleted.

## Constraints and dependencies

- **This is a breaking change for node 3.** `corn-price` raises on an unknown
  `region_key` by design. Merging this brief without the matching
  `ag-commodity-bundles` brief leaves the four-step flow failing one step later
  than it does today. Sequencing is: this brief, then node 3's, then re-register
  both on Model Home. Say so in the pull request.
- **A stratum is not "the better half".** Node 1's build script is explicit that
  the sign of the irrigated yield gap flips by state -- irrigating operations
  out-yield non-irrigating ones in Kansas and Nebraska, but under-yield them in
  Iowa and Ohio, where irrigation sits on sandy ground. Nothing here may assume
  irrigated means better in general.
- **The irrigated stratum is in a drier place.** Kansas's irrigated point is
  High Plains, 266 m higher and 2.2 degrees of longitude west of its rainfed
  one. Simulated rainfed, it would come out *worse* than `ks_rainfed` and invert
  the real ordering. That is the trap this brief exists to avoid, and it is also
  why "just add four rows to `soils.csv`" is not the fix, however much the
  platform's error message suggests it.
- **Baseline period is fixed by node 3's contract.** 1995-2024. Node 3 refuses
  to start when its `yield_history.csv` window and node 2's baseline window
  disagree.
- **No network at run time.** Unchanged. Rebuilt tables come from the one-time
  committed build scripts, and every rebuilt table ships its `*.meta.json`
  vintage.
- **PCSE version stays pinned** as it is today, and the crop-parameter checkout
  stays pinned. If the irrigated configuration needs a different engine class,
  it comes from the same pinned PCSE.
- **Determinism.** Rebuilding the baselines must be reproducible: rerunning the
  build for the eight unchanged states reproduces the committed numbers exactly.

Repo-wide conventions live in [`CLAUDE.md`](../../CLAUDE.md) and are not
restated here.

## General guidance

- Before you write the plan, ask any questions you need to. One is expected to
  be blocking:

  **How is the irrigated stratum simulated?** Three candidates, each with a real
  cost:

  1. **Potential production** (`Wofost72_PP`): simplest and defensible, since
     fully irrigated corn is close to water-unlimited. But it removes *all*
     water response, so a drought year shows no signal at all in that stratum
     and the national figure loses the part of the drought that irrigation
     cannot cover.
  2. **Soil-moisture-triggered irrigation** inside `Wofost72_WLP_FD`
     agromanagement: keeps a water balance and so keeps a drought signal, at the
     cost of trigger and application parameters that need sourcing.
  3. **Fixed seasonal schedule**: committed applications by date. Simple and
     sourceable from extension guidance, but insensitive to the season it is
     supposed to be responding to.

  Option 2 is the one that preserves what this node is for -- a weather-driven
  signal -- but it is also the one with the most to justify. Bring a
  recommendation and the parameter sources to the plan; do not pick silently.

- Read node 1's `build_regions.py` and its brief
  `docs/features/0003-irrigation-region-strata.md` before planning. The
  apportionment method, the 20 percent threshold, and the reason Missouri was
  deliberately left unsplit are all documented there and should not be
  re-derived.
- Read node 3's `CLAUDE.md` region-identity and period-coupling sections before
  deciding anything about keys or the baseline window.
- The eight unsplit states are a regression surface. Treat "identical output for
  unchanged regions" as a property to prove, not to assume.
