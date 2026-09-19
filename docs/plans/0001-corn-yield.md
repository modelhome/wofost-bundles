# Plan: Corn yield

Source brief: docs/features/0001-corn-yield.md
Status: blocked
Planned against commit: b408b0b (chore: scaffold wofost-bundles)
Base commit: b408b0b (main at branch creation)

## Outcome

`corn-yield/` is a self-contained Model Home model that takes an
`agromet-bundles/crop-weather` output and returns, per US corn region: the
crop's development stage and signed days to anthesis and maturity, a
full-season projected yield, a weather-driven yield anomaly against a
normal-weather baseline run of the same model, and stress-day counts taken
*inside* the lifecycle windows where they matter. It makes no network calls at
run time.

Node 2 of the climate -> agriculture -> finance flow. Its input is node 1's
output unchanged; its `yield_anomaly_pct` is node 3's input.

## Scope

### In scope

- The `corn-yield/` bundle: `Modelfile.toml`, `Dockerfile`, `runner.py`, a
  committed sample input, four committed static tables, the climatology build
  script, and `check_yield.py` run outside the image.
- A `WeatherDataProvider` built from node 1's series, including the three
  evaporation terms node 1 deliberately leaves to this node.
- Two `Wofost72_WLP_FD` runs per region (projection and normals baseline).
- Phenology, yield, anomaly and stage-specific stress outputs.

### Out of scope

- Nodes 1 and 3, and the flow definition (composed in the platform; no
  Flowfile).
- USDA production/price levels and any absolute-bushel trend yield: node 3's
  job.
- Any yield adjustment overlay on top of WOFOST (see D6) and any SPA.
- Redefining regions (D11).

## Assumptions and decisions

The brief asked for two blocking questions. Both were resolved by reading the
platform source and by running PCSE against node 1's real output rather than by
asking, so they are recorded here as answered with their evidence. Three further
decisions that would have materially changed the work were put to John and his
answers are recorded in D4, D6 and D7.

### D1. The handoff is JSON, and specifically node 1's `crop_weather_daily` (answered, verified)

The brief's blocking question (a), JSON vs CSV, is settled by the platform:
`orchestration` collects only `/run/<name>.output.json` per declared output and
parses it with `json.loads`. Any CSV a runner writes is discarded on-platform.
So the handoff is JSON and there is no CSV option to weigh.

The real question is *which* of node 1's two JSON outputs binds, and that is
decided by `check_schema_compatibility`
(`orchestration/modelfile/validation.py`): the downstream input's `required`
keys must be a **subset** of the upstream output's `required` keys, then the
check recurses into `properties` and array `items` comparing declared `type`s.

Node 1 declares:

| Output | `required` |
|---|---|
| `crop_weather_daily` | `metadata`, `columns`, `rows` |
| `crop_weather_summary` | `generated_at`, `metadata`, `regions` |

This bundle therefore declares one input with
`required = ["metadata", "columns", "rows"]`, which binds to
`crop_weather_daily` and **cannot** bind to `crop_weather_summary`. The
long-format table is also the better fit: it carries `is_forecast` per row and
the per-region Angstrom coefficients in `metadata`, both of which this node
needs.

The runner parses the row list rather than the nested summary, groups by
`region_key`, and sorts by `date` defensively rather than trusting row order.

### D2. The PCSE run configuration (answered, verified by running it)

The brief's blocking question (b). Verified on 2026-09-19 against PCSE
**6.0.13** using node 1's real 2026 output for Iowa:

| Piece | Choice | Evidence |
|---|---|---|
| Engine | `pcse.models.Wofost72_WLP_FD` | water-limited, free-draining; the only water-limited 7.2 engine besides `WLP_CWB`. Adds `RFTRA` to daily output, which is the water-stress indicator the brief asks for |
| Crop parameters | `pcse.input.YAMLCropDataProvider()`, crop `maize` | ships **inside the pip package**; no download and no committed crop file. Provenance `ajwdewit/WOFOST_crop_parameters` |
| Variety | per region from the `Grain_maize_201`..`_205` ladder | TSUM1/TSUM2 = 695/800, 695/860, 775/880, 855/900, 935/920: a maturity-class ladder, which is exactly how a north-to-south US relative-maturity gradient is expressed (D10) |
| Site | `pcse.input.WOFOST72SiteDataProvider(WAV=...)` | `WAV` is initial available soil water in cm and matters a great deal under `WLP_FD` (D4) |
| Soil | committed per-region table (D4) | not `DummySoilDataProvider` |
| Agromanagement | a plain Python dict list built in the runner | no YAML agromanagement file. `crop_start_type = "sowing"` at the region's planting date, `crop_end_type = "maturity"`, `max_duration = 200` |
| Weather | subclass of `pcse.base.weather.WeatherDataProvider` | there is no public "from a table" constructor; set `latitude`/`longitude`/`elevation`/`angstA`/`angstB`/`description`, then `self._store_WeatherDataContainer(container, day)` per day. `crop-weather/check_weather.py` has the working 25-line reference implementation to mirror |

`E0`, `ES0` and `ET0` are this node's debt, as node 1's README states. Derive
them per day with
`pcse.util.reference_ET(DAY, LAT, ELEV, TMIN, TMAX, IRRAD, VAP, WIND, angstA,
angstB, "PM")`, which returns **mm/day**, and divide by 10 for the container's
cm/day. Take `angstA`/`angstB` from node 1's `metadata.angstrom[region_key]`
rather than re-estimating them, so both nodes use one number.

Verified result, Iowa, sown 2026-05-01 on node 1's real series:

```
PP     : DOA=2026-06-30  DOM=2026-08-13  TWSO=10061 kg/ha  LAIMAX=4.22
WLP_FD : DOA=2026-06-30  DOM=2026-08-13  TWSO= 9945 kg/ha  LAIMAX=3.94
```

Daily variables available from `WLP_FD`: `DVS, LAI, RD, RFTRA, SM, TAGP, TRA,
TWLV, TWRT, TWSO, TWST, WWLOW`. Summary: `DOS, DOA, DOM, TWSO, LAIMAX`.

### D3. Season completion: observed + forecast + normals

The driving series for the **projection** run is spliced, in this order, and
every day is tagged with its provenance:

1. **observed** -- node 1 rows with `is_forecast = false`, from the planting
   date forward;
2. **forecast** -- node 1 rows with `is_forecast = true` (about 15 days);
3. **normals** -- the committed climatology for every remaining day through
   `planting_date + max_duration`, so WOFOST can always reach maturity.

Splice by calendar date, not by position, and fail loudly on a gap. The normals
leg shrinks as the season advances and the projection sharpens; before planting
the series is all normals and the anomaly is ~0 by construction. That is
expected and gets said in the README rather than tuned away.

`forecast_fraction` = (forecast days + normals days) / total driving days over
the simulated window, so a reader can see how much of the projection is not yet
observed.

### D4. Soil: a committed per-region table (answered by John)

**The finding that drove the question.** With PCSE's
`DummySoilDataProvider` (`SM0` 0.4, `SMFCF` 0.3, `SMW` 0.1, `RDMSOL` 120) and
`WAV=100`, the Iowa water-limited run came out 9,945 kg/ha against a potential
10,061 -- a 1.2% gap. `WAV=100` means 100 cm of initial available water, which
is far more than any real profile holds; the crop is effectively never short,
and the water-limited engine reduces to the potential one. Shipping that would
have produced a yield anomaly with almost no drought signal in it, which is the
main thing this node exists to detect.

**Decision.** Commit `soils.csv`, keyed on node 1's `region_key`, carrying
`SM0`, `SMFCF`, `SMW`, `RDMSOL`, `CRAIRC`, `K0`, `SOPE`, `KSUB`, `WAV`, plus
`texture_class`, `method` and `source` columns, one row per region. Each region
is assigned a documented dominant texture class for its corn area, and the
water-retention parameters come from that class rather than from one generic
soil; `WAV` is set to a realistic fraction of the profile's available water at
planting rather than 100 cm. The table is a modelling choice, stated as one in
the README and in the `method` column, not a soil survey.

Implementation must **verify the signal exists** before the bundle is
considered done: the check compares `WLP_FD` against `PP` per region and
asserts the water-limited yield is materially below potential in at least the
drier regions, so a future parameter slip that silently restores the 1% gap
fails the check rather than shipping.

**Future work, to be written up in the README** (John's instruction): deriving
the soil table from real USDA soil data -- gNATSGO/SSURGO -- area-weighted over
each region's corn acreage rather than assigned by texture class. A paragraph
covering what that would buy (genuinely regional drought response, defensible
`RDMSOL` from real rooting depth restrictions, and a `WAV` that reflects the
actual profile), and what it costs (a large gridded download, a committed build
script, and an acreage weighting that duplicates the NASS work node 1 already
did for its region points). It is its own brief, not a stretch goal here.

### D5. The yield anomaly, and why it sidesteps calibration

Two `Wofost72_WLP_FD` runs per region, identical in every parameter except the
driving weather:

- **projection** -- the spliced series of D3;
- **baseline** -- the committed climatology normals for the whole season.

```
yield_anomaly_pct = (TWSO_projection - TWSO_baseline) / TWSO_baseline * 100
```

Because both runs use the same uncalibrated crop parameters, the same soil and
the same planting date, the calibration error is common to numerator and
denominator and largely cancels. The percentage is the market-relevant number
and the one node 3 consumes.

Absolute yields ship too, for diagnostics and charting, and are labelled
uncalibrated everywhere they appear: `yield_projection_kg_ha`,
`yield_baseline_kg_ha`, and `yield_projection_bu_acre` via the standard corn
conversion **1 kg/ha = 0.0159 bu/acre** (1 bu of corn at 15.5% moisture =
25.4012 kg; 1 ha = 2.47105 acres; so bu/acre = kg/ha / 25.4012 / 2.47105). The
factor gets a comment naming both units, per repo convention.

### D6. Stress overlay: counts only in v1 (answered by John)

Base WOFOST models phenology and water-limited assimilation. It does **not**
model corn heat-sterility at silking -- a few days above ~35 degC during
pollination can cost yield in a way WOFOST's temperature response does not
capture.

**Decision.** v1 emits the stage-specific stress *counts* and no yield
adjustment. There is no `yield_anomaly_pct_adjusted` field. Every published
number stays pure WOFOST, and node 3 can be built against a stable schema.

The counts intersect node 1's per-day flags with DVS-derived windows from the
projection run. Window bounds are declared inputs with documented defaults:

| Diagnostic | Window | Default |
|---|---|---|
| `heat_stress_days_in_silking_window` | DVS around anthesis | `[0.90, 1.20]` |
| `frost_days_in_sensitive_window` | early establishment and grain fill to maturity | `[0.00, 0.15]` and `[1.70, 2.00]` |

Both are counted from the projection run's daily DVS, joined to node 1's
`heat_stress_day` / `frost_day` on `(region_key, date)`. Days supplied by
normals carry no flags and are counted as non-stress; the README says so,
because it means the count is a season-to-date-plus-forecast figure, not a
whole-season one.

**Future work, to be written up in the README** (John's instruction): how the
parameterised adjustment would be added later. The shape is a multiplicative
penalty applied after the WOFOST anomaly,
`yield_anomaly_pct_adjusted = yield_anomaly_pct - k * heat_stress_days_in_silking_window`,
with `k` a declared input in percentage points per heat day, defaulting to 0 so
the adjustment is off unless asked for. The README paragraph should name what a
real version needs: a citation for `k` (the field literature puts silking heat
losses in the low single-digit percent per stress day, but the value depends on
the temperature threshold and on whether the day was also dry), a threshold
input separate from node 1's 32 degC general heat flag, and the rule that the
pure-WOFOST anomaly is always emitted alongside so the overlay's contribution
is auditable by subtraction. Adding it is additive to the schema and does not
break node 3.

### D7. Climatology normals: Open-Meteo archive, 1995-2024 (answered by John)

`build_climatology.py`, committed and run once, fetches daily ERA5 from
**Open-Meteo's archive endpoint** -- the same
`archive-api.open-meteo.com/v1/archive` with `models=era5` that node 1 already
uses at run time -- for each region point over **1995-2024**, and writes
`climatology.csv`: one row per `(region_key, month, day)` with the mean of each
of node 1's six variables over the 30 years, in node 1's container units.

Why this source: it is keyless, it is the same endpoint, the same variable set
and the same units as node 1, so the normals drop straight into the same
provider code with no second conversion path to get wrong. The alternative,
Copernicus CDS, is what `thermofeel-bundles/thermal-indices` uses; it needs a
`~/.cdsapirc` key and is far slower over 30 years x 10 regions.

Why 1995-2024 rather than the WMO-standard 1991-2020: this table is not a
published climate normal, it is the reference weather for an internal baseline
run, and what it should represent is the weather a grower and a grain market
currently treat as unremarkable. A window ending in 2024 rather than 2020 is
closer to that. The deviation from the WMO convention is deliberate and gets
stated plainly in the README and in the table's `period` column, so nobody
mistakes these for standard normals. 29 February is handled by taking the
28 February value, documented in the `method` column.

Leap years, missing days and any region the archive cannot serve fail the build
script loudly; the committed CSV is the artifact, and the cache directory
(`.climatology-cache/`) is git-ignored.

### D8. Two JSON outputs, mirroring node 1

The brief allows one long file with a `row_type` discriminator or two files.
Two is cleaner here and matches node 1, and it makes node 3's binding
unambiguous under D1's required-key rule:

| Output | `required` | Content |
|---|---|---|
| `corn_yield_snapshot` | `metadata`, `columns`, `rows` | one row per region as of `date`: phenology, yields, anomaly, stress, provenance |
| `corn_yield_trajectory` | `generated_at`, `metadata`, `regions` | nested per region: the snapshot plus the daily DVS/stress series |

`corn_yield_snapshot` is the stdout redirect; `corn_yield_trajectory` is an
`{output:...}` arg. A `corn_yield_snapshot.csv` is written beside the
trajectory for off-platform use only, and the README says it is discarded
on-platform.

Snapshot columns:

```
region_key, state, date,
dvs, stage_name, days_to_anthesis, days_to_maturity, date_anthesis, date_maturity,
yield_projection_kg_ha, yield_baseline_kg_ha, yield_projection_bu_acre,
yield_anomaly_pct,
heat_stress_days_in_silking_window, frost_days_in_sensitive_window,
water_stress_indicator,
forecast_fraction, planting_date, variety_name, observed_through
```

`days_to_anthesis` / `days_to_maturity` are **signed**: negative once the stage
has passed. `water_stress_indicator` is the mean `RFTRA` over the emergence-to-
maturity window of the projection run (1.0 = unstressed), so it summarises the
season rather than one day. Trajectory rows are
`region_key, date, dvs, frost_day, heat_stress_day, is_forecast`.

### D9. Planting dates

`planting_dates.csv`, keyed on `region_key`, one representative planting date
per region (month/day, applied to the input's year) from USDA NASS
*Usual Planting and Harvesting Dates for U.S. Field Crops*, with the source and
the choice of the "most active" midpoint recorded in `method` and `source`
columns. The date is an override-able input so a user can test an early or late
planting.

### D10. Maturity class per region

`planting_dates.csv` also carries `variety_name` per region, drawn from the
`Grain_maize_201`..`_205` ladder of D2: shorter-season classes for the northern
regions (MN, SD, WI), longer for the southern and western ones (KS, MO, NE).
The assignment is a documented modelling choice mirroring the US relative-
maturity gradient, recorded in the table's `method` column, and it is what
makes the same heat event land at different growth stages in different states.

### D11. Region join, and failing loudly

The region set comes from the input. For every distinct `region_key` in the
input rows, the runner requires a row in `soils.csv` and `planting_dates.csv`
and a full year of rows in `climatology.csv`. A missing key exits non-zero with
a message naming the key and the table, per AC-8. Regions are never silently
dropped, and this node never invents a key. Keys are read from
`agromet-bundles/crop-weather/regions.csv`: `ia, il, mn, ne, in, sd, oh, wi,
ks, mo`.

### D12. Sample input

`sample_input.json` is a real node 1 `crop_weather_daily` output, subset to two
regions (`ia`, `ne` -- one wetter, one drier, so the check exercises a real
difference in water stress) across node 1's full window, with
`metadata.angstrom` and `metadata.regions` subset to match. It is generated
from a real node 1 run and committed, so the bundle runs standalone with no
network, per AC-3 and AC-4.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | Repo scaffold matching siblings | `.gitignore`, `.dockerignore`, `LICENSE`, `README.md`, `CLAUDE.md`, `.claude/skills/feat/`, `corn-yield/` (already committed at b408b0b) | `ls`; diff vendored feat against `agromet-bundles` | planned |
| AC-2 | Bundle files and tables present | `corn-yield/{Modelfile.toml,Dockerfile,runner.py,sample_input.json,soils.csv,planting_dates.csv,climatology.csv,build_climatology.py,check_yield.py,README.md}` | `ls corn-yield` | planned |
| AC-3 | Sample input runs end to end | D12; `runner.py` | `python corn-yield/runner.py corn-yield/sample_input.json` writes both outputs | planned |
| AC-4 | Docker build and run reproduce it | `Dockerfile` | `docker build` then `docker run --network none`; diff against the local run | planned |
| AC-5 | Water-limited run, plausible DVS to maturity | `build_provider`, `run_wofost` in `runner.py` | `check_yield.py`: DVS reaches 2.0, `DOA`/`DOM` present, `TWSO` in 5-25 t/ha | planned |
| AC-6 | Anomaly moves the right way on a hot, dry silking spell | `yield_anomaly_pct` (D5) | `check_yield.py` perturbs the sample input in the silking window and asserts the anomaly falls | planned |
| AC-7 | Stress windows intersect correctly; overlay separable | `stress_windows` in `runner.py` (D6) | `check_yield.py` hand-worked case; assert no `*_adjusted` field exists | planned |
| AC-8 | Tables keyed on node 1's key; unknown key fails loudly | table loaders (D11) | `check_yield.py` feeds an unknown `region_key` and asserts exit 1 with the key named | planned |
| AC-9 | Full region set from a node 1 output, no parameters | `required = []` on every optional field | run a full 10-region node 1 output with no other input | planned |
| AC-10 | Model Home import from the subfolder URL works | `Modelfile.toml` | manual: paste the branch subfolder URL at `/models/new/repo` and run | planned |
| AC-11 | README documents every modelling choice | `corn-yield/README.md` | review against the AC-11 list, incl. the D4 and D6 future-work paragraphs | planned |

## Verification

This repo has no test framework, matching its siblings; the convention is one
committed check script per bundle, run outside the image. There is no baseline
to record because there is no prior code.

| Command | Purpose | Baseline result | Final result |
|---|---|---|---|
| `python corn-yield/runner.py corn-yield/sample_input.json > run/corn_yield_snapshot.output.json` | the model runs standalone (AC-3) | n/a (new) | pending |
| `uv run --no-project --python 3.12 --with pcse==6.0.13 --with numpy python corn-yield/check_yield.py run/corn_yield_snapshot.output.json` | every modelling assertion (AC-5 to AC-8) | n/a (new) | pending |
| `cd corn-yield && docker build -t wofost-corn-yield:local .` | the image builds from the bundle context (AC-4) | n/a (new) | pending |
| `docker run --rm --network none wofost-corn-yield:local` | no network needed; output identical to local (AC-4) | n/a (new) | pending |
| `uv run python -m orchestration.modelfile validate <path>/corn-yield/Modelfile.toml` | Modelfile valid, no annotation warnings; run from the `modelhome` repo | n/a (new) | pending |

## Implementation steps

1. **Region tables.** Read `agromet-bundles/crop-weather/regions.csv` for the
   ten keys. Write `planting_dates.csv` (D9, D10) and `soils.csv` (D4) with
   `method` and `source` columns on every row.
2. **`build_climatology.py`** (D7). Open-Meteo archive, 1995-2024, node 1's six
   daily variables and node 1's exact conversions (IRRAD MJ->J, RAIN mm->cm,
   VAP by Magnus on mean dew point, WIND via the 0.71833 log profile with
   `wind_speed_unit=ms` passed explicitly -- node 1's `CLAUDE.md` documents the
   3.6x bug in PCSE's own provider). Cache to `.climatology-cache/`. Write
   `climatology.csv` with `period`, `source` and `method` columns.
3. **Run it once** and sanity-check the table: 366 rows per region, seasonal
   shape sane, values inside PCSE's range checks.
4. **`runner.py` -- input.** Parse node 1's `{metadata, columns, rows}`, group
   by `region_key`, sort by date, pull `metadata.angstrom` per region, resolve
   the input's optional fields against defaults, and join the three tables with
   the loud failure of D11.
5. **`runner.py` -- provider.** The `WeatherDataProvider` subclass of D2,
   including `reference_ET` and the `/10` conversion, mirroring
   `crop-weather/check_weather.py`.
6. **`runner.py` -- splice.** Build the projection series (D3) and the
   all-normals baseline series, both by calendar date, both failing loudly on a
   gap.
7. **`runner.py` -- WOFOST.** Two `Wofost72_WLP_FD` runs per region with the
   parameters of D2 and D4. Collect summary output and the daily frame.
8. **`runner.py` -- derived outputs.** DVS and stage name as of `date`, signed
   days to anthesis and maturity, the anomaly of D5, the stress-window counts
   of D6, mean `RFTRA`, `forecast_fraction`.
9. **`runner.py` -- output.** The two documents of D8 plus the CSV sidecar;
   stdout carries only the snapshot JSON, logs to stderr.
10. **`Modelfile.toml`.** One input with `required = ["metadata", "columns",
    "rows"]` (D1) plus the optional override fields with `required = []`; two
    outputs; every annotation field; `validity_domain` under 600 characters.
11. **`Dockerfile`.** `python:3.12-slim`, one `pip install --no-cache-dir` layer
    pinning `pcse==6.0.13` and its numeric stack, `COPY` of the runner and the
    three CSVs and the sample, `ENTRYPOINT`, `CMD ["sample_input.json"]`.
    `build_climatology.py` and `check_yield.py` are **not** copied in.
12. **`sample_input.json`** (D12), generated from a real node 1 run.
13. **`check_yield.py`.** All of AC-5 to AC-8, including the D4 signal check
    that water-limited is materially below potential.
14. **`corn-yield/README.md`** covering the full AC-11 list, including the D4
    (USDA soil data) and D6 (parameterised overlay) future-work paragraphs.
15. **Top-level `README.md`**: confirm the bundle table row reads correctly once
    the outputs are final.
16. **Run every verification command** and record results in the table above and
    in a "Verified results" section in `CLAUDE.md`, matching how
    `agromet-bundles` records them.
17. **Open the pull request.** Stop there.

## Files likely to change

```
corn-yield/Modelfile.toml        new
corn-yield/Dockerfile            new
corn-yield/runner.py             new
corn-yield/build_climatology.py  new (not in the image)
corn-yield/check_yield.py        new (not in the image)
corn-yield/planting_dates.csv    new
corn-yield/soils.csv             new
corn-yield/climatology.csv       new (built by the script)
corn-yield/sample_input.json     new
corn-yield/README.md             new
CLAUDE.md                        verified-results and task-list sections
README.md                        bundle table row, if the outputs change
docs/plans/0001-corn-yield.md    status, base commit, verification results
```

## Risks and follow-ups

- **The soil table is the load-bearing assumption.** D4 exists because the
  generic soil produced a 1.2% water-limited gap. A texture-class table is a
  real improvement but still a modelling choice, not a survey; the check in
  step 13 is what stops a regression back to no signal. USDA-soil derivation is
  the named follow-up.
- **Two WOFOST runs per region per run.** Twenty simulations for the default
  ten regions. Each Iowa run took well under a second locally, so this stays in
  the "seconds" runtime class, but `[resources]` should be set with headroom.
- **The baseline drifts with the climatology, not with the crop.** Genetics and
  management have raised US corn yields steadily; this anomaly deliberately
  measures weather only, against a fixed-weather baseline. Node 3 applies it to
  a USDA trend level, which is where the trend belongs. Worth stating in both
  READMEs so the division of labour is explicit.
- **Stress counts cover observed and forecast days only.** Normals days carry no
  stress flags, so a whole-season count is only complete once the season is. The
  field name and the README should not imply otherwise.
- **`max_duration = 200`** must be long enough for the longest maturity class in
  the coldest region. Step 7 should assert every region reaches `DOM` rather
  than silently truncating, and widen the bound if a northern region fails.
- **AC-10 needs a signed-in human.** The local stack is behind Auth0, exactly as
  node 1's AC-9 was; expect to hand that step to John.

## Deviations and conflicts found during run

### C1. BLOCKING -- the normals baseline of D5 + D7 is unworkable as specified

`run` stopped here rather than redesigning. The brief and plan both specify the
baseline as "WOFOST on climatology normals for the whole season". Implemented
exactly as written, it produces nonsense:

| Region | Projection kg/ha | Normals baseline kg/ha | Anomaly |
|---|---|---|---|
| ia | 10,053 | 6,936 | **+44.9%** |
| ne | 5,979 | 625 | **+855.9%** |

**Cause, verified.** A mean-by-calendar-day climatology preserves the seasonal
*total* rainfall but destroys its *structure*, and WOFOST's free-draining water
balance is driven by the structure:

| May-Sep, sample input | ia actual | ia normals | ne actual | ne normals |
|---|---|---|---|---|
| total rain | 60.8 cm | 53.2 cm | 47.3 cm | 43.1 cm |
| wet days (>0.1 cm) | 59 | 148 | 48 | 137 |
| dry days (exactly 0) | 63 | 0 | 68 | 0 |
| max daily | 6.30 cm | 0.93 cm | 6.57 cm | 0.81 cm |

Averaging turns episodic rain into continuous drizzle: 148 wet days instead of
59, and no dry day at all. Light daily rain is largely lost to soil evaporation
before it reaches the root zone, so the baseline crop starves on almost the same
seasonal total. The baseline is therefore biased far too low, which inflates
every anomaly and inverts the sign of the signal the node exists to produce.

This defeats D5's calibration-cancelling argument too: the error is no longer
common to both runs, because only the baseline run is driven by fictitious
weather.

**Verified alternative.** A 30-year ensemble baseline -- WOFOST run once per
historical year on that year's *real* daily weather, with the median as the
baseline -- was tested on the same two regions:

| Region | Ensemble median | min | p10 | p90 | max | Anomaly vs median |
|---|---|---|---|---|---|---|
| ia | 8,225 | 81 | 3,090 | 11,889 | 13,413 | +22.2% |
| ne | 3,928 | 23 | 379 | 9,185 | 10,612 | +52.2% |

It can be **precomputed offline** into a committed `baseline_yields.csv`,
because it depends only on committed data (soil, planting date, variety,
historical weather) and not on the input. The runner would then still do one
WOFOST run per region, stay fast, and remain deterministic and offline. The
30-year daily archive stays in the git-ignored cache; only the resulting table
is committed.

**Second-order finding.** The ensemble also shows the yield distribution is
severely skewed (ia spans 81 to 13,413 kg/ha). A percentage anomaly against a
median is a noisy statistic on a distribution like that, and a **percentile
rank** ("this season sits at the 63rd percentile of the last 30 years") would be
more robust and arguably more useful to node 3. That changes node 3's input
contract, so it is a cross-node decision, not a local one.

**Consequence.** D5 and D7's baseline role need revising in the brief and plan
before this can be finished. Everything else in the plan held: see C2.

### C2. What is implemented and working

Unaffected by C1 and verified on the sample input: the node 1 input contract
(D1), the PCSE run configuration (D2), the observed + forecast + normals
splice for the **projection** (D3, whose use of normals is sound -- it only
fills the tail of a season whose profile is already charged by real weather),
the per-region soil table (D4), the stage-specific stress overlay (D6), the
region join and loud failure (D11), and the sample input (D12).

Sample run as of 2026-09-19: ia mature, anthesis 2026-07-05, maturity
2026-08-24, mean RFTRA 0.90, 0 heat-stress days in the silking window; ne
mature, anthesis 2026-07-03, maturity 2026-08-18, mean RFTRA 0.79, **5
heat-stress days in the silking window**. The lifecycle diagnostic works.

### C3. Minor deviations from the plan, already applied

- **Trajectory rows carry a `source` field** (`observed` / `forecast` /
  `normals`) beyond the columns D8 lists. Without it a reader cannot tell a
  normals day from an observed one, which matters for reading the stress
  counts.
- **`stage_for` replaces the plan's implied `stage_name` + `dvs_on` pair.** The
  WOFOST run terminates at maturity, so the daily frame stops there and a
  snapshot date past maturity found no row and reported "planted, not yet
  emerged". A date past maturity is now reported as DVS 2.0, `mature`.
- **`build_climatology.py` waits out Open-Meteo's per-minute rate limit.** A
  30-year daily request is large enough that consecutive regions trip it; the
  limit clears on a wall-clock minute, so a seconds-scale backoff cannot
  recover. Not a modelling change.
