# Corn yield

## Outcome

`corn-yield/` is a self-contained Model Home model that takes a node 1
(`agromet-bundles/crop-weather/`) output and returns, for each US corn-growing
region: the crop's current development stage and estimated days to anthesis and
maturity, a full-season projected yield, a weather-driven yield anomaly against
a normal-weather baseline, and stage-specific stress diagnostics that intersect
node 1's stress-day flags with the sensitive windows of the crop's lifecycle.

Pasting the subfolder's GitHub URL into
`http://localhost:5173/models/new/repo` creates a working model, and Model Home
composes it after `crop-weather` in a flow that runs daily.

This is node 2 of a three-node climate -> agriculture -> finance flow: forecast
weather -> corn crop model (WOFOST, phenology-aware yield) -> corn commodity
price impact. Node 2 is where a weather event becomes a yield consequence
*conditioned on the crop's development stage*: WOFOST tracks development on a
unitless scale (0 at emergence, 1 at anthesis, 2 at maturity) driven by
accumulated temperature, so the same heat or frost event matters enormously at
silking and little during vegetative growth. Surfacing that is the point of the
node.

Node 2's input is node 1's output, unchanged: they are semantic peers. Node 3
consumes node 2's percentage anomaly.

## Scope

### In scope

**Repo scaffold.** Top-level `.gitignore`, `.dockerignore`, `LICENSE` (MIT),
`README.md` and `CLAUDE.md` matching `agromet-bundles` and `thermofeel-bundles`,
plus the vendored `feat` skill, then `corn-yield/` beneath it.

**The `corn-yield/` bundle:** `Modelfile.toml`, `Dockerfile`, `runner.py`, a
sample input JSON, the committed static tables, the build script(s) for the
sourced ones, and a validation check run outside the image.

**Input contract.** Node 2's input schema *is* node 1's output schema. Per
region node 1 supplies `region_key, state, LAT, LON, ELEV`, the daily series
`TMIN, TMAX, IRRAD, VAP, WIND, RAIN` from season start through today plus a
forecast, `is_forecast` per day, and agromet columns (`gdd_cumulative`,
`frost_day`, `heat_stress_day`, and the season-to-date counts). The runner
builds a PCSE `WeatherDataProvider` per region from that series. The set of
regions to process comes from the input, not from a redefinition here.

**Region-keyed static tables**, joined on node 1's `region_key`, for the same
ten corn states node 1 defines (read
`agromet-bundles/crop-weather/regions.csv` for the exact key set):

- **Maize crop parameters** -- a documented WOFOST maize parameter set, cited,
  and labelled illustrative rather than locally calibrated.
- **Soil parameters** -- a documented water-holding parameter set, needed for
  water-limited mode; a standard generic soil is acceptable for v1 provided the
  choice is documented.
- **Planting date** per region, from USDA planting-date norms, committed and
  sourced.
- **Daily climatology normals** per region, matching node 1's variable set and
  units, built once from a long baseline period by a committed script, with the
  period, source and method documented.

**The WOFOST run.** Water-limited mode (rainfed corn: drought is a primary US
yield driver and it uses node 1's `RAIN`), starting at the region's configured
planting date. The driving weather is assembled as **observed (planting ->
today) + node 1 forecast (today -> +~16 d) + climatology normals (remainder ->
maturity)**, so a full-season projection is available mid-season. The normals
portion shrinks and the projection sharpens as the season progresses.

**Phenology outputs.** Current development stage (DVS), its stage name, and
estimated days to anthesis and to maturity; plus the daily DVS trajectory over
the season, so a downstream view can show the crop advancing through stages
with stress events marked.

**Yield anomaly.** A projection run (observed + forecast + normals) and a
baseline run (climatology normals for the whole season), with
`yield_anomaly_pct = (projection - baseline) / baseline`. Absolute WOFOST
yields are emitted too (kg/ha native, and bu/acre by a documented conversion)
for diagnostics, clearly labelled uncalibrated.

**Stage-specific stress diagnostics.** Intersect node 1's `heat_stress_day` and
`frost_day` flags with the DVS-derived sensitive windows -- the silking/anthesis
window around DVS ~ 1, and killing-frost sensitivity near emergence and
pre-maturity -- and emit counts such as `heat_stress_days_in_silking_window`.
Optionally apply a documented, parameterised yield adjustment for heat days in
the silking window, kept separable and reported alongside the pure-WOFOST
anomaly.

**Output.** A long-format table (snapshot rows plus season-trajectory rows) and
a convenience nested-per-region document, following the `crop-weather` output
convention.

### Out of scope

- Node 1 (weather) and node 3 (price), their repos, and the flow definition,
  which lives in the platform: no Flowfile.
- USDA production and price levels, and any trend-yield table in absolute
  bushels. Those belong to node 3, which applies node 2's percentage anomaly to
  real USDA levels. Node 2 outputs a weather-driven **relative** shock, not a
  calibrated absolute forecast.
- The SPA and any downstream visualisation.
- Redefining regions. Node 2 reads the region set from its input and keys its
  tables on node 1's `region_key`.

## Acceptance criteria

- **AC-1** -- `modelhome/wofost-bundles` exists with top-level `.gitignore`,
  `.dockerignore`, `LICENSE` (MIT), `README.md`, `CLAUDE.md`, and a
  `corn-yield/` subfolder, matching `agromet-bundles` and `thermofeel-bundles`
  conventions.
- **AC-2** -- `corn-yield/` contains `Modelfile.toml`, `Dockerfile`,
  `runner.py`, a sample input JSON, and the committed static tables (maize crop
  parameters, soil, planting dates, climatology normals) plus the build scripts
  for the sourced tables.
- **AC-3** -- The sample input is a real, small node 1 `crop-weather` output --
  a couple of regions across the full window -- committed so the model runs
  standalone. `python corn-yield/runner.py corn-yield/<sample_input>` runs end
  to end and writes the declared outputs with the schema above.
- **AC-4** -- `docker build` from the bundle folder succeeds and `docker run`
  reproduces identical outputs. The model needs no network.
- **AC-5** -- WOFOST runs per region in water-limited mode, driven by a
  `WeatherDataProvider` built from node 1's series, and produces a plausible
  DVS trajectory reaching maturity. A committed check demonstrates a
  full-season run and the phenology outputs.
- **AC-6** -- The yield anomaly is computed as (projection - normals baseline) /
  normals baseline. Switching the driving weather -- injecting a hot, dry spell
  in the silking window into the sample input -- moves the anomaly in the
  expected direction, demonstrated by a committed check.
- **AC-7** -- Stage-specific stress diagnostics correctly intersect node 1's
  stress-day flags with the DVS sensitive windows, shown against a hand-worked
  example, and the overlay is separable and labelled, with the pure-WOFOST
  anomaly reported alongside any adjusted figure.
- **AC-8** -- All static tables are keyed on node 1's `region_key`, read from
  `agromet-bundles/crop-weather/regions.csv`. An input `region_key` with no
  matching table row fails the run loudly.
- **AC-9** -- With a node 1 output as input and no other parameters, the model
  runs the full region set using the committed defaults, so the scheduled flow
  needs no manual parameters.
- **AC-10** -- Pasting the `corn-yield/` subfolder GitHub URL into
  `http://localhost:5173/models/new/repo` creates a working model whose run
  produces the expected artifacts.
- **AC-11** -- The bundle README documents: the PCSE/WOFOST version and run
  configuration; the crop-parameter and soil sources; the planting-date and
  climatology-normals sources, periods and methods; the observed + forecast +
  normals season-completion approach; the yield-anomaly definition and why it
  avoids calibration; the stage-stress overlay and its limitations (base WOFOST
  does not model corn heat-sterility at silking); the kg/ha -> bu/acre
  conversion; and the determinism and offline semantics.

## Constraints and dependencies

- **PCSE is the core runtime dependency.** Pin `pcse` (and numpy/pandas if
  needed) in the Dockerfile, mirroring how `thermal-indices` pins `thermofeel`.
  Read PCSE's source for the run configuration -- crop, soil and agromanagement
  providers, the water-limited engine, and building a `WeatherDataProvider`
  from tabular data -- before implementing.
- **No network at run time.** The model is a pure function of the node 1 input
  plus the committed tables. Climatology normals and any sourced parameters are
  fetched only by one-time committed build scripts, never by the runner.
- **Flow-handoff format.** Confirm what the platform passes from node 1 before
  building the parser, and design the input declaration to mirror node 1's
  output declaration.
- **`E0`, `ES0` and `ET0` are this node's job.** Node 1 deliberately omits the
  three evaporation terms PCSE requires because they are PCSE's own physics.
  This node owes them, from `pcse.util.reference_ET` and node 1's per-region
  Angstrom coefficients.
- **Region key originates in node 1.** Key every table on it, and fail loudly on
  a key with no row. Do not redefine regions or invent keys.
- **Calibration honesty.** Uncalibrated WOFOST absolute yields are illustrative;
  the market-relevant output is the internally consistent percentage anomaly.
  Never present absolute bu/acre as a forecast of the USDA number.
- **Stress-overlay honesty.** The stage-specific heat-stress effect is a
  documented overlay on top of WOFOST, not core WOFOST physiology. Keep it
  separable and report the pure-WOFOST anomaly alongside.
- **Seasonality is expected.** Output is most meaningful from about April to
  October; pre-planting and early-season output is near-baseline and quiet.
  That is not a defect.
- **Licence** MIT. Provenance is PCSE/WOFOST from pip, the cited crop and soil
  parameter sources, and documented data tables -- not vendored analysis code.

Repo-wide conventions -- mirroring `bond/`, `thermal-indices/` and
`crop-weather/`, the Modelfile and runner contracts, the PCSE contract, unit
discipline, modelling honesty and region identity -- live in
[`CLAUDE.md`](../../CLAUDE.md) and are not restated here.

## General guidance

- Before you write the plan, ask any questions you need to in order to best
  implement the brief. Two are expected to be blocking: the flow-handoff format
  from node 1, and confirmation of the exact PCSE run configuration and
  crop-parameter source.
- Read node 1 (`agromet-bundles/crop-weather/`), `thermal-indices/` and `bond/`
  first, and mirror their conventions. Consistency across the bundles matters
  more than any local preference.
- Design the output schema as a clean input for node 3: a per-region
  weather-driven yield anomaly it can production-weight and apply to USDA
  levels.
- Document every modelling choice in the README and the Modelfile
  validity-domain annotations.
