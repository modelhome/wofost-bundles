# Plan: Irrigation supply limits

Source brief: docs/features/0003-irrigation-supply-limits.md
Status: implemented (revised 2026-09-26 for R-1 and 2026-09-27 for R-2)
Planned against commit: 24d658b2779b5101141a1e4c082806f85334f0c3
Base commit: 24d658b2779b5101141a1e4c082806f85334f0c3 (branch `feat/0003-irrigation-supply-limits`)

## Outcome

`ne_irrigated` and `ks_irrigated` irrigate under declared, sourced supply limits
rather than an unlimited supply: a **pumping-capacity ceiling** in cm/day, and a
**seasonal allocation cap** that is declared per region and is, on the evidence
gathered while planning, a sourced "none" for both strata (see D-1). A hot dry
spell then costs the irrigated strata yield when their wells cannot keep up, the
output metadata says which limit bound, when, and how much water was applied,
and the README stops calling their drought protection an upper bound.

The ten rainfed regions do not change, and with the limits set to non-binding
values the irrigated strata reproduce brief 0002 exactly. Any change in the
delivered numbers is therefore the limits' doing.

## Scope

### In scope

- A daily irrigation controller that replaces brief 0002's `StateEvent` for
  irrigated regions only. It enforces both limits and records what it applied.
- Per-region limit columns in `water_regime.csv`, each sourced.
- A rebuilt baseline for the two irrigated strata under the limits, with the
  limits recorded in `baselines.meta.json` and enforced by the runner.
- Auditable per-region irrigation reporting in the output metadata (additive
  only).
- A capacity stress test, the before-and-after yield gap, the per-year binding
  counts, and the regression proofs in `check_yield.py`.
- README, `Modelfile.toml`, repo `CLAUDE.md`, and the documentation corrections
  already made in the working tree while writing the brief (see A-6).

### Out of scope

As the brief lists: year-varying limits, projected aquifer decline, surface
water, deficit-irrigation strategy (the controller uses no rationing rule; see
D-4), changes to the trigger, application depth or efficiency beyond what the
ceiling forces, rainfed regions, node 1, node 3, the other README follow-ups,
and any change to a required key set. Also out of scope, per D-1: building a
county-to-district table or reading Kansas water-right quantities.

## Assumptions and decisions

Three blocking questions were put to John while planning. All three were
answered, and the answers bind `run`. The research behind each is recorded here
because `run` starts from this file, not from the conversation.

### D-1. The allocation cap follows the rules at node 1's stratum point

**Answer: the rules at the point.** The cap comes from the water-management
district that contains node 1's stratum coordinate, which is also where this
bundle already takes the stratum's weather and soil.

What that yields, found at planning time and to be re-verified in `run` against
the current published rules:

| Region | Point (node 1 `regions.csv`) | County (US Census geocoder) | Governing district | Standing allocation |
|---|---|---|---|---|
| `ne_irrigated` | 41.1699 N, -98.6459 W | Howard County, NE | split between **Central Platte NRD** and **Lower Loup NRD**; `run` resolves which side the point falls on | Neither sets a standing per-acre allocation. Lower Loup regulates by certified acres and limits new ones. Central Platte's rules (updated 2023-11 and 2024-09) add metering and allocation only as a response to groundwater-decline triggers in a subarea. |
| `ks_irrigated` | 38.1752 N, -99.7654 W | Hodgeman County, KS | **no GMD**: Hodgeman was removed from GMD 3's boundary on 1988-12-20 | No LEMA applies. A Kansas water right carries an authorized annual quantity, but that is a per-right entitlement, not a district allocation, and is out of scope per this decision. |

Both caps are therefore declared as a sourced **none**, so only the capacity
ceiling binds on delivered runs. The cap logic is still built, and
`check_yield.py` exercises it with a synthetic cap (D-5), so AC-5 is proven for
both limits rather than for one.

The README must say why none of the caps that actually bind apply here. Those
caps are Upper Republican NRD's 62.5 in over five years, Lower Republican NRD's
45 in over five years with a 13 in hard cap in a Compact-call year, North Platte
NRD's, Sheridan 6 LEMA's 55 in over 2023-2027, and the GMD 4 district-wide LEMA's
township-varying allocation. They sit far west of both stratum points, and a
single production-weighted point cannot represent a stratum that spans several
regimes. That limitation belongs beside the existing "one representative point
per region" caveat.

**Rejected:** the rule covering the largest share of irrigated acres (needs a new
county-to-district table and split-county handling, and may still return "none",
since Kansas's largest irrigated area, GMD 3, has no LEMA), and Kansas water-right
quantities (generous and rarely binding, so mostly added complexity).

**Consequence for the brief's Outcome, stated plainly:** the brief expected two
binding limits. On the evidence, one binds. This is a finding, not a scope cut:
the brief itself allows a sourced "none".

### D-2. The capacity ceiling is the extension minimum design capacity

**Answer: extension design minimum.** Nebraska uses UNL NebGuide **G1851**,
*Minimum Center Pivot Design Capacities in Nebraska* (Kranz, Martin, Irmak,
van Donk and Yonts, issued May 2008). Its Table I gives the **net** system
capacity, in gpm/ac, that meets crop water needs in nine of ten years, by soil
available-water class and region. For loam and silt loam (2.5 in/ft) the planning
research found 3.85 gpm/ac in one region and 4.62 in the other. `ne_irrigated`
inherits Nebraska's loam in `soils.csv` (brief 0002, D-4). `run` must read the
figure and Howard County's region directly from G1851's table and map; the
planning-time reading of which region is which is not reliable enough to commit.

Kansas needed a K-State equivalent, and the plan said to stop rather than borrow
G1851 if none existed. `run` found none (R-1) and stopped.

**Revision, 2026-09-26: Kansas uses G1851 Region 2 as a labelled analogue**
(John: "follow your recommendation", R-1 option 1). `ks_irrigated`'s silt loam,
at 2.5 in/ft, gives **4.62 net gpm/ac**. The reasoning, which the row's `method`
text and the README must carry:

- Hodgeman County, at about 22 in of annual rainfall, is climatically closest
  to G1851's western Region 2, which is 20-24 in at its eastern edge.
- Kansas's peak ET is higher than western Nebraska's, so the borrowed figure
  understates Kansas need. It stays a minimum, biased toward stress, in the same
  direction as Nebraska's.
- Both strata then rest on one standard (von Bernuth et al. 1984, via G1851),
  so the two ceilings differ only by region and soil, not by method.
- It is borrowed, not a Kansas source, and says so wherever the value appears.
  A K-State design-minimum publication, if one appears, replaces it.

Conversion, committed with both units and the factor at each step (the brief's
AC-1, and `CLAUDE.md`'s unit rule):

```
1 acre-inch = 43,560 ft2 x (1/12) ft x 7.48052 gal/ft3 = 27,154 US gal
1 gpm/ac pumped 24 h = 1,440 gal/ac/day = 1,440 / 27,154 in/day
                     = 0.05303 in/day = 0.13470 cm/day
(G1851's own factor 453 is 27,154 / 60, the same number in gpm per acre-inch/hour)
```

The ceiling in `water_regime.csv` is stored **gross**, in cm/day, as the
controller sends it to PCSE's `amount`. PCSE then applies `amount x efficiency`,
so a net design capacity `N` gpm/ac becomes
`capacity_cm_day_gross = N x 0.13470 / efficiency`. `run` confirms from G1851's
text whether Table I's net figure assumes 24 h or 22 h pumping, and converts
accordingly, stating the basis in the row's `method`.

A design **minimum** is conservative: many real wells exceed it, so the modelled
ceiling tends to overstate stress. The README says so. It is also what makes
the ceiling meaningful: by construction it should bind in roughly one year in
ten, and AC-7 reports whether it does.

Aquifer decline enters only as the brief allows, as far as it already shows in
present-day capacity. A 2008 design standard does not embody decline at all.
The README states that too.

**Rejected:** registered well capacity (rates are set when a well is drilled and
do not show later decline; needs a new data build), and a single published
requirement figure (a requirement, not a capacity, so it would rarely bind).

### D-3. The limits are enforced by an engine subclass with a level trigger

**Answer: yes, as recommended.** Verified against the pinned PCSE 6.0.13
(`pcse/engine.py`):

- `Engine._run` does, in order, `integrate`, then `self.agromanager(day, drv)`,
  which is where a `StateEvent` fires, then `self.calc_rates(day, drv)`.
  `__init__` calls the same pair once at the start date.
- `WaterbalanceFD._on_IRRIGATE(amount, efficiency)` **sets**
  `self._RIRR = amount * efficiency`, and `calc_rates` consumes it and zeroes it
  (`classic_waterbalance.py:394-395,632-633`). So there is at most one
  application per day, and a second signal the same day would overwrite the
  first, not add to it.
- `StateEventsDispatcher` with `zero_condition: falling` fires when the previous
  sign of `SM - trigger` was +1 and the current sign is -1 or 0; the first call
  only records the sign (`agromanager.py`, `_zero_condition_falling`).

The mechanism is a small subclass of `Wofost72_WLP_FD`, defined in `runner.py`,
that overrides `calc_rates(day, drv)`. It first calls an irrigation controller,
then `super().calc_rates(day, drv)`. Because `calc_rates` runs immediately after
the agromanager in both `_run` and `__init__`, the controller reads exactly the
`SM` the `StateEvent` read and irrigates in the same rate step. That is what
makes AC-4 reproducible. The controller sends the signal with
`self._send_signal(signal=signals.irrigate, amount=..., efficiency=...)`, the
same dispatcher path the agromanager uses, with the kiosk as sender.

The rule, once per day while the crop is in the ground:

```
if SM <= trigger_sm and season_applied_gross < cap:
    amount = min(irrigation_amount_cm, capacity_cm_day_gross, cap - season_applied_gross)
    send irrigate(amount, efficiency); season_applied_gross += amount
```

`trigger_sm` is rounded to four places exactly as 0002 does
(`round(trigger_sm, 4)`). `<=` matches the dispatcher's firing on sign 0. The
controller evaluates nothing before its first observation, so a profile that
starts below the trigger at sowing behaves like 0002, which does not fire on its
first call. `run` confirms this case never arises in the sample or the baseline.

Consequences:

- **The irrigated regions drop `StateEvents` and the trailing empty campaign.**
  Their agromanagement becomes the same single campaign rainfed regions use, so
  the run stops at maturity and 0002's traps C-2 and C-3 no longer apply to them.
  The `DVS is not None` filter in `run_wofost` stays, since it is harmless and
  still correct. 0002's C-1 (`amount`, not `irrigation_amount`) and the cm unit
  still apply to the signal the controller sends.
- **Rainfed regions are constructed exactly as today**: plain `Wofost72_WLP_FD`,
  no controller. This is what protects AC-3.
- **The level trigger is a deliberate semantic change** from 0002's crossing
  trigger. Under a low-capacity ceiling a crossing trigger would fire once and,
  if one day's water failed to lift `SM` back above the trigger, never fire
  again that season. With the ceiling non-binding, the two agree exactly when
  0002's profile always rose back above the trigger the day after an
  application. **`run` measures that first (step 3)** by counting, across the
  projection and all 60 irrigated baseline seasons, days with `SM <= trigger` on
  the day after a 0002 application. If the count is zero, AC-4 must hold
  exactly. If it is not zero, `run` stops, records the count and the affected
  seasons here, and asks for a stated tolerance before sourcing any value, as
  the brief's AC-4 requires.
- **`irrigation_amount_cm` (2.54) becomes the most applied on any one day.**
  With a ceiling below it, the ceiling governs. This matches how a pivot
  actually delivers an inch: over several days, not in one.
- **Shared, not duplicated.** `build_baselines.py` currently constructs
  `Wofost72_WLP_FD` itself (`build_baselines.py:181`). It must obtain the model
  from the same `runner.py` factory, so the baseline and the projection cannot
  drift, which extends 0002's A-2.

**Rejected:** keeping crossing semantics and spreading an application over days
(closer to 0002 by construction, but able to stop irrigating for the rest of a
season under low capacity).

### D-4. No rationing rule

The controller is first-come: it irrigates whenever the trigger is met until
the cap, if any, is exhausted. Under D-1 no cap binds on delivered runs, so the
choice only affects the synthetic-cap test and future caps. It is stated in the
README as the simplest rule, not as how farmers ration.

### D-5. What "bound" means, so AC-7 cannot be satisfied trivially

**Revised 2026-09-27 (R-2): the definition below failed its own purpose and was
replaced by a counterfactual.** A season is limit-bound when the limits cost it
more than 1% (`runner.LIMIT_BINDING_LOSS_PCT`) of the yield the same season makes
with the limits removed; only a limit actually reached (a day at the ceiling, a
cap reached) is named among the binding ones. Each irrigated region is run a
second time with the limits removed, in the projection and in every baseline
year. John chose this on 2026-09-27 ("Counterfactual yield loss"). The original
text is kept below for the record.


With a ceiling around 0.6 cm/day gross against a 2.54 cm application depth,
the ceiling limits the amount on every irrigation day, so "the ceiling was
applied" would be true every year and would measure nothing. The definitions:

- **Capacity-limited day:** the controller irrigated at the ceiling **and** the
  crop was water-stressed that day (`RFTRA < 1.0`). The well was running flat
  out and the crop still went short. A year in which capacity bound has at
  least one such day.
- **Cap-limited day:** a day with `SM <= trigger_sm` on which the cap left less
  than the day's demanded amount. A year in which the cap bound has at least
  one such day.

The metadata reports, per irrigated region: the limits in force; the season's
gross applied irrigation; the count and first date of capacity-limited days;
and the first cap-limited date or `null`.

`check_yield.py` reports the per-year binding counts for the baseline.
`build_baselines.py` records them in `baselines.meta.json` so they are committed
evidence rather than a transient print.

**Synthetic-cap test:** a cap small enough to bind is injected in-process for one
irrigated region. The check asserts the applied total never exceeds it and that a
cap-limited date is reported. This proves the cap code path without a committed
value that no source supports.

### Other material assumptions

- **A-1. Present-day limits constant across 1995-2024** is decided by the brief
  and not reopened.
- **A-2. The rainfed-strata regression fixture is captured before any code
  change.** 0002's `unsplit_regression.json` covers the eight unsplit states
  only. `run` captures pre-change figures for all twelve regions from the base
  commit, which covers `ne_rainfed` and `ks_rainfed` for AC-3 and the irrigated
  strata for AC-4. It commits them as `regime_regression.json`, and
  `unsplit_regression.json` stays in place. The AC-4 baseline comparison is
  against the committed `baseline_yields.csv` rows for the two irrigated strata
  (60 values).
- **A-3. Only the irrigated strata's baselines change.** `build_baselines.py`
  rebuilds all twelve regions from `.climatology-cache/`. The ten rainfed
  regions' 300 values must reproduce exactly, or it is a build error.
  `climatology.csv` does not depend on regime, so it should not change at all;
  `run` rebuilds it only to prove that, and discards it if identical.
- **A-4. Local caches.** `.climatology-cache/` and `.crop-parameters-cache/`
  (the pinned `f0a6491f` checkout) exist, gitignored, in the main checkout at
  `/Users/john/repos/wofost-bundles/corn-yield/`. A fresh branch or worktree
  must point at them or rebuild them. Rebuilding the climatology cache hits
  Open-Meteo's hourly limit (0002's C-5), so point at them.
- **A-5. Environment.** A scratch venv with the Dockerfile's exact pins
  (`pcse==6.0.13`, `numpy`, `pandas` as pinned), as 0002 used. The repository
  has no other test runner.
- **A-6. Documentation corrections already in the working tree.** While writing
  the brief, `CLAUDE.md` (node 3 task marked done, the Model Home task restated,
  brief 0003 added, the "sibling repo still to come" task closed) and
  `corn-yield/README.md` (the "breaking for node 3" banner rewritten, the
  follow-up linked to this brief) were edited but not committed. John asked for
  them to ship in this brief's PR. `run` commits them with the brief and this
  plan, not as unrelated work. A matching stale task in
  `ag-commodity-bundles/CLAUDE.md` (task 3) belongs to that repo and is not
  touched here.
- **A-7. Nothing here is a breaking change for node 3.** No key or required set
  changes. The PR says node 2 must be re-registered and that stored runs'
  irrigated-strata figures are superseded.

### Acceptance-criteria numbering

The brief's AC-1 to AC-15 are sequential and unambiguous and are kept as they
are. Where a decision narrows how an AC is met (AC-1 and AC-7 by D-1 and D-5),
the traceability row says so.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | Sourced cap (depth or "none") and ceiling (cm/day) per irrigated region; every conversion stated | `water_regime.csv` gains `allocation_cap_cm` (empty = sourced none), `capacity_net_gpm_ac`, `capacity_cm_day_gross` and `limits_method`/`limits_source` with the conversion chain and citations | `check_limits_table`: columns present, empty for rainfed, gross ceiling re-derived from net gpm/ac and efficiency (NE 3.85 -> 0.6101, KS 4.62 -> 0.7321), empty caps declared as a sourced none naming the NRD/GMD | **pass** |
| AC-2 | No regime or limit inferred from key spelling | limits read only through `irrigation_parameters(soil, regime_row, key)` | source scan in `check_regime_table` over `runner.py` and `build_baselines.py`, now covering the controller code | **pass** |
| AC-3 | Ten rainfed regions identical to today | rainfed regions get plain `Wofost72_WLP_FD` from `model_for`, no controller | `check_rainfed_regions_unchanged` (all ten against `regime_regression.json`) plus the kept `check_unsplit_regions_unchanged`; 300 rainfed baseline values unchanged on rebuild | **pass** |
| AC-4 | Non-binding limits reproduce 0002 | D-3 controller; `unlimited()` | step-3 gate (0 level-only days); `check_limits_off_reproduces_0002`: both irrigated projection rows identical in every field with limits off and 0002's distribution restored, each delivered run's unlimited counterfactual equals 0002's yield, and all 60 baseline years' unlimited yields equal 0002's committed values | **pass** |
| AC-5 | Applied never exceeds cap; no day exceeds ceiling | controller clamps with `min(...)`; `max_daily_gross_cm` reported | `check_limits_respected`: projection and 30 baseline seasons per stratum within the ceiling; synthetic 10 cm cap never exceeded, reached date reported, cap named as binding | **pass** |
| AC-6 | Irrigated baselines rebuilt under limits, 1995-2024; rainfed rows exact; limits recorded and enforced | `build_baselines.py` via `model_for`; `baselines.meta.json` records `irrigation` and `irrigation_binding` per region; `check_baseline_regimes` compares them before any projection | rebuild: 300 rainfed values identical, climatology byte-identical; `check_baseline_regime_matches`; `check_baseline_regime_mismatch_fails` with two new scenarios (different ceiling; pre-0003 baseline with no irrigation record) | **pass** |
| AC-7 | Per-region count of baseline years each limit bound | counterfactual D-5 (R-2); `binding_summary` in `baselines.meta.json` | `check_limit_binding_years`: NE 22/30 capacity-bound (>5% in 10), KS 30/30 (>5% in 19), cap 0/30 both, as D-1 predicts | **pass** |
| AC-8 | Gap positive and within 25-200%; before and after printed | none beyond the limits | `check_irrigation_gap`: KS +116.5% (0002 +133.2%, NASS +105%), NE +52.0% (0002 +57.4%, NASS +55%) | **pass** |
| AC-9 | Hot, dry fortnight costs more yield with limits than without; metadata shows which bound | `perturb_silking` reused; counterfactual in metadata | `check_capacity_stress`: KS 1,132 vs 684 kg/ha lost, NE 1,296 vs 1,020; limits' cost 7.17% -> 15.51% (KS), 3.43% -> 7.16% (NE); pumping capacity named as binding | **pass** |
| AC-10 | Metadata reports limits, applied water and binding; no required set changes; input still binds to `crop_weather_daily` | `water_regime_metadata`: `irrigation`, `season` (applied, days, max daily, days at ceiling, cap reached, unlimited yield, loss, verdict, limits named), `baseline_vintage` with limits | `check_schema_honesty` extended (season keys present for irrigated, null for rainfed, snapshot columns unchanged); `check_schema_compatibility`: binds `crop_weather_daily`, refuses `crop_weather_summary` | **pass** |
| AC-11 | Unknown `region_key` fails loudly in every table | limits in `water_regime.csv`, already covered; `_positive_float` validation | `check_unknown_region_fails`, `check_every_table_fails_loudly` unchanged and passing; `check_malformed_limit_fails`: negative, empty and non-numeric limits each raise naming the table, region and column | **pass** |
| AC-12 | Docker build; `--network none` identical apart from `generated_at` | `Dockerfile` unchanged (no new shipped file) | built from `corn-yield/`; offline run: rows, columns and trajectory identical, metadata differs only in `generated_at`, pin verified at `f0a6491f2368` | **pass** |
| AC-13 | `validity_domain` and `not_for` describe the limits; validator clean within caps | `Modelfile.toml` `validity_domain` (514/600), `not_for`, `provenance` (392/400), resources comment | `orchestration.modelfile validate` -> `OK`, no annotation warnings | **pass** |
| AC-14 | README and `CLAUDE.md` current, including node 3's re-key | README: table, controller, traps, new Supply limits section, limitations, future work, validation; `CLAUDE.md`: file list, design notes, verified results, task lists (with A-6 edits) | review of the full diff | **pass** |
| AC-15 | `check_yield.py` passes in full, 0002's 379 checks included, updated not deleted | the two `StateEvent` assertions rewritten (campaign has no events; engine and controller per regime); `check_baseline_regimes` callers updated | **485/485 pass** (baseline 379/379); no check removed | **pass** |

## Verification

Run from `corn-yield/` in the scratch venv (A-5), with the caches from A-4.

| Command | Purpose | Baseline result | Final result |
|---|---|---|---|
| `python runner.py sample_input.json > run/corn_yield_snapshot.output.json` | model runs end to end, stdout is JSON only | pass: 12 regions, all mature, 0.9 s | pass: 12 regions, all mature, 0.9 s; 2 extra counterfactual runs |
| `python check_yield.py run/corn_yield_snapshot.output.json` | the bundle's committed validation | **379/379 pass** (pinned crop parameters linked, so the verifiable pin branch runs) | **485/485 pass** |
| step-3 gate script (scratch, not committed) | D-3's reproducibility precondition | **pass**: 0 level-only days in 60 baseline seasons (762 applications) and the projection (29); 0 seasons start below the trigger; stepped runs reproduce all 60 committed baseline values | n/a |
| `python build_baselines.py <agromet-bundles>/crop-weather/regions.csv <crop-parameters checkout>` | AC-6 rebuild; rainfed values exact | n/a | pass: 360 rows/12 keys; 300 rainfed values identical; irrigated medians NE 9,339 -> 8,966, KS 6,164 -> 5,676 |
| `python build_climatology.py <regions.csv>` | A-3: proves climatology is unchanged | n/a | pass: `climatology.csv` byte-identical; meta differed only in `built_at` and was restored |
| `docker build -t corn-yield . && docker run --rm --network none corn-yield` | AC-12 | not run before the change (Dockerfile unchanged; 0002 verified it) | pass: offline output identical to local apart from `generated_at` |
| `python -m orchestration.modelfile validate corn-yield/Modelfile.toml` (from the Model Home repo) | AC-13 | not run before the change | `OK`, no annotation warnings |

## Conflicts found while running

Recorded as they were hit. `run` stopped at the first one, as the plan requires.

### R-1. No K-State design-minimum capacity exists to source Kansas's ceiling (resolved: option 1)

Step 2 looked for the Kansas counterpart D-2 requires and found none:

- **K-State MF2870**, *Kansas Center Pivot Survey* (Rogers, Alam and Shaw,
  April 2009), is a roadside survey of nozzle packages in 12 counties. It
  reports no capacities.
- **K-State MF3066**, *Efficient Crop Water Use in Kansas*, gives the demand
  side: corn normally needs 5 to 7.5 gpm/ac, and a limited-irrigation field study
  ran at 3.1 gpm/ac. That is a requirement, which D-2 rejected as a basis.
- **Lamm, Stone and O'Brien (2007)**, *Crop Production and Economics in
  Northwest Kansas as Related to Irrigation Capacity*, Applied Engineering in
  Agriculture 23(6), is research relating yield to capacities from 2.5 to 8.5
  mm/day at Colby. It is not a design standard.

D-2 says to stop rather than borrow G1851's Nebraska table for Kansas, so no
Kansas value was written. Options for the revised plan:

1. **G1851 Region 2 as a labelled analogue.** `ks_irrigated` (Hodgeman County,
   about 22 in annual rainfall) is climatically closest to G1851's western
   Region 2 (20-24 in at its eastern edge). Silt loam at 2.5 in/ft gives 4.62
   net gpm/ac. Kansas's peak ET is higher than Nebraska's, so this understates
   need and stays a minimum. It is the same standard for both strata, and it is
   stated as borrowed.
2. **A capacity taken from Lamm et al. (2007)**: a Kansas source, but a research
   range rather than a design figure, so the chosen point within it needs its
   own justification.
3. **Apply G1851's criterion to Kansas weather**: the net capacity meeting
   demand in nine of ten years, computed from `ks_irrigated`'s own 1995-2024
   series. It is Kansas-specific but derived by this repo, not published, and
   partly circular with the baseline it then constrains.
4. **No ceiling for Kansas**: Nebraska constrained, Kansas unconstrained as in
   0002, with the asymmetry stated. This leaves AC-9 unmet for Kansas.

**Resolution (2026-09-26):** John chose option 1. D-2 is revised accordingly,
and `run` resumes at the rest of step 2 (the D-1 re-verification).

### R-2. D-5's "bound" definition was trivially true (resolved: counterfactual)

With the limits in place, D-5 as planned counted capacity as binding in 29/30
Nebraska and 30/30 Kansas baseline years. Measured with the limits **removed**,
27/30 (NE) and 30/30 (KS) years still had irrigation days on which WOFOST's crop
was stressed (`RFTRA < 1`): maize is already short of water at the 50% depletion
trigger. So "at the ceiling on a stressed day" measured the trigger, not the
ceiling, which is exactly the trivially-true AC-7 that D-5 was written to
prevent. `run` stopped and asked. John chose the counterfactual on 2026-09-27;
D-5 now records it. It adds one limits-off WOFOST run per irrigated region at
run time, which changes the "one simulation per region" design note; the
README, `CLAUDE.md` and Modelfile resources comment say so.

### Step 2 findings on resumption (D-1, both points)

- **D-1 re-verified at both points with official GIS layers** (2026-09-26):
  Nebraska's `NaturalResourcesDistrictBoundaries` layer puts `ne_irrigated` in
  the **Lower Loup NRD**, which is one side of the Howard County split. Kansas
  Geoportal's GMD layer returns no district for `ks_irrigated`; a control point
  at Garden City returns GMD 3, so the empty result is real.
- **Lower Loup sets no per-acre pumping allocation**: its 2025 Lower Platte
  River Basin Coalition report (dated 2026-03-01) uses "groundwater acres
  allocations" for new irrigated acres, and its February 2024 Groundwater
  Quantity Area requires meters in Buffalo and Platte counties, not Howard.
- **No Kansas allocation applies at the point.** It lies outside every GMD, so
  no LEMA applies. The Pawnee Valley IGUCA controls new appropriations (750
  acre-feet per 2-mile circle since 1985), not seasonal pumping on existing
  rights, per Big Bend GMD 5's page. KDA's own IGUCA pages refuse automated
  requests, so they were not read directly.

### Deviations from the plan, all recorded as built

- **More `water_regime.csv` columns than D-2 named**: `capacity_net_gpm_ac`
  (so the gross ceiling can be re-derived and checked) and
  `limits_method`/`limits_source` (per-limit provenance beside the existing
  regime `method`/`source`).
- **The controller summary reports plain facts** (`applied_gross_cm`,
  `irrigation_days`, `max_daily_gross_cm`, `days_at_capacity_ceiling`,
  `first_cap_reached_date`); the binding verdict comes from `limits_effect()`.
  `max_daily_gross_cm` was added so AC-5 is proven from committed evidence for
  all baseline seasons.
- **Check names differ from the plan**: `check_rainfed_regions_unchanged` covers
  all ten rainfed regions, not only the two strata. The limits-mismatch refusal
  is two new scenarios inside `check_baseline_regime_mismatch_fails` rather than
  a separate function. `check_limits_table` and `check_malformed_limit_fails`
  were added.
- **The engine subclass declares `irrigation` as a PCSE trait**: PCSE engines
  refuse undeclared attributes, which the plan did not anticipate.

### Step 2 findings from the first stop (R-1)

- **G1851 re-verified from the PDF** (issued May 2008; Kranz, Martin, Irmak,
  van Donk, Yonts; Table I from von Bernuth et al. 1984, Trans. ASAE 27(2)).
  Loam, silt loam and very fine sandy loam over a silt loam subsoil, at 2.5
  in/ft: **3.85 net gpm/ac in Region 1**, 4.62 in Region 2.
- **Howard County is Region 1.** Read from Figure 1: the dividing line crosses
  41.17 N near -99.7 W, about 1.1 degrees west of the point. This is consistent
  with G1851's text, which puts Rock County (about -99.4 W) on the line.
- **Pumping-hours basis resolved.** Table I is a net rate of continuous
  operation: G1851's worked example multiplies it by 168 / (168 - downtime
  hours) to size a larger pump for a pivot that runs less. The daily ceiling is
  therefore Table I's value as a 24-hour average, with no hours correction:
  3.85 x 0.13470 = 0.5186 cm/day net = 0.6101 cm/day gross at 0.85 efficiency.
- **D-1 was not re-verified before the first stop**; it was on resumption, as
  recorded above.

### Step 3 gate: passed

Measured by stepping the pinned 6.0.13 engine day by day on the unchanged 0002
code (scratch script, not committed):

| | Trigger SM | Seasons | Applications | Level-only days | Crossing-only days | Seasons starting below trigger |
|---|---|---|---|---|---|---|
| `ne_irrigated` baseline | 0.22 | 30 | 311 | 0 | 0 | 0 |
| `ks_irrigated` baseline | 0.24 | 30 | 451 | 0 | 0 | 0 |
| `ne_irrigated` projection | 0.22 | 1 | 10 | 0 | 0 | no |
| `ks_irrigated` projection | 0.24 | 1 | 19 | 0 | 0 | no |

0002's profile rose back above the trigger the day after every application, so
under D-3 the level trigger and the crossing trigger agree on every day, and
AC-4 must hold exactly. The stepped runs reproduced all 60 committed baseline
values, so the measurement itself is faithful.

### Done before the first stop (R-1)

- Branch `feat/0003-irrigation-supply-limits` from `24d658b`.
- Baseline: model 12 regions in 0.9 s; `check_yield.py` **379/379**.
- `corn-yield/regime_regression.json` captured at the base commit (all twelve
  regions' rows, plus the 60 pre-change irrigated baseline values). This is the
  AC-3 and AC-4 fixture, and it cannot be reconstructed after the controller
  lands.
- No product code changed.

## Implementation steps

1. **Baseline and fixture.** On the base commit, run the model and
   `check_yield.py` and record the counts. Capture `regime_regression.json` with
   the pre-change figures for all twelve regions (A-2). This cannot be
   reconstructed after the controller lands.
2. **Re-verify the sources.** Read the current Central Platte and Lower Loup
   rules, resolve which side of Howard County the NE point falls on, and confirm
   Hodgeman County's status (no GMD, no LEMA). Read G1851's Table I, its
   region map and its pumping-hours basis. Find the K-State design-minimum
   source, and **stop if there is none** (D-2). Record exact citations,
   publication dates and values in this plan before writing any table row.
3. **The D-3 gate.** Instrument 0002's behaviour in scratch and count the days
   with `SM <= trigger` on the day after an application, across the projection
   and all 60 irrigated baseline seasons. Zero: proceed. Otherwise: stop and
   record the count (see D-3).
4. **Controller.** Add the `Wofost72_WLP_FD` subclass and a factory
   (`model_for(parameters, provider, agromanagement, soil, regime_row,
   region_key)`) to `runner.py`. Simplify `agromanagement_for` so irrigated
   regions get the single campaign. Validate the limit columns loudly (a
   negative or non-numeric value names the row). Keep the controller's record
   (gross applied, capacity-limited days, cap-limited date) on the model
   instance for the runner to read.
5. **Limits off first.** With both limits empty or non-binding, run the model and
   rebuild the two irrigated baselines in scratch. Assert AC-4 exactly before
   adding any value.
6. **Write the limits.** Add the two columns to `water_regime.csv` with D-1 and
   D-2's sourced values, conversion chain and citations. Rainfed rows stay empty
   and unchanged.
7. **Baselines.** Point `build_baselines.py` at the factory. Record per-region
   limits and D-5 binding counts in `baselines.meta.json`. Rebuild all twelve
   regions; assert the 300 rainfed values are unchanged and that climatology is
   unchanged (A-3).
8. **Runner guard.** Extend `check_baseline_regimes` (or add a sibling called at
   the same point, before any projection runs) to refuse a baseline whose
   recorded limits differ from `water_regime.csv`'s. A baseline recording no
   limits is a mismatch, as 0002 treated a missing regime.
9. **Metadata.** Extend the `water_regime` block in `build_metadata` per D-5.
   Additive only; no required keys.
10. **Checks.** Rewrite the two `StateEvent` assertions and add
    `check_rainfed_strata_unchanged`, `check_limits_off_reproduces_0002`,
    `check_limits_respected`, `check_baseline_limits_mismatch_fails`,
    `check_limit_binding_years` and `check_capacity_stress`. Extend
    `check_regime_table`, `check_irrigation_gap` and `check_schema_honesty`.
    Delete nothing.
11. **Annotations and docs.** `Modelfile.toml` `validity_domain` and `not_for`
    (validity is at 591/600 now, so rewrite rather than append); README limits
    section (D-1's "why no cap binds here", D-2's conservative bias and its
    silence on decline, D-4, A-1, before/after figures, the updated upper-bound
    caveat); `CLAUDE.md` design notes (engine subclass instead of a `StateEvent`,
    the level trigger), verified results and task lists. Include the A-6 edits.
12. **Docker and validator.** Build, run offline, diff; validate the Modelfile.
13. **Pull request.** State that nothing breaks for node 3, that node 2 must be
    re-registered, the D-1 finding (one limit binds, not two), and the
    before-and-after gaps.

## Files likely to change

```
corn-yield/runner.py               engine subclass, factory, controller, guard, metadata
corn-yield/build_baselines.py      use the runner factory; record limits and binding counts
corn-yield/water_regime.csv        allocation_cap_cm, capacity_cm_day_gross (+ method/source)
corn-yield/baseline_yields.csv     irrigated strata rebuilt (60 values); rainfed unchanged
corn-yield/baselines.meta.json     limits and binding counts per region
corn-yield/regime_regression.json  new: pre-change figures, all twelve regions
corn-yield/check_yield.py          two assertions rewritten, six checks added, three extended
corn-yield/Modelfile.toml          validity_domain, not_for
corn-yield/README.md               limits section; A-6 edits already present
CLAUDE.md                          design notes, verified results; A-6 edits already present
docs/features/0003-irrigation-supply-limits.md   the brief (uncommitted today)
docs/plans/0003-irrigation-supply-limits.md      this plan
```

`climatology.csv`, `Dockerfile` and `sample_input.json` should not change;
`run` reports it if they do.

## Risks and follow-ups

- **Kansas's ceiling is borrowed** from G1851 Region 2 (R-1, option 1). It
  understates Kansas's peak demand, so Kansas's modelled stress is, if anything,
  overstated. A K-State design-minimum source would replace it.
- **The D-3 gate fails.** If 0002 ever left the profile below the trigger after
  an application, AC-4 cannot hold exactly under the level trigger. The plan
  then needs a stated tolerance, per the brief.
- **The gap may leave the 25-200% band.** A binding ceiling cuts irrigated yield.
  Nebraska's 0002 gap (+57.4%) has the least room. Per the brief that is a
  finding for the PR, not a number to tune: `run` does not adjust the ceiling to
  stay inside.
- **A design minimum is biased toward stress**, and a 2008 standard does not
  capture decline since. Both are stated, not corrected.
- **The cap never binds on delivered runs (D-1).** The machinery is tested
  synthetically. A follow-up could represent a stratum by several points or by
  district shares, which is where real caps would enter; that belongs with the
  crop-reporting-district granularity follow-up.
- **Private-API reliance.** `_send_signal` and overriding `calc_rates` are PCSE
  internals. Both are verified on the pinned 6.0.13 and the pin is exact, but a
  PCSE upgrade must re-verify them. The README says so, beside the other three
  PCSE traps.
- **Model Home import** remains unverified from a session; it needs a signed-in
  human.

Sources consulted while planning (re-verify in step 2):
[URNRD](https://www.urnrd.org/deep-dive-nebraska%E2%80%99s-water-resources),
[Nebraska NRD regulations summary](https://dwee.nebraska.gov/sites/default/files/Waterinitiatives/WaterQualityQuantityTaskForce/ResourcesAndBackgroundMaterials/NaturalResourcesDistricts/2025_gwamquantity_quality_regulations.pdf),
[Central Platte NRD rules 2024-09-26](https://www.cpnrd.org/wp-content/uploads/RULES-REGS_09_26_2024.pdf),
[Lower Loup NRD management](https://www.llnrd.org/programs/management),
[Sheridan 6 LEMA](https://www.agriculture.ks.gov/divisions-programs/division-of-water-resources/managing-kansas-water-resources/local-enhanced-management-areas/sheridan-6-lema),
[GMD4 LEMA](https://www.agriculture.ks.gov/divisions-programs/division-of-water-resources/managing-kansas-water-resources/local-enhanced-management-areas/gmd4-lema),
[GMD 3 (Hodgeman boundary change)](https://www.gmd3.org/pdf/2014/2014GMD3reportfinal.pdf),
[UNL G1851](https://extensionpubs.unl.edu/publication/g1851/na/html/view),
[K-State MF3066](https://bookstore.ksre.ksu.edu/pubs/efficient-crop-water-use-in-kansas_MF3066.pdf),
[K-State limited irrigation](https://www.sunflower.k-state.edu/agronomy/irrigation/limited_irrigation.html),
US Census geocoder for the two points' counties.
