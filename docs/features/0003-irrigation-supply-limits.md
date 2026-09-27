# Irrigation supply limits

## Outcome

`ne_irrigated` and `ks_irrigated` are simulated with the two limits that
actually bind on High Plains irrigation, a **seasonal allocation cap** and a
**pumping-capacity ceiling**, instead of an unlimited water supply. Their drought
signal stops being an upper bound on protection and becomes an estimate under
declared, sourced limits.

Today, brief 0002's irrigated strata irrigate whenever the soil profile dries to
50 percent depletion, 2.54 cm gross per application, with no limit on how often
that happens or on how much water a season uses. The README says so plainly:
supply is unconstrained, so the simulated drought protection is "an upper
bound, not a forecast of what a well can deliver in a bad year". That is
the wrong way round for a node whose whole job is a weather-driven signal. The
seasons where supply limits bind (a long hot dry spell at silking, when demand
peaks and a well cannot keep up) are exactly the seasons the market cares
about, and they are exactly where node 2 currently reports the least stress.

When this is done, a hot dry season costs the irrigated strata yield in
proportion to what their sourced limits allow, and the output says which limit
bound, when, and how much water was applied.

## Scope

### In scope

**A seasonal allocation cap.** A total depth of irrigation water a region may
apply in one season, declared per region. Once the season's applied total
reaches the cap, no further irrigation is applied that season. Sourced from the
water-management rules that govern the stratum's corn: Nebraska Natural
Resources District (NRD) rules and regulations for `ne_irrigated`, and Kansas
Division of Water Resources / Groundwater Management District (GMD) rules,
including any Local Enhanced Management Area (LEMA) order, for `ks_irrigated`.
Where the governing rules set no allocation, "no cap" is a legitimate, sourced
answer. It is declared as such and not replaced with an invented number.

**A pumping-capacity ceiling.** A maximum depth of water a region can apply per
day, declared per region, derived from a sourced representative well capacity
(for example gallons per minute per acre, converted to cm/day with the
conversion stated). This is the limit that bites in extreme heat. It turns
brief 0002's instantaneous 2.54 cm application into one the system can
physically deliver only over several days, while the crop keeps transpiring.

**Present-day limits held constant across the baseline.** Both limits take
their current values and are applied unchanged to every year of the 1995-2024
baseline and to the projection. The baseline therefore answers "what would
each of those thirty seasons have yielded under today's rules and today's
wells", which is the right comparison for a present-day anomaly. That the
historical rules and well capacities differed is a stated limitation, not
something this brief models.

**Aquifer decline only as it already shows in well capacity.** A present-day
well-capacity figure already embodies the decline that has happened. Projected
future decline, and any year-by-year history of it, are out of scope. The
README says so.

**Both limits in the declared regime table.** They extend brief 0002's
`water_regime.csv` (or a table beside it, the plan's call) with per-row method
and source text, and apply only to regions whose regime is `irrigated`. The ten
rainfed regions are untouched. As in brief 0002, nothing infers a regime or a
limit from the spelling of a region key.

**The same limits in the baseline and the projection.** `build_baselines.py`
rebuilds the two irrigated strata's thirty-year distributions under the new
limits, using the same agromanagement or irrigation logic `runner.py` uses, not
a second copy. The baseline metadata records the limits it was built under, and
the runner refuses to run when they differ from the ones it would apply, as it
already does for the regime and the crop-parameter pin.

**Auditable output.** Per irrigated region, the output metadata reports the
limits in force, the season's applied irrigation to date (cm, gross), and
whether and from when each limit bound. The addition is additive: no required
key set changes.

**Honest relabelling.** The README, the Modelfile `validity_domain` and
`not_for`, and the output's regime description stop calling the irrigated
strata an upper bound. They state instead which limits are now modelled, from
what source, and what remains out (projected aquifer decline, historical rule
changes, surface-water supply, farmer deficit-irrigation strategy, one
representative point per stratum).

**Keep this repo's `CLAUDE.md` and bundle README current**, including the
now-stale node 3 task: `ag-commodity-bundles` brief 0003 (PR #3, `7a07216`,
2026-09-21) re-keyed node 3 on the twelve regions, so the four-step flow is no
longer broken by design. What remains there is Model Home registration.

### Out of scope

- **Year-varying limits.** No history of allocations, LEMA orders or well
  capacities across 1995-2024.
- **Projected aquifer decline** or any groundwater model.
- **Surface-water irrigation**, canal deliveries, and conjunctive use.
- **Deficit-irrigation strategy.** How a farmer rations a cap across a season
  (for example saving water for silking) is behaviour, not a limit. The plan
  may choose a simple, stated rationing rule; it does not optimise one.
- **Changing the trigger, application depth or efficiency** that brief 0002
  sourced, except where the capacity ceiling forces an application to be spread
  over days.
- **Rainfed regions**, the region set, and node 1's strata.
- **Node 3.** Its keys do not change. Its percentile ranks for the two irrigated
  strata will move because node 2's numbers move, which is expected, not a
  break.
- The other README follow-ups: gNATSGO/SSURGO soils, the silking-heat overlay,
  crop-reporting-district granularity.
- Any change to the output document's required key sets.

## Acceptance criteria

- **AC-1** -- A committed table declares, for each irrigated region, a seasonal
  allocation cap (a depth, or an explicit sourced "none") and a pumping-capacity
  ceiling in cm/day, each with its own method and source text. Every unit
  conversion is stated with both units and the factor.
- **AC-2** -- Nothing in the runner or the build scripts infers a regime or a
  limit from the spelling of a region key, demonstrated by a committed check.
- **AC-3** -- The ten rainfed regions produce output identical to today's for
  the same input, demonstrated by a committed check. Brief 0002's regression
  fixture covers the eight unsplit states; this brief extends the proof to the
  two rainfed strata.
- **AC-4** -- With both limits set to non-binding values, the irrigated strata
  reproduce brief 0002's committed figures, so any change in the delivered
  numbers is attributable to the limits and not to a change of mechanism. If the
  mechanism the plan chooses cannot reproduce them exactly, the plan says why
  and states the tolerance before implementation starts.
- **AC-5** -- Under the delivered limits, the season's applied irrigation never
  exceeds the cap and no day's application exceeds the ceiling, for every
  baseline year and the projection, shown by a committed check.
- **AC-6** -- The baseline for the two irrigated strata is rebuilt under the
  limits over the unchanged 1995-2024 period by the existing build scripts. The
  ten rainfed regions' committed baseline and climatology rows reproduce
  exactly. The baseline metadata records the limits, and the runner fails loudly
  when they disagree with the limits it would apply.
- **AC-7** -- The committed check reports, per irrigated region, in how many of
  the thirty baseline years each limit bound. A limit that never binds is a
  finding to report and explain, not a failure to tune away.
- **AC-8** -- The irrigated-minus-rainfed yield gap stays positive in both states
  and inside brief 0002's documented 25-200 percent sanity band. The check prints
  the gap before and after the limits against NASS's operation-level +105 percent
  (KS) and +55 percent (NE). A result outside the band is a finding for the pull
  request, not a number to tune.
- **AC-9** -- A stress test (a hot, dry fortnight injected over each irrigated
  region's own projected flowering date, as in brief 0001's AC-6) costs the
  irrigated strata more yield with the limits than without them, and the
  metadata shows which limit bound.
- **AC-10** -- Per irrigated region, the output metadata reports the limits in
  force, the season's applied irrigation (cm, gross) and whether and from when
  each limit bound. No required key set changes, and `check_schema_compatibility`
  still binds the input to node 1's `crop_weather_daily`.
- **AC-11** -- An input `region_key` with no matching row in any table, including
  a new or extended limits table, still fails the run loudly and names the table.
- **AC-12** -- `docker build` from the bundle folder succeeds and `docker run
  --network none` reproduces identical output apart from `generated_at`.
- **AC-13** -- The Modelfile's `validity_domain` and `not_for` no longer describe
  irrigated supply as unconstrained. They state the modelled limits and what is
  left out, and the validator passes with no annotation warnings inside the
  600- and 400-character caps.
- **AC-14** -- The bundle README documents the limits, their sources and
  conversions, the rationing rule if any, the present-day-limits assumption, what
  remains out, and the before-and-after figures. The repo `CLAUDE.md` design
  notes, verified results and task lists are current, including node 3's
  completed re-key.
- **AC-15** -- `check_yield.py` passes in full, including brief 0002's 379
  checks, with any check that assumed unconstrained supply updated rather than
  deleted.

## Constraints and dependencies

- **Where the stratum point sits is not the whole story.** Node 1's stratum
  points are production-weighted centroids of many counties, not farms:
  `ne_irrigated` at 41.17 N, -98.65 W and `ks_irrigated` at 38.18 N, -99.77 W.
  The jurisdiction a centroid happens to fall in may not be representative of
  the stratum's production. The plan must choose, and justify, whether the
  limits come from the centroid's own NRD/GMD or from a production-weighted
  representative across the stratum. It must not pick whichever gives the
  nicer gap.
- **Nebraska and Kansas regulate differently**, and many Nebraska NRDs set no
  allocation at all. The two states' limits are sourced independently. Nothing
  here assumes they share a mechanism or a magnitude.
- **PCSE mechanics.** Brief 0002's irrigation is a PCSE `StateEvent` on `SM`.
  A `StateEvent` cannot keep a running seasonal total or spread an application
  over days, so enforcing either limit probably needs a different mechanism,
  such as stepping the engine day by day and sending the irrigate signal from
  the runner. Whatever is chosen:
  - it uses the pinned PCSE 6.0.13 and the pinned crop-parameter commit;
  - it keeps `Wofost72_WLP_FD`;
  - it respects the three PCSE traps brief 0002 documented: the keyword is
    `amount`, amounts are in **cm**, and a trailing empty campaign is needed
    when `StateEvents` are used.
- **The baseline must be built under the same limits the projection uses**, or
  the anomaly compares two different models. `build_baselines.py` imports the
  irrigation logic from `runner.py`, as it already imports the agromanagement
  builder.
- **Baseline period is fixed by node 3's contract.** 1995-2024.
- **Determinism and no network at run time.** Unchanged. Any sourcing is by
  committed documentation or one-time build scripts, never at run time.
- **Not a breaking change for node 3.** Region keys and required key sets are
  unchanged. After merge, node 2 must be re-registered on Model Home, and any
  stored flow run's irrigated-strata figures are superseded. Say so in the pull
  request.
- **A stratum is not "the better half"**, as brief 0002 recorded, and a capped
  irrigated stratum is not guaranteed to beat its rainfed neighbour in every
  year. A year where it does not is data.

Repo-wide conventions live in [`CLAUDE.md`](../../CLAUDE.md) and are not
restated here.

## General guidance

- Before you write the plan, ask any questions you need to. Two are expected to
  be blocking:

  1. **Where do the limits come from?** For each state: the specific rule
     (named NRD or GMD/LEMA order and its date), the value, and whether it
     applies at the centroid or as a production-weighted representative (see
     constraints). Verify against the current published rules, not memory.
     For well capacity, name the study or dataset (K-State Research and
     Extension and UNL Extension both publish well-capacity and
     capacity-versus-yield work for these regions) and the conversion to
     cm/day.
  2. **How is a limit enforced in PCSE?** Recommend a mechanism, verified
     against the pinned 6.0.13 source, and show it satisfies AC-4 before
     sourcing values on top of it. If the capacity ceiling spreads an
     application over days, say how the trigger behaves while an application
     is still in progress, so it cannot re-fire and double-count.

- Present-day limits constant across the baseline is decided. Do not reopen it
  unless a source makes it indefensible, and then raise it rather than working
  around it.
- Read brief 0002's plan (`docs/plans/0002-irrigation-strata.md`), especially
  C-1 to C-4, before touching the irrigation code. Every trap it found applies
  here.
- The ten rainfed regions are a regression surface. Treat "identical output" as a
  property to prove, not to assume.
