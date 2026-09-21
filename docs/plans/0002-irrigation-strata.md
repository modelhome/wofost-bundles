# Plan: Irrigation strata

Source brief: docs/features/0002-irrigation-strata.md
Status: implemented
Planned against commit: f53e84aee6e990623d91cc8868e274ae966206db
Base commit: f53e84aee6e990623d91cc8868e274ae966206db (branch `feat/0002-irrigation-strata`)

## Outcome

`corn-yield/` runs on node 1's current twelve `region_key` values instead of the
ten it was built against, and `ne_irrigated` and `ks_irrigated` are simulated as
irrigated corn — soil-moisture-triggered irrigation inside the same
`Wofost72_WLP_FD` engine — rather than as dryland corn that happens to sit in the
High Plains. The eight unsplit states and the two rainfed strata behave exactly
as they do today. A four-step flow (weather -> yield -> price -> trade) gets past
node 2, and node 2's committed baseline medians stop being dryland simulations of
states whose corn is substantially irrigated.

## Scope

### In scope

- Re-key `soils.csv`, `planting_dates.csv`, `climatology.csv` and
  `baseline_yields.csv` on node 1's twelve keys, read from
  `agromet-bundles/crop-weather/regions.csv`.
- A new committed, sourced `water_regime.csv` declaring each region's regime and
  its irrigation parameters.
- Soil-moisture-triggered irrigation for the two irrigated strata, in both the
  run-time projection and the precomputed baseline.
- Rebuild `climatology.csv` and `baseline_yields.csv` for the four new keys at
  their own stratum coordinates, over the unchanged 1995-2024 period.
- Per-region regime and baseline vintage in the output metadata.
- README, `Modelfile.toml` annotations and `CLAUDE.md` updated; `check_yield.py`
  extended.

### Out of scope

- Node 3's `production_weights.csv` and `yield_history.csv` (a separate brief in
  `ag-commodity-bundles`, which cannot start until this lands).
- Node 1: its region set, stratum weights and apportionment method are given.
- Splitting any state beyond Nebraska and Kansas.
- gNATSGO/SSURGO soils, the silking-heat overlay, crop-reporting-district
  granularity.
- Any change to the output document's shape or its `required` key sets.

## Assumptions and decisions

Four questions were put to John while framing this brief. All four were answered;
the answers are binding on `run`.

### D-1. The irrigated stratum is simulated with a soil-moisture trigger (brief option 2)

**Answer: option 2, SM-triggered irrigation inside `Wofost72_WLP_FD`.**

Verified against the pinned PCSE 6.0.13 (`pcse/agromanager.py:333-390`,
`StateEventsDispatcher`): `event_signal: irrigate`, `event_state: SM`,
`zero_condition: falling` is supported, so this needs no change to the PCSE pin
and no change of engine class. The water balance stays live, so the irrigated
stratum keeps a drought signal — which is what node 2 exists for. Option 1
(`Wofost72_PP`) would erase that signal and would make AC-5's yield-gap check
pass for the wrong reason; option 3 would irrigate identically in a wet year and
a dry one.

Parameters, all declared per row in `water_regime.csv` with their source text:

| Parameter | Value | Basis |
|---|---|---|
| `trigger_depletion_fraction` | 0.50 | Management-allowed depletion for corn: UNL Extension NebGuide **G1850**, *Irrigation Management for Corn* (verified at planning time). Trigger `SM` is derived, not stored: `SMW + (1 - 0.50) * (SMFCF - SMW)`. |
| `irrigation_amount_cm` | 2.54 **gross** | One inch per application, mid-range of G1850's 0.75-1.3 in for medium- and fine-textured soils. An *applied* depth, so it maps onto PCSE's `amount` directly; at 0.85 efficiency the soil receives 2.159 cm net. Well inside the profile's total available water (measured: 19.2 cm for `ne`, 21.6 cm for `ks`), so an application refills rather than drains away. |
| `efficiency` | 0.85 | The centre-pivot efficiency K-State Research and Extension **L915** assumes for general planning; G1850 gives 85-90% for well-maintained systems. PCSE multiplies it in directly (`_RIRR = amount * efficiency`). |

**`irrigation_amount` is in centimetres, not millimetres.**
`pcse/soil/classic_waterbalance.py:633` sets `self._RIRR = amount * efficiency`
and line 209 documents `RIRR` as `cmday-1`. PCSE's own `agromanager` docstring
example (`0.15: {irrigation_amount: 20}`) reads as though it were mm; taken as mm
it would apply 200 mm per event. This is precisely the unit slip `CLAUDE.md`
warns about, and it would silently inflate the irrigated strata rather than fail.
The column is therefore named `irrigation_amount_cm`, and a check asserts a
single application is smaller than the profile's plant-available water.

**Corrected by measurement — see C-4.** The claim above that an mm/cm slip "would
silently inflate the irrigated strata" is wrong. Measured, a 25.4 run produced a
yield identical to the 2.54 run to one decimal place, because a free-draining
balance simply drains the excess. The slip is undetectable in the output, which
is why the check asserts on the declared value rather than on a downstream
number.

### D-2. The regime lives in a new `water_regime.csv`

**Answer: a separate table beside `soils.csv`.** The irrigation parameters are
crop management, not soil water retention, and `soils.csv`'s per-row method and
source text is entirely about texture-class retention. `water_regime.csv` follows
the hand-curated pattern of `soils.csv` and `planting_dates.csv` — every row
carries its own `method` and `source` — rather than the built-table pattern, so
it needs no `.meta.json`. Cost: one more `COPY` line in the Dockerfile and one
more loud-failure path.

### D-3. AC-5 asserts sign and order of magnitude

**Answer: positive in both states, within a wide documented band (25-200%), with
the exact simulated figures printed and recorded in the README.** NASS's
operation-level classes compare irrigating operations with non-irrigating ones,
confounded with soil quality and management; this is a two-point simulation
differing in regime and weather. They are not the same estimand, so a tight band
would be false precision and would invite exactly the tuning the brief forbids.
The band is documented in the check and in the README as a sanity bound, not a
calibration target. A result outside it is a finding to report in the pull
request, not a number to adjust.

### D-4. The stratum rows inherit their state's soil texture

**Answer: inherit.** `ks_irrigated` and `ks_rainfed` both take today's `ks` silt
loam; `ne_irrigated` and `ne_rainfed` both take today's `ne` loam. The existing
`WAV = 0.50 x (SMFCF - SMW) x RDMSOL` western fraction rule carries over
unchanged. The only deliberate differences between a state's two strata are then
the regime and the weather at their own points, which keeps the AC-5 gap
attributable rather than confounded between soil and regime. Real per-stratum
soils remain the out-of-scope gNATSGO/SSURGO follow-up.

### Other material assumptions

- **A-1. Old `ne` and `ks` rows are removed, not retained.** AC-1 says the tables
  are keyed on the twelve current values, and node 1 emits only those. Keeping
  dead rows would let a stale input pass quietly.
- **A-2. The irrigation agromanagement builder lives in `runner.py` and
  `build_baselines.py` imports it.** `build_baselines.py` today duplicates
  `SOIL_PARAMETERS`, `CROP_NAME`, `MAX_DURATION` and the whole agromanagement
  dict (`build_baselines.py:74-76,168-182` against `runner.py:90-95,453-465`).
  Duplicating the irrigation logic too would risk the baseline and the projection
  diverging in something other than weather, which is the one thing the anomaly
  may not tolerate. `runner.py` is in the image and `build_baselines.py` is not,
  so the dependency runs the safe direction. This is the minimum shared surface —
  the builder and the regime loader, nothing else.
- **A-3. The eight unchanged regions' committed numbers must not move.**
  `build_climatology.py` caches per region as `<key>-1995-2024.json`, so the four
  new keys fetch and the eight existing keys are rebuilt from cache and asserted
  byte-identical. Any drift is a build error, not a diff to accept.
- **A-4. AC-6 needs a real twelve-region node 1 output.** `agromet-bundles` is
  present locally at commit `0618aed`, which is the commit that introduced the
  strata, so `run` generates the input by running node 1 rather than hand-editing
  the sample. That run needs network (Open-Meteo); node 2 itself stays offline.
  The result replaces `corn-yield/sample_input.json`, whose current sample is two
  regions (`ia`, `ne`) and no longer resolves.
- **A-5. Rebuild cost is real.** Four regions x 30 years of ERA5, plus 120 WOFOST
  baseline runs, plus a 12-region projection. Budget tens of minutes, not
  seconds.

### Acceptance-criteria numbering

The brief's IDs AC-1 to AC-12 are sequential and unambiguous; they are preserved
verbatim. No renumbering was needed.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | Four tables keyed on node 1's twelve keys; eight unsplit rows unchanged | `soils.csv`, `planting_dates.csv`, `climatology.csv`, `baseline_yields.csv` re-keyed; old `ne`/`ks` rows removed | `check_tables` reads node 1's real `regions.csv` and asserts each table both covers and does not exceed its twelve keys; unsplit rows diffed against `HEAD` — 8/8 identical in both hand-curated tables | **pass** |
| AC-2 | A committed, sourced table declares the regime; nothing infers it from key spelling | new `water_regime.csv` (12 rows, per-row method and source); `runner.load_water_regime` via `read_csv_keyed` | `check_regime_table` — regime valid and sourced for all 12, exactly `{ne_irrigated, ks_irrigated}` irrigated, and a source scan asserting no `==`/`endswith`/`startswith` on `_irrigated`/`_rainfed` in `runner.py` or `build_baselines.py` | **pass** |
| AC-3 | Irrigated strata run irrigated; unsplit states and rainfed strata identical to today | `agromanagement_for` returns the pre-0002 campaign unchanged for rainfed | `check_unsplit_regions_unchanged` against `unsplit_regression.json`, captured at base commit `f53e84a` — all 21 fields identical for all 8 states | **pass** |
| AC-4 | Climatology and baselines rebuilt for the four new keys at their own coordinates, 1995-2024, by the existing scripts | `build_climatology.py` (unchanged), `build_baselines.py` (regime-aware) | Rebuilt: `climatology.csv` 4,380 rows/12 keys, `baseline_yields.csv` 360 rows/12 keys, period `1995-2024`; 2,920 unsplit climatology rows and 240 unsplit baseline years reproduced **exactly** | **pass** |
| AC-5 | Gap positive in both states and within a documented tolerance of the NASS gap | `water_regime.csv` parameters (0.50 / 2.54 cm / 0.85) | `check_irrigation_gap` — **ks +133.2%** (NASS +105%), **ne +57.4%** (NASS +55%); both positive, both inside the 25-200% band of D-3. Plus the cm/mm guard: one application < profile available water | **pass** |
| AC-6 | A run on a real twelve-region node 1 output succeeds with plausible DVS and anomaly for all twelve | new `sample_input.json` — node 1 at `0618aed`, 3,324 rows | Run succeeded; all 12 reach maturity (DVS 2.0); anomalies -43.74% to +57.76%; `check_wofost_run` passes over 12 rows | **pass** |
| AC-7 | An unknown `region_key` still fails loudly, in every table | existing `process_region` guard extended to `water_regime.csv` | `check_unknown_region_fails` (subprocess) plus new `check_every_table_fails_loudly`, which drops the region from each of the five tables in turn and asserts the `RunError` names that table | **pass** |
| AC-8 | Metadata names, per region, the regime used and the baseline vintage | `build_metadata` `water_regime` block; `baselines.meta.json` now records `regime` per region | `check_baseline_regime_matches` — recorded baseline regime equals the run's regime for all 12; `check_schema_honesty` extended | **pass** |
| AC-9 | `docker build` succeeds; `docker run --network none` reproduces identical output apart from `generated_at` | `Dockerfile` `COPY water_regime.csv` | Built and run offline: rows, columns and trajectory **identical**; metadata differs in nothing but `generated_at`; `crop_parameters_pin_verified: true` at `f0a6491f2368` | **pass** |
| AC-10 | `validity_domain` no longer says rainfed throughout; validator passes with no annotation warnings | `Modelfile.toml` `validity_domain` (591/600) and `provenance` (367/400) | `python -m orchestration.modelfile validate` → `OK`. Also re-confirmed `check_schema_compatibility`: binds to `crop_weather_daily`, correctly refused by `crop_weather_summary` | **pass** |
| AC-11 | README documents the table, mechanism, parameters, limits, rebuilt baselines and the node 3 breaking change | `corn-yield/README.md` — new `water_regime.csv` section, rewritten limitations, breaking-change notice at the top | Review; measured figures recorded in the Validation section | **pass** |
| AC-12 | `check_yield.py` passes in full, including the existing checks, with ten-region assumptions updated not deleted | `check_yield.py` — `check_tables` rewritten, four checks added, none removed | **379/379 pass** (baseline 83; the rise is per-region checks over 12 regions instead of 2, plus the new checks) | **pass** |

## Verification

The repository has no test runner; `check_yield.py` is the bundle's committed
validation and is the baseline. It is run outside the image and takes a snapshot
output as its argument.

| Command | Purpose | Baseline result | Final result | Assessment |
|---|---|---|---|---|
| `python runner.py sample_input.json > run/corn_yield_snapshot.output.json` | The model runs end to end, JSON on stdout only | pass — 2 regions (ia, ne); ia +22.02% pct 70, ne +51.9% pct 80, reproducing `CLAUDE.md` | pass — 12 regions, all reach maturity, ~1 s | improved (scope: 2 -> 12 regions) |
| `python check_yield.py run/corn_yield_snapshot.output.json` | The bundle's committed validation | **83/83 pass** | **379/379 pass** | improved |
| AC-3 fixture: pre-change figures for the eight unsplit states | Regression surface | captured at `f53e84a`, committed as `unsplit_regression.json` | all 8 states, all 21 fields identical | unchanged (as required) |
| `build_climatology.py <regions.csv>` | Rebuild normals for 12 keys | n/a (10-key file committed) | 4,380 rows/12 keys; 2,920 unsplit rows byte-identical | unchanged for unsplit |
| `build_baselines.py <regions.csv> <pinned checkout>` | Rebuild 30-year baselines for 12 keys | n/a (10-key file committed) | 360 rows/12 keys; 240 unsplit years byte-identical | unchanged for unsplit |
| `docker build … && docker run --rm --network none …` | AC-9: builds from the bundle folder, runs offline | build pass (pre-change image) | build pass; output identical but `generated_at` | unchanged |
| `python -m orchestration.modelfile validate Modelfile.toml` | AC-10 | not run pre-change | `OK`, no annotation warnings | pass |

**Baseline note — 83 checks, not the 82 `CLAUDE.md` records.** `check_crop_parameter_pin`
(`check_yield.py:361-376`) runs a one-check branch when the crop-parameter commit
is unknowable and a two-check branch when it is pinned and verifiable. `run`
cloned `WOFOST_crop_parameters` at the pinned `f0a6491f` into the gitignored
`.crop-parameters-cache/` and linked it, so the verifiable branch runs. 83 is
therefore the stricter local baseline; 82 remains correct for an off-platform
checkout without the pinned copy. AC-12 is assessed against 83.

The environment is a scratch venv carrying the Dockerfile's exact pins
(`pcse==6.0.13`, `numpy==2.5.3`, `pandas==3.0.6`, …). The repository provides no
other test runner; `check_yield.py` is the bundle's committed validation.

Baseline caveat: the pre-change baseline must be captured **before** the tables
are re-keyed, because the committed `sample_input.json` is a two-region document
that the new tables will still serve but the new sample will replace. `run`
records the ten-region snapshot as the AC-3 comparison fixture in its first step.

## Conflicts found while building

Recorded as they were hit, per the skill's rule that implementation does not
silently redesign around a plan that turns out to be incomplete. None of these
changed the plan's decisions; all three are things D-1 assumed would be
straightforward and were not.

### C-1. PCSE's documented irrigation keyword does not exist

`agromanagement_for` first used `irrigation_amount`, the keyword in every
`pcse.agromanager` docstring example. The first time the trigger fired, PCSE
raised:

```
TypeError: WaterbalanceFD._on_IRRIGATE() missing 1 required positional argument: 'amount'
```

The events-table values are splatted verbatim into
`WaterbalanceFD._on_IRRIGATE(self, amount, efficiency)`, so the real keyword is
`amount`. Grepping the pinned 6.0.13 confirms `irrigation_amount` appears **only
in docstrings** and never in executable code; `pcse/signals.py:173` documents the
real signature. This is an upstream documentation bug, and following the
documentation would have made every irrigated run fail. Fixed, with the reason
in a comment beside the events table so it is not "corrected" back later.

`signals.py:177` also independently confirms the unit — "Amount of irrigation in
cm water" — which is the second source for D-1's cm finding.

### C-2. A campaign with StateEvents needs a trailing empty campaign

PCSE refused the first valid-keyword attempt with:

> In the AgroManagement definition, the last campaign with start date ... contains
> StateEvents. When specifying StateEvents, the end date of the campaign must be
> explicitly given by a trailing empty campaign.

`AgroManager.end_date` will infer an end date from a crop calendar but not from a
state event, so an irrigated region's agromanagement is now
`[{planting_day: campaign}, {planting_day + max_duration: None}]`. The trailing
date equals the `crop_end_date` the calendar already carries, so this is a
required declaration rather than a change to when the run stops. Rainfed regions
keep the single-campaign definition unchanged, which is what protects AC-3.

### C-3. The trailing campaign makes the engine run past maturity

A consequence of C-2, and the one with teeth. With an end date declared, the
engine keeps stepping the water balance after the crop matures, emitting rows
whose crop variables are all `None`: for Iowa, 201 daily rows instead of 113, the
last 88 with no crop. `mean_water_stress` hit
`TypeError: unsupported operand type(s) for +: 'float' and 'NoneType'`, and the
stage, the stress windows and the trajectory would each have been affected
differently.

`run_wofost` now returns only days on which the crop was in the ground
(`DVS is not None`). For a rainfed region this removes nothing — the run already
stops at maturity — which is why the eight unsplit states stay bit-for-bit
identical. Summary output (`TWSO`, `DOM`) was correct throughout; only the daily
series was affected.

### C-4. The mm/cm slip is invisible in the output

D-1 predicted that entering 25.4 instead of 2.54 would "silently inflate the
irrigated strata". Measured, it does not: on a free-draining water balance the
excess simply drains, and a 25.4 run produced a yield identical to the 2.54 run
to one decimal place. The slip is therefore *worse* than predicted in one sense —
nothing downstream looks wrong at all — and harmless to yield in another. The
README now says this accurately, and AC-5's guard asserts on the **declared
value** (one application must be smaller than the profile's plant-available
water) rather than expecting a wrong number to surface.

### C-5. Open-Meteo's hourly limit blocked the climatology rebuild

The twelve-region rebuild fetched eleven regions and then failed on `mo` with
`HTTP 429: Hourly API request limit exceeded`. `build_climatology.py` writes its
output only after all regions succeed, so `climatology.csv` was left untouched
rather than half-rebuilt. The eleven fetched series are cached in
`.climatology-cache/`, so a retry needs only `mo`. Retried on a timer; see the
verification table for the outcome. This is an environmental rate limit, not a
defect in the change.

### C-6. Copilot review (PR #2): two findings, both valid

**Finding 1 -- the baseline regime was recorded but never enforced.** AC-8 put
each region's baseline regime in the output metadata and `check_yield.py`
compared it, but `check_yield.py` is a development tool and is not in the image.
Nothing in the runner itself stopped a rainfed-built baseline being used for an
irrigated projection -- the exact silent wrong answer `check_crop_parameters_pin`
exists to prevent, one field over. Added `check_baseline_regimes`, called once
before any projection runs so a mismatch costs no simulation. A baseline that
records no regime at all (one built before `water_regime.csv` existed) is treated
as a mismatch rather than assumed rainfed. Covered by
`check_baseline_regime_mismatch_fails`, which doctors the metadata both ways.

**Finding 2 -- `irrigation_amount_cm` was labelled NET, and is GROSS.** PCSE adds
`amount x efficiency` to the soil, so 2.54 cm at 0.85 efficiency delivers 2.159
cm net. Calling the committed value net made the provenance inconsistent with the
simulated management. G1850's 0.75-1.3 inch application depth is an *applied*
depth, so the value maps onto PCSE's `amount` correctly and only the label was
wrong: relabelled as gross in `water_regime.csv`, the runner comment, the README
and this plan, with the net depth stated. **No number changed**, and the snapshot
rows are byte-identical to the pre-review run.

Checks went 372 -> **379/379**.

## Implementation steps

1. **Capture the pre-change baseline.** On the base commit, run the model on the
   current `sample_input.json` and `check_yield.py`, and record the per-region
   figures for the ten regions in a scratch fixture. This is the AC-3 evidence
   that unchanged regions did not move; it cannot be reconstructed later.
2. **Generate the twelve-region input.** Run node 1 (`agromet-bundles` at
   `0618aed`) and commit its output as the new `corn-yield/sample_input.json`.
   Needs network; node 2 stays offline.
3. **Write `water_regime.csv`.** Twelve rows: `region_key`, `regime`
   (`rainfed`/`irrigated`), `trigger_depletion_fraction`,
   `irrigation_amount_cm`, `efficiency`, `method`, `source`. Irrigation columns
   are empty for rainfed rows, which fall back the same way a missing value does.
   Per-row method and source text as D-1 records.
4. **Re-key `soils.csv` and `planting_dates.csv`.** Remove `ne` and `ks`; add the
   four stratum rows, inheriting their state's texture, `WAV` rule, planting date
   and maturity class per D-4. Eight unsplit rows untouched.
5. **Teach `runner.py` the regime.** Add `load_water_regime()` and
   `agromanagement_for(planting_day, variety, max_duration, regime_row)`, which
   returns today's dict for a rainfed region and adds the `StateEvents` block for
   an irrigated one, computing the trigger `SM` from the soil row. A region with
   no regime row fails loudly (AC-7). No inference from key spelling (AC-2).
6. **Teach `build_baselines.py` the same regime**, by importing the builder from
   `runner.py` per A-2 rather than duplicating it.
7. **Rebuild `climatology.csv`** for the twelve keys; assert the eight unchanged
   regions come back byte-identical (A-3). Refresh `climatology.meta.json`.
8. **Rebuild `baseline_yields.csv`** for the twelve keys against the pinned
   crop-parameter checkout; assert the eight unchanged regions reproduce their
   committed numbers exactly. Refresh `baselines.meta.json`.
9. **Extend `check_yield.py`**: rewrite `check_tables` to read node 1's
   `regions.csv`; add `check_regime_table`, `check_rainfed_unchanged`,
   `check_irrigation_gap`; extend `check_unknown_region_fails` and
   `check_schema_honesty`. Update, do not delete, ten-region assumptions (AC-12).
10. **Metadata and annotations.** Per-region regime and baseline vintage in
    `build_metadata` (AC-8); rewrite `Modelfile.toml`'s `validity_domain` and
    `not_for` (AC-10), staying inside the 600-character cap.
11. **Dockerfile.** `COPY water_regime.csv`; rebuild and verify offline (AC-9).
12. **Documentation.** `corn-yield/README.md` per AC-11, including the upper-bound
    caveat (no aquifer decline, no allocation limit, no pumping-capacity ceiling
    in extreme heat) and the node 3 breaking change. Update the repo `CLAUDE.md`
    region set, engine description, verified-results and task-list sections.
13. **Pull request**, stating the breaking change for node 3 and the sequencing:
    this brief, then node 3's, then re-register both on Model Home.

## Files likely to change

```
corn-yield/water_regime.csv        new
corn-yield/soils.csv               re-keyed
corn-yield/planting_dates.csv      re-keyed
corn-yield/climatology.csv         rebuilt (12 keys)
corn-yield/climatology.meta.json   rebuilt
corn-yield/baseline_yields.csv     rebuilt (12 keys)
corn-yield/baselines.meta.json     rebuilt
corn-yield/sample_input.json       replaced (12-region node 1 output)
corn-yield/runner.py               regime loader, agromanagement builder, metadata
corn-yield/build_baselines.py      imports the shared builder
corn-yield/build_climatology.py    likely unchanged; verify
corn-yield/check_yield.py          rewritten table check, three new checks
corn-yield/Dockerfile              COPY water_regime.csv
corn-yield/Modelfile.toml          validity_domain, not_for
corn-yield/README.md               regime, mechanism, limits, breaking change
CLAUDE.md                          region set, engine, verified results, tasks
docs/plans/0002-irrigation-strata.md  status and final results
```

## Risks and follow-ups

- **The trap the brief names.** `ks_irrigated` sits 266 m higher and 2.2 degrees
  west of `ks_rainfed`. If the regime wiring silently fails, that stratum is
  simulated rainfed at a drier point and comes out *worse* than `ks_rainfed`,
  inverting the real ordering. AC-5's sign assertion is the tripwire; it must fail
  loudly rather than warn.
- **Unit slip on `irrigation_amount`** — see D-1. The cm/mm confusion is
  introduced by PCSE's own docstring, and taken as mm would apply 200 mm per
  event while still producing plausible-looking output.
- **A stratum is not "the better half".** Nothing in the implementation or the
  documentation may generalise irrigated to better; node 1 records that the gap
  flips sign in Iowa and Ohio.
- **AC-5 may fail on first measurement.** Per D-3 that is a finding for the pull
  request, not a parameter to tune. If the gap lands outside 25-200%, `run` stops
  and reports rather than adjusting `trigger_depletion_fraction`.
- **Breaking change for node 3.** Merging this without the matching
  `ag-commodity-bundles` brief leaves the four-step flow failing one step later
  than today. Must be stated in the PR.
- **Follow-ups unchanged:** gNATSGO/SSURGO per-stratum soils, a parameterised
  silking-heat overlay, crop-reporting-district granularity. This plan adds one:
  irrigation is modelled as unconstrained supply, so an aquifer-decline or
  allocation-limit constraint is the natural next brief for the west.
- **AC-10 on-platform import** remains unverified from this session, as it did
  for brief 0001 — it needs a signed-in human at the Auth0 login.
