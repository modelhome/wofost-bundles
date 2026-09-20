# US Corn Yield (WOFOST)

What this season's weather is doing to the US corn crop, state by state.

This model takes the daily weather series from
[`agromet-bundles/crop-weather`](https://github.com/modelhome/agromet-bundles)
and runs [PCSE](https://github.com/ajwdewit/pcse)/WOFOST -- the crop growth
model behind the EU MARS operational yield-forecasting system -- once per
region. It reports how far each state's corn has developed, when it flowers and
ripens, the yield the season is heading for, how that compares with the same
model run over the last thirty years of real weather, and how many hot or
frosty days landed during flowering, when corn is most vulnerable.

It is node 2 of a three-node flow: weather -> **crop** -> price. Node 1 supplies
the weather; node 3 (`ag-commodity-bundles/corn-price/`) turns this model's
yield anomaly into a price impact.

```
corn-yield/
  Modelfile.toml          one input (node 1's table), two JSON outputs
  Dockerfile              python:3.12-slim, pinned pcse
  runner.py               the model
  soils.csv               per-region water-holding parameters
  planting_dates.csv      per-region planting date and maturity class
  climatology.csv         per-region daily weather normals, 1995-2024
  climatology.meta.json   provenance for the above
  baseline_yields.csv     30 normal-weather yields per region
  baselines.meta.json     provenance for the above
  build_climatology.py    one-time normals build (not in the image)
  build_baselines.py      one-time baseline build (not in the image)
  check_yield.py          validation, incl. real WOFOST runs (not in the image)
  sample_input.json       a real node 1 output: Iowa and Nebraska, 2026
  README.md
```

## Running it

```bash
cd corn-yield
docker build -t wofost-corn-yield:local .
docker run --rm --network none wofost-corn-yield:local   # uses sample_input.json
```

Locally, without Docker:

```bash
uv run --no-project --python 3.12 --with pcse==6.0.13 \
    python corn-yield/runner.py corn-yield/sample_input.json \
    run/corn_yield_trajectory.output.json > run/corn_yield_snapshot.output.json
```

On Model Home, paste
`https://github.com/modelhome/wofost-bundles/tree/main/corn-yield` into the
"classic import" option, then wire the US Corn Crop Weather model's
`crop_weather_daily` output into this model's input.

## Input

The whole input is node 1's `crop_weather_daily` document --
`{metadata, columns, rows}` -- and in a flow nothing else needs filling in. The
regions to model are read from it, so **this model never defines its own region
set**; it joins its tables on node 1's `region_key` and fails loudly on a key it
has no row for.

Flow steps bind by required-key set, so this model's input declares
`["metadata", "columns", "rows"]`, which matches `crop_weather_daily` and not
`crop_weather_summary` (whose required keys are `generated_at` / `metadata` /
`regions`).

Optional fields, all with working defaults: `date` (the day to report as of,
defaulting to the upstream run's own date), `planting_date`, `variety_name`,
`max_duration`, `silking_window` and `frost_windows`. See `Modelfile.toml` for
each one.

> Overriding `planting_date` or `variety_name` means the committed thirty-year
> baseline no longer describes the configuration being run, so the anomaly and
> percentile are reported empty and `baseline_note` says why. The projection and
> the phenology are still produced.

## Outputs

Two JSON documents. Model Home keeps only JSON, so the `corn_yield_snapshot.csv`
written beside them exists for off-platform use only and is discarded
on-platform.

- **`corn_yield_snapshot`** -- one row per region: development stage and days to
  flowering and ripeness, projected and baseline yields, the anomaly and
  percentile rank, the stage-specific stress counts, and provenance. This is
  what node 3 reads.
- **`corn_yield_trajectory`** -- the same values plus each region's day-by-day
  development, with each day marked `observed`, `forecast` or `normals` and
  carrying node 1's stress flags, for charting a season.

## How it works

### The crop model

| Piece | Choice |
|---|---|
| Version | PCSE **6.0.13** |
| Engine | `Wofost72_WLP_FD` -- water-limited, free-draining |
| Crop | `maize`, from `YAMLCropDataProvider`, reading the parameter repository baked into the image |
| Variety | per region from the `Grain_maize_201`..`_205` maturity ladder |
| Site | `WOFOST72SiteDataProvider(WAV=...)` from `soils.csv` |
| Soil | `soils.csv` (see below) |
| Agromanagement | sown on the region's planting date, run to maturity, `max_duration` 200 days |

Water-limited rather than potential mode because drought is a primary driver of
US corn yields, and it is what makes node 1's `RAIN` matter.

**`E0`, `ES0` and `ET0` are this node's job.** They are in
`WeatherDataContainer.required` and WOFOST reads them directly, but they are
PCSE's own physics, so node 1 deliberately leaves them out. This model derives
them with `pcse.util.reference_ET(..., ETMODEL="PM")`, which returns mm/day, and
divides by 10 for the container's cm/day.

PCSE offers no public "build a provider from a table" constructor, so
`runner.py` subclasses `WeatherDataProvider` and stores one
`WeatherDataContainer` per day. Units are node 1's throughout -- TMIN/TMAX degC,
IRRAD J/m2/day, VAP hPa, WIND m/s at 2 m, RAIN **cm**/day -- so nothing is
renamed and nothing is converted twice.

### Completing the season

A mid-season run still produces a full-season projection, by splicing the
driving weather:

**observed** (planting -> last observed day) + **node 1's forecast** (about 15
days) + **climatology normals** (the remainder, to maturity).

`forecast_fraction` reports the share that was not observed. It falls towards 0
as the season progresses and the projection firms up. Before planting the series
is all normals and the output is near-baseline and quiet; that is expected, not
a defect. The signal is most meaningful from about April to October.

### The yield anomaly, and why it avoids calibration

Uncalibrated WOFOST does not reproduce USDA bushel levels, so comparing its
absolute yield to a USDA trend would be apples to oranges. The anomaly is
defined **internally** instead:

```
yield_anomaly_pct = (projection - baseline) / baseline * 100
```

where the **baseline** is the median of the same model run once per year over
**1995-2024**, on each year's real daily weather, with identical soil, planting
date, variety, engine and Angstrom coefficients. Because every parameter is
shared, the calibration error is common to both sides and largely cancels. The
percentage is the market-relevant number, and it is what node 3 applies to real
USDA levels.

`yield_percentile_rank` ships alongside: the share of those thirty years that
yielded less than this season's projection. Where the distribution is strongly
skewed -- and a rainfed corn distribution is, because a bad year can fail almost
completely -- the percentile is the more robust statistic. Use the percentage
for magnitude and the percentile for context.

> **Why the baseline is real years and not the normals table.** Driving the
> baseline with a mean-by-calendar-day climatology was the original design and it
> does not work. Averaging preserves a season's rainfall *total* but destroys its
> *structure*: for Iowa, the normals give 148 wet days instead of 59, no dry day
> at all, and a maximum daily fall of 0.93 cm instead of 6.30 cm. WOFOST's
> free-draining water balance responds to structure, not totals -- light daily
> rain is largely lost to soil evaporation before it reaches the root zone -- so
> the baseline crop starved and every anomaly was inflated (Iowa +45%, Nebraska
> +856%). Real historical years keep the structure. The normals are still used,
> but only to complete the tail of a season whose soil profile is already charged
> by real weather, where the same objection does not apply.

The baseline is **precomputed** by `build_baselines.py`, because it depends only
on committed data and never on the input. That keeps the model to one simulation
per region at run time.

Absolute yields ship too, for diagnostics and charting:
`yield_projection_kg_ha`, `yield_baseline_kg_ha`, and
`yield_projection_bu_acre` via

```
bu/acre = kg/ha / (25.4012 * 2.47105) = kg/ha / 62.7735
```

(one bushel of corn at 15.5% moisture is 25.4012 kg; one hectare is 2.47105
acres). **These are uncalibrated model yields, not forecasts of the USDA
number.** Do not read `yield_projection_bu_acre` as a prediction.

### Stage-specific stress

The point of running a phenology model is that the same weather matters
differently at different stages. WOFOST tracks development on a unitless scale
-- 0 at emergence, 1 at flowering, 2 at ripeness -- so this model intersects
node 1's stress-day flags with the stages where they do damage:

| Output | Window (DVS) | Why |
|---|---|---|
| `heat_stress_days_in_silking_window` | 0.90 - 1.20 | corn pollination is most heat-sensitive around flowering |
| `frost_days_in_sensitive_window` | 0.00 - 0.15 and 1.70 - 2.00 | a killing frost matters just after emergence and again while grain is still filling |

Both windows are inputs, so the defaults can be changed without editing code.
Only observed and forecast days carry flags, so these are
**season-to-date-plus-forecast** counts, not whole-season ones: days filled in
with normals contribute nothing.

`water_stress_indicator` is the mean `RFTRA` (transpiration reduction factor,
1.0 = never short of water) over the days the crop was actually growing. It
reads 0 outside the crop's life, so averaging the whole run would report severe
stress everywhere; the average is restricted to days with a development stage.

**No yield adjustment is applied.** Base WOFOST does not model corn
heat-sterility at silking, and this model does not bolt one on: every yield
figure here is pure WOFOST, and the stage-specific stress is reported as day
counts only. See "Adding a silking-heat adjustment" below for how one would be
added.

## The committed tables

All four are keyed on node 1's `region_key`, read from
`agromet-bundles/crop-weather/regions.csv`: `ia, il, mn, ne, in, sd, oh, wi, ks,
mo`.

### `soils.csv`

Water-retention parameters per region, from the dominant texture class of the
state's corn area, plus `RDMSOL` (maximum rooting depth) and `WAV` (available
water in the profile at planting, cm). `WAV` is
`fraction x (SMFCF - SMW) x RDMSOL`, with the fraction 0.65 in the humid Corn
Belt, where the profile is near field capacity after spring recharge, and 0.50
in the drier west.

**This is a modelling choice, not a soil survey**, and it is load-bearing. PCSE's
generic `DummySoilDataProvider` with `WAV=100` -- 100 cm of available water,
more than any real profile holds -- leaves the crop effectively never short, so
the water-limited engine degenerates into the potential one: on this sample it
produced a 1.2% gap, and the yield anomaly stopped measuring drought at all.
`check_yield.py` asserts the water balance is live for every region, so a
regression to that state fails the check rather than shipping.

### `planting_dates.csv`

One representative planting date per region, the midpoint of the state's most
active corn planting period, and the maize maturity class to use. Source: USDA
NASS, *Usual Planting and Harvesting Dates for U.S. Field Crops* (Agricultural
Handbook 628). The dates are transcribed to the nearest day and are
representative of the state, not of any field; re-check them against the current
edition before relying on them.

Maturity classes run north to south down PCSE's `Grain_maize_201`..`_205`
ladder (TSUM1/TSUM2 = 695/800, 695/860, 775/880, 855/900, 935/920), which is how
the US relative-maturity gradient is expressed: shorter-season classes in
Minnesota and South Dakota, longer in Missouri and Kansas. It is what makes the
same heat event land at a different growth stage in different states.

### `climatology.csv` and `baseline_yields.csv`

Both built once from **Open-Meteo's ERA5 archive over 1995-2024** -- the same
endpoint, variables and units node 1 uses at run time, so the normals are
drop-in for the same provider code and no API key is needed.

`climatology.csv` is the mean of each variable by calendar (month, day), used
only to complete a season past the forecast horizon. 29 February is deliberately
absent -- it falls in 8 of the 30 years, too thin to sit beside 30-year means --
and the runner falls back to 28 February.

`baseline_yields.csv` is 30 WOFOST yields per region, one per year, on real
daily weather. `baselines.meta.json` also carries the per-region Angstrom
coefficients, estimated once from the 30-year radiation series and used for
**both** the baseline and the projection, so the two runs differ in nothing but
weather.

> **On the baseline period.** 1995-2024 is deliberately *not* the WMO-standard
> 1991-2020. This table is not a published climate normal; it is the reference
> weather for an internal baseline run, and what it should represent is the
> weather a grower and a grain market currently treat as unremarkable. A window
> ending in 2024 is closer to that. Do not cite these as standard normals.

Rebuild either with:

```bash
python corn-yield/build_climatology.py ../agromet-bundles/crop-weather/regions.csv
uv run --no-project --python 3.12 --with pcse==6.0.13 \
    python corn-yield/build_baselines.py ../agromet-bundles/crop-weather/regions.csv
```

## Determinism

**Deterministic and offline at run time.** Unlike node 1, this model makes no
network calls when it runs: it is a pure function of its input document and the
committed tables, and `docker run --network none` works.

One thing had to be arranged for that to be true. PCSE's `YAMLCropDataProvider`
does **not** carry its parameters inside the package: given no local path it
downloads them from GitHub on first use and caches the result **for seven days**.
A container relying on that would fail with no network, and would start failing
a week after it was built even with one. The Dockerfile therefore clones the
parameter repository into the image at a **pinned commit**
(`WOFOST_CROP_PARAMETERS_SHA`) and the runner reads that local copy, so two
builds of the same Dockerfile produce the same parameters and the same yields.
Pass a different SHA as a build argument to move it.

PCSE also prints a one-off `Building PCSE demo database ...` line to **stdout**
the first time it is imported into a fresh home directory. Since the platform
parses this model's stdout as JSON, that line would break every first run in a
fresh container, so `runner.py` imports PCSE with stdout pointed at stderr.

It inherits node 1's forecast caveat for the days that were forecast rather than
observed -- those values can be revised by a later ERA5 pass -- and uses fixed
normals beyond them. `forecast_fraction` reports how much of the simulated
season that was. The whole projection is recomputed on every run, so it always
reflects current data and current code.

## Limitations

- **Rainfed everywhere.** Every region is simulated as dryland corn. Much of the
  corn in **Kansas and Nebraska is irrigated**, so those regions' weather
  response is overstated and their absolute yields read far below reality
  (Kansas's baseline median is about 1,500 kg/ha, roughly 24 bu/acre, which is a
  dryland number in a state whose actual average is several times that). The
  anomaly is still a reasonable weather signal; the level is not. An irrigation
  share per region is the obvious fix and is not implemented.
- **One point per state.** These are state-scale representative points, not
  fields. Node 1 chooses them by production weighting; a state with a real
  north-south split is flattened into one series.
- **Uncalibrated.** The crop parameters are the standard European WOFOST maize
  sets, not US-calibrated. Absolute yields are illustrative.
- **Base WOFOST models no heat sterility at silking.** The stage-specific heat
  count reports exposure; the yield figures do not respond to it beyond WOFOST's
  own temperature response.
- **Stress counts are not whole-season** until the season is over, because
  normals days carry no flags.
- **The soil table is assigned by texture class**, not derived from survey data.
- **Planting dates are transcribed**, not machine-read from the current USDA
  handbook edition.

## Future work

### Deriving `soils.csv` from USDA soil data

The soil table is the single most load-bearing modelling choice here: it decides
how hard water limitation bites, and therefore how much of the yield anomaly is
real signal. Today each region gets the water-retention parameters of one
dominant texture class and a rooting depth from typical profile depth, which is
defensible and documented but coarse -- it cannot distinguish a deep Iowa loess
profile from a shallower, stonier one a hundred miles away, and it gives every
region in the same texture class an identical drought response.

Deriving it from **gNATSGO/SSURGO**, the USDA's own soil survey, would fix that.
The build script would pull the gridded soil database, take the map units
intersecting each region's corn area, and area-weight their component properties
into `SM0`, `SMFCF`, `SMW` and `RDMSOL` -- with `RDMSOL` coming from real
root-restricting layer depths rather than a typical-profile assumption, which is
where the largest errors probably sit today. `WAV` could then be a genuine
fraction of a real profile's available water instead of a flat 0.65 or 0.50.

The cost is real: a large gridded download, a committed build script with a
cache, and an acreage weighting that duplicates the NASS work node 1 already did
to place its region points -- which argues for doing it once, upstream, and
sharing the weights. It is its own brief, not a stretch goal.

### Adding a silking-heat adjustment

Base WOFOST captures phenology and water-limited assimilation but not corn's
heat-sterility at silking: a few days above roughly 35 degC during pollination
can cost yield through failed kernel set in a way WOFOST's temperature response
does not represent. This model therefore reports the exposure
(`heat_stress_days_in_silking_window`) and stops there.

An adjustment would be additive to the schema and would not break node 3. The
shape is a penalty applied *after* the WOFOST anomaly:

```
yield_anomaly_pct_adjusted = yield_anomaly_pct - k * heat_stress_days_in_silking_window
```

with `k` a declared input in percentage points per stress day, **defaulting to 0
so the adjustment is off unless asked for**. Doing it properly needs three
things this bundle does not have yet:

1. **A citation for `k`.** The field literature puts silking heat losses in the
   low single-digit percent per stress day, but the value depends on the
   temperature threshold used and on whether the day was also dry -- heat and
   drought at silking interact, and attributing the loss twice is the obvious
   trap.
2. **Its own temperature threshold**, separate from node 1's general 32 degC
   heat flag, since kernel-set failure is usually pinned nearer 35 degC.
3. **The pure-WOFOST anomaly emitted alongside**, always, so the overlay's
   contribution is auditable by subtraction.

Until those exist, an adjustment would be a number with no provenance dressed as
physiology, which is worse than not having one.

### Smaller items

- Crop-reporting-district granularity instead of one point per state.
- An irrigation share per region, to stop Kansas and Nebraska being modelled as
  fully dryland.
- Soybean and wheat bundles, which would reuse the same machinery with a
  different crop and parameter set.

## Validation

`check_yield.py` runs outside the image and proves the modelling claims rather
than the plumbing: a real water-limited run reaching maturity, the water balance
actually binding per region, the anomaly falling when a hot dry spell is
injected into the silking window, the stress windows intersecting a hand-worked
case exactly, and an unknown `region_key` failing loudly.

```bash
uv run --no-project --python 3.12 --with pcse==6.0.13 \
    python corn-yield/check_yield.py run/corn_yield_snapshot.output.json
```

## Licence and attribution

MIT, as the repo. The crop model is [PCSE](https://github.com/ajwdewit/pcse) and
its maize parameters come from
[`ajwdewit/WOFOST_crop_parameters`](https://github.com/ajwdewit/WOFOST_crop_parameters).
Weather, climatology and baseline weather are ERA5 via
[Open-Meteo](https://open-meteo.com/) (CC BY 4.0). Planting dates are from USDA
NASS Agricultural Handbook 628.
