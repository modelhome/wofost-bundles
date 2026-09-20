# wofost-bundles

Standalone Model Home **model bundles** built on
[PCSE](https://github.com/ajwdewit/pcse)/WOFOST, the crop growth model behind
the EU MARS operational yield-forecasting system. Each bundle is a
self-contained folder with everything Model Home needs to run one model: a
`Modelfile.toml`, a `Dockerfile`, a `runner.py`, and sample input(s).

This repo is **not** a fork of PCSE. It holds only the Model Home packaging
layer. PCSE is pulled in as a pinned pip package inside a bundle's Docker
image; nothing upstream is vendored here. Keep it that way.

```
wofost-bundles/
  CLAUDE.md                 <- you are here
  README.md
  LICENSE                   (MIT)
  .claude/skills/feat/      <- vendored feat skill (brief -> plan -> PR workflow)
  docs/features/            <- feature briefs (NNNN-name.md)
  docs/plans/               <- implementation plans, one per brief
  <bundle>/                 <- one self-contained model per folder
```

This is the agronomic engine of a climate -> agriculture -> finance flow:

| Node | Repo | Job |
|---|---|---|
| 1 | `agromet-bundles/crop-weather/` | the daily weather series, in PCSE's own variables and units |
| **2** | **`wofost-bundles/corn-yield/`** | **phenology, projected yield, weather-driven yield anomaly** |
| 3 | `ag-commodity-bundles/corn-price/` | the price impact of node 2's anomaly |

Node 2 is where a weather event becomes a yield consequence *conditioned on the
crop's development stage*. Node 1's output is node 2's input: they are semantic
peers, composed in a Model Home Flow.

---

## The templates: `QuantLib-bundles/bond/`, `thermofeel-bundles/thermal-indices/`, `agromet-bundles/crop-weather/`

[`bond/`](https://github.com/modelhome/QuantLib-bundles),
[`thermal-indices/`](https://github.com/modelhome/thermofeel-bundles) and
[`crop-weather/`](https://github.com/modelhome/agromet-bundles) are the
authoritative templates. Read them before starting a bundle and mirror them.
`bond` is the minimal shape; `thermal-indices` is the shape for a bundle with a
committed climatology table built by a one-time script; `crop-weather` is the
immediate upstream and the closest model for this repo's conventions.
Consistency with them matters more than any local preference:

- **`Modelfile.toml` keys.** `name`, `description`, `run`, `image`, `args`,
  `[resources]`, `[[inputs]]` with a documented `[inputs.schema]` (every
  property has a plain-language `description`; the schema's `default` is what
  the platform offers as the "Example to paste", so it must stay runnable), and
  `[[outputs]]` with `[outputs.schema]`. Add the annotation fields Model Home
  validates: `determinism`, `expected_runtime`, `validity_domain`, `not_for`,
  `provenance`, and per-property `unit`. Rationale the schema cannot express
  goes in TOML comments beside it. The platform validator caps
  `validity_domain` at **600 characters** and `provenance` at **400**, and
  rejects any key listed in a `required` array that has no declared
  `properties.<key>.type`; longer prose belongs in the README.
- **Runner I/O contract.** Input JSON file path(s) arrive as positional args.
  The result JSON goes to **stdout** and nothing else does; logs go to stderr.
  The Modelfile's `run` redirects stdout to `run/<output>.output.json`. Further
  outputs are passed as `{output:NAME}` args.
- **`required = []` and defaults in the runner.** A present-but-empty value
  (`""` or `null`) falls back the same way a missing key does, so the model runs
  standalone or composed.
- **Dockerfile.** `python:3.12-slim`, `WORKDIR /app`, exact `==` pins installed
  in one `pip install --no-cache-dir` layer, `COPY` paths relative to the bundle
  folder, `ENTRYPOINT ["python", "runner.py"]`, and a `CMD` naming the bundled
  sample input so a bare `docker run` works.
- **Stdlib over dependencies.** PCSE, numpy and pandas are this repo's reason to
  exist; nothing else gets added without a clear reason.

## Model Home platform facts (verified against the platform code)

- **Build context is the bundle subfolder.** Adding a model from
  `github.com/modelhome/wofost-bundles/tree/main/<bundle>` promotes that folder
  to the build-context root, exactly like `cd <bundle> && docker build .`. Never
  use repo-relative `COPY <bundle>/...` paths; the on-platform build fails.
- **Every output is a JSON file.** The platform collects only
  `/run/<name>.output.json` for each declared `[[outputs]]` name and parses it
  with `json.loads`. Any other file a runner writes (a CSV, a PNG) is discarded
  on-platform. A long-format table therefore ships as JSON
  (`{metadata, columns, rows}`); a CSV written beside it is for off-platform use
  only, and its README must say so.
- **A schedule re-sends a fixed input.** A scheduled run passes the same stored
  input every time, so anything that should change per run (such as "today")
  must be defaulted inside the runner, not baked into the schedule's input.
- **Flow steps connect by output shape, not by name**, and "shape" has an exact
  meaning: `check_schema_compatibility` in
  `orchestration/modelfile/validation.py` requires the downstream input's
  `required` keys to be a **subset** of the upstream output's `required` keys,
  then recurses into `properties` and array `items`, comparing declared `type`s
  by equality with one widening (a `number` input accepts an `integer` output).
  A property with no declared `type` is unconstrained and is not compared.
  Practical consequence: **an output is selected by its required-key set**, so
  declaring `required = ["metadata", "columns", "rows"]` binds to
  `crop_weather_daily` and cannot bind to `crop_weather_summary`, whose required
  keys are `generated_at`, `metadata`, `regions`.

## The PCSE contract (verified against PCSE 6.0.13)

Read the source, not the README, and re-verify against the pinned version.
`crop-weather`'s `CLAUDE.md` carries the full weather-variable table; the
essentials this repo depends on:

- `WeatherDataContainer` units are **container** units, not PCSE's *CSV file*
  units: `TMIN`/`TMAX` degC, `IRRAD` J/m2/day, `VAP` hPa, `WIND` m/s at 2 m,
  `RAIN` **cm**/day, `DAY` a `datetime.date`. Range checks raise `PCSEError`, so
  a unit slip usually surfaces as a range error rather than a wrong yield.
- **`E0`, `ES0` and `ET0` are required** and WOFOST reads them directly. Node 1
  deliberately does not compute them: they are PCSE's own physics, so they are
  **this repo's job**. Derive them with
  `pcse.util.reference_ET(DAY, LAT, ELEV, TMIN, TMAX, IRRAD, VAP, WIND, ANGSTA,
  ANGSTB, ETMODEL="PM")`, which returns **mm/day**, and divide by 10 for the
  container's cm/day. Node 1 emits the Angstrom A/B it estimated per region in
  its output metadata; use those rather than re-estimating.
- **Build the provider by subclassing `WeatherDataProvider`** and calling
  `self._store_WeatherDataContainer(container, day)` per day, after setting
  `latitude`, `longitude`, `elevation`, `angstA`, `angstB` and `description`.
  There is no public "from a table" constructor; `crop-weather/check_weather.py`
  has a working 25-line example, which is the reference implementation.

### Engines and parameters (verified 2026-09-19 on `crop-weather`'s real output)

- `pcse.models` ships `Wofost72_PP` (potential) and **`Wofost72_WLP_FD`**
  (water-limited, free-draining), among others. `WLP_FD` is the water-limited
  engine this repo uses; it adds **`RFTRA`** to the daily output, the
  transpiration reduction factor (1.0 = unstressed), which is the natural
  water-stress indicator.
- Daily output variables from a `Wofost72_WLP_FD` run: `DVS`, `LAI`, `RD`,
  `RFTRA`, `SM`, `TAGP`, `TRA`, `TWLV`, `TWRT`, `TWSO`, `TWST`, `WWLOW`.
  Summary output carries `DOS`, `DOA`, `DOM`, `TWSO`, `LAIMAX`.
- **Crop parameters do NOT ship inside the pip package**, whatever a warm
  developer cache suggests. `YAMLCropDataProvider()` with no `fpath` downloads
  `ajwdewit/WOFOST_crop_parameters` from GitHub and caches it **for seven days**,
  so a bundle that relies on it is neither offline nor stable: it fails with no
  network, and starts failing a week after the image was built even with one.
  Bake the repository into the image at a pinned commit and pass `fpath`. It
  offers maize varieties `Grain_maize_201` ... `_205`, `Fodder_maize_nl` and
  `Maize_VanHeemst_1988`; the `20x` series is a maturity-class ladder
  (TSUM1/TSUM2 = 695/800, 695/860, 775/880, 855/900, 935/920), which is how a
  north-to-south US relative-maturity gradient is expressed.
- **Pin the crop parameters, and check the pin.** Anything precomputed against
  those parameters (a baseline distribution, a calibration) must record the
  exact commit it used, and the runner must refuse to run against a different
  one. Otherwise a parameter change silently makes a precomputed comparison
  wrong while the metadata still claims the two match -- a wrong answer, which
  is worse than a failed run.
- **The Modelfile schema format has no nullable type.** `schema_type` requires a
  plain string, so `["number", "null"]` is not expressible. A field that can be
  empty therefore must not appear in any `required` list -- and if a required
  field can go null, fix the model rather than the declaration.
- **PCSE writes to stdout on first import.** Into a fresh home directory it
  prints `Building PCSE demo database at: ... OK`. Every runner here redirects
  stdout to stderr around the PCSE import, because the platform parses stdout
  with `json.loads` and that one line breaks the first run in a fresh container.
- `DummySoilDataProvider()` is a generic medium soil: `SM0` 0.4, `SMFCF` 0.3,
  `SMW` 0.1, `RDMSOL` 120, `CRAIRC` 0.06, `K0`/`SOPE`/`KSUB` 10.0. It is a
  starting point, not a regional soil; see the bundle's plan for what replaces
  it and why the choice matters.

## Conventions for every bundle

- **Read the upstream source and tests before coding.** Take variable names,
  units and formulae from the source, not from a README or from this file.
- **Unit slips are the classic bug.** Convert at one boundary and name variables
  with their unit (`rain_mm`, `yield_kg_ha`, `yield_bu_acre`). Every conversion
  gets a comment naming both units and the factor.
- **Commit a validation check** per bundle, run outside the image, that proves
  the bundle's output is what the downstream consumer expects, and that includes
  a real model run rather than eyeballed columns.
- **Pin everything** in the Dockerfile.
- **Readable over clever.** These inputs are small (tens of sites x hundreds of
  days).
- **Parameters, not constants.** Agronomic choices are declared, annotated
  inputs with sensible defaults.
- **No emojis** in source files.

### Modelling honesty

This repo publishes numbers that look like forecasts, so the labelling matters
as much as the arithmetic:

- **Uncalibrated absolute yields are illustrative.** WOFOST run on generic
  parameters does not reproduce USDA bushel levels and must never be presented
  as a forecast of them. The market-relevant output is an internally consistent
  **percentage anomaly** against a normal-weather baseline run of the same
  model, which cancels the calibration error.
- **Keep overlays separable.** Anything bolted on top of WOFOST's own physiology
  (for example a corn heat-sterility adjustment at silking, which base WOFOST
  does not model) is labelled an overlay, is parameterised, and is reported
  *alongside* the pure-WOFOST figure so its effect is auditable.
- **Seasonality is expected, not a defect.** Output is most meaningful from
  about April to October; pre-planting and early-season runs are near-baseline
  and quiet. Say so rather than tuning it away.

### Determinism

Unlike `crop-weather` and `thermal-indices`, bundles here make **no network
calls at run time**: they are pure functions of their input plus committed
tables. Climatology normals and any sourced parameters are fetched only by
one-time committed build scripts. State this in the bundle README and the
Modelfile annotations, and keep it true -- the moment a runner fetches, the
determinism claim changes category.

### Region identity

Bundles in this flow share one region set, and **node 1 owns it**. The
`region_key`, its coordinates and its elevation originate in
`agromet-bundles/crop-weather/regions.csv` and propagate unchanged. This repo
joins on the key and adds its own attributes (planting dates, maturity classes,
soils, climatology normals); it never redefines the key, and an input
`region_key` with no matching table row **fails the run loudly** rather than
being dropped.

## How features are built: `feat`

Features are developed from versioned briefs with the vendored
[`feat`](./.claude/skills/feat/SKILL.md) skill, so the brief, the plan and the
implementation land together in one pull request:

1. `/feat create <name>` scaffolds `docs/features/NNNN-<name>.md`. Hand-written
   briefs in the same template are fine.
2. `/feat plan <name>` writes `docs/plans/NNNN-<name>.md` and stops. John reviews
   and revises the plan before anything is built.
3. `/feat run <name>` implements the approved plan on `feat/NNNN-<name>` and
   stops at the pull request. It never merges, releases or deploys.

Repo-wide conventions live in this file; briefs reference them rather than
restating them.

## The `corn-yield/` bundle

**US Corn Yield (WOFOST).** Takes a `crop-weather` output and returns, per US
corn region, the crop's development stage, a full-season projected yield, a
weather-driven yield anomaly and percentile rank against a thirty-year
normal-weather distribution, and heat and frost days counted inside the
lifecycle windows where they matter. Brief:
`docs/features/0001-corn-yield.md`; plan with every decision and its reasoning,
including the conflicts found while building it:
`docs/plans/0001-corn-yield.md`. User-facing documentation:
[`corn-yield/README.md`](./corn-yield/README.md).

```
corn-yield/
  Modelfile.toml          one input (node 1's table), two JSON outputs
  Dockerfile              multi-stage: crop parameters pinned, then the model
  runner.py               the model
  soils.csv               per-region water-holding parameters
  planting_dates.csv      per-region planting date and maturity class
  climatology.csv         per-region daily normals, 1995-2024 (3,650 rows)
  baseline_yields.csv     30 normal-weather yields per region (300 rows)
  *.meta.json             provenance for the two built tables
  build_climatology.py    one-time normals build (not in the image)
  build_baselines.py      one-time baseline build (not in the image)
  check_yield.py          validation incl. real WOFOST runs (not in the image)
  sample_input.json       a real node 1 output: Iowa and Nebraska, 2026
  README.md
```

### Design notes

- **The baseline is thirty real years, not a mean climatology.** This is the
  one thing to understand before changing anything here. Driving a baseline
  season with mean-by-calendar-day normals was the original design and it is
  wrong: averaging preserves a season's rainfall *total* but destroys its
  *structure* (for Iowa, 148 wet days instead of 59 and no dry day at all), and
  WOFOST's free-draining water balance responds to structure. The baseline crop
  starved and anomalies came out at +45% and +856%. The baseline is now the
  median of one run per year of 1995-2024 on real daily weather, precomputed by
  `build_baselines.py` because it never depends on the input. The climatology is
  still used, but only to complete the tail of a season whose profile is already
  charged by real weather, where the objection does not apply.
- **`WAV` is the footgun.** PCSE's `DummySoilDataProvider` with `WAV=100` gives
  the crop 100 cm of available water, so it is never short and `WLP_FD`
  degenerates into `PP`: a 1.2% gap on the sample, and a yield anomaly that
  measures nothing. `soils.csv` sets realistic per-region values (9.6-17.6 cm)
  and `check_yield.py` asserts the water balance is live for every region.
- **One simulation per region at run time.** The projection is the only WOFOST
  run the model does; the thirty baseline runs are precomputed.
- **Two JSON outputs**, `corn_yield_snapshot` (the table node 3 reads) and
  `corn_yield_trajectory` (nested, with the daily DVS series). The CSV beside
  them is for off-platform use only.
- **Percentage and percentile.** The yield distribution is strongly skewed
  (Iowa spans 81 to 13,413 kg/ha over the period), so `yield_percentile_rank`
  ships beside `yield_anomaly_pct`: magnitude from one, context from the other.
- **No overlay.** Stage-specific stress is reported as day counts only; every
  yield figure is pure WOFOST. The README says how a silking-heat adjustment
  would be added and what it would need first.
- **Angstrom coefficients are committed**, estimated once from the thirty-year
  radiation series and used by both the baseline and the projection, so the two
  differ in nothing but weather. This deliberately does not use node 1's
  per-run estimate.

### Verified results (2026-09-19)

- `check_yield.py`: **54/54 checks pass**. That includes real `Wofost72_WLP_FD`
  runs reaching maturity, the water balance binding for every region (a quarter
  of the rain costs Iowa and Nebraska real yield), the hand-worked stress-window
  cases including both window edges, the anomaly arithmetic, and an unknown
  `region_key` exiting 1 with a readable message naming the key and the table.
- **AC-6 demonstrated:** injecting a hot, dry fortnight over each region's own
  projected flowering date moves Iowa from +22.02% to **-34.2%** (silking heat
  days 0 -> 11) and Nebraska from +51.9% to **+7.96%** (5 -> 11).
- Sample run (2026-09-19, Iowa and Nebraska): ia mature, anthesis 2026-07-05,
  maturity 2026-08-24, 10,053 kg/ha (160 bu/acre), anomaly +22.02%, percentile
  70, mean RFTRA 0.90, 0 silking heat days; ne mature, anthesis 2026-07-03,
  maturity 2026-08-18, 5,979 kg/ha (95 bu/acre), anomaly +51.9%, percentile 80,
  mean RFTRA 0.79, **5 silking heat days**. About 30 s for two regions.
- **Docker build and run** produce rows, columns and trajectory **identical** to
  the local run, metadata identical apart from `generated_at`, with
  `--network none`.
- **Modelfile validates** (`OK`, no annotation warnings), and
  `check_schema_compatibility` confirms the input binds to node 1's
  `crop_weather_daily` and is correctly refused by `crop_weather_summary`.
- Baseline medians (kg/ha, 1995-2024): mn 10,131, wi 8,816, ia 8,238, oh 8,165,
  il 7,631, in 7,567, mo 6,252, ne 3,936, sd 3,098, ks 1,491. The low western
  numbers are dryland simulations of states whose corn is substantially
  irrigated -- a documented limitation, not a bug.
- **Copilot review (PR #1):** four findings, all addressed -- an unpinned
  crop-parameter baseline, required-but-nullable output fields, silent gap
  filling, and a stale annotation. Checks went 54 -> **82/82**. Rebuilding the
  baseline against the pinned checkout changed no committed number.
- **Not yet verified:** the Model Home import (AC-10), which needs a signed-in
  human at the Auth0 login.

### Task list

1. AC-10: add the model on the local Model Home stack from the branch subfolder
   URL and run it with node 1's output.
2. Mark the PR ready once AC-10 passes; John merges.
3. After merge: register on Model Home from `main` and compose it after
   `crop-weather` in a daily flow.
4. Follow-ups, detailed in the bundle README: soils from gNATSGO/SSURGO, an
   irrigation share per region, a parameterised silking-heat overlay, and
   crop-reporting-district granularity.

## Task list

1. ~~Create `modelhome/wofost-bundles` on GitHub and push `main`.~~ Done
   2026-09-19.
2. Finish `corn-yield/` (brief 0001): see that bundle's task list above.
3. Sibling repo still to come: `ag-commodity-bundles/corn-price/` (node 3),
   which consumes this bundle's `yield_anomaly_pct` and `yield_percentile_rank`.
