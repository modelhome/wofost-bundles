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
  goes in TOML comments beside it. `validity_domain` is capped at **600
  characters** by the platform validator; longer prose belongs in the README.
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
- **Crop parameters ship inside the pip package.** `YAMLCropDataProvider()`
  needs no download and offers maize varieties `Grain_maize_201` ... `_205`,
  `Fodder_maize_nl` and `Maize_VanHeemst_1988`. The `20x` series is a
  maturity-class ladder (TSUM1/TSUM2 = 695/800, 695/860, 775/880, 855/900,
  935/920), which is how a north-to-south US relative-maturity gradient is
  expressed. Provenance is `ajwdewit/WOFOST_crop_parameters`.
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

## The `corn-yield/` bundle (planned, not yet built)

**US Corn Yield (WOFOST).** Takes a `crop-weather` output and returns, per US
corn region, the crop's current development stage, a full-season projected
yield, a weather-driven yield anomaly against a normal-weather baseline, and
stage-specific stress diagnostics. Brief:
`docs/features/0001-corn-yield.md`; plan with every decision and its reasoning:
`docs/plans/0001-corn-yield.md`.

## Task list

1. Review and approve `docs/plans/0001-corn-yield.md`, then `/feat run corn-yield`.
2. Create `modelhome/wofost-bundles` on GitHub and push `main`.
3. After the PR merges: register the model on Model Home from `main` and compose
   it after `crop-weather` in a daily flow.
4. Sibling repo still to come: `ag-commodity-bundles/corn-price/` (node 3),
   which consumes this bundle's `yield_anomaly_pct`.
