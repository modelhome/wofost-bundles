#!/usr/bin/env python3
"""
Model Home runner: phenology-aware corn yield for US corn regions, from a
crop-weather (node 1) output.

Reads one JSON input file (positional arg, default ``sample_input.json``), which
is an ``agromet-bundles/crop-weather`` ``crop_weather_daily`` document:
``{metadata, columns, rows}``. Every other field is optional, so a flow or a
daily schedule can pass node 1's output and nothing else.

For each region in that input it:

1. builds a PCSE ``WeatherDataProvider`` from node 1's series, deriving the
   three evaporation terms node 1 deliberately leaves to this node;
2. splices the driving weather as observed + forecast + climatology normals, so
   a full season can be simulated mid-season;
3. runs WOFOST once in water-limited mode on that spliced series (the
   projection), and compares it with the committed normal-weather distribution
   in baseline_yields.csv -- the same model run over each of the last thirty
   years of real weather, precomputed because it never depends on the input;
4. reports development stage, days to anthesis and maturity, the projected and
   baseline yields, the weather-driven yield anomaly and percentile rank between
   them, and heat and frost days counted inside the lifecycle windows where they
   matter.

Outputs:

- stdout: the per-region snapshot table as JSON (the ``corn_yield_snapshot``
  output; the Modelfile redirects stdout to run/corn_yield_snapshot.output.json);
- the second positional arg (default run/corn_yield_trajectory.output.json):
  the same values plus each region's daily development trajectory;
- corn_yield_snapshot.csv beside the trajectory. Model Home keeps only JSON
  outputs, so that file exists only when the model is run off-platform.

This model makes no network calls at run time. It is a pure function of its
input and the committed tables (soils.csv, planting_dates.csv, climatology.csv,
baseline_yields.csv). PCSE's crop parameters would be downloaded on first use,
so the Dockerfile bakes them into the image at a pinned commit and
crop_data_provider() reads that local copy.

Units are PCSE's WeatherDataContainer convention, matching node 1: TMIN/TMAX
degC, IRRAD J/m2/day, VAP hPa, WIND m/s at 2 m, RAIN cm/day. Logs go to stderr;
stdout carries only the result.
"""
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# The first time PCSE is imported into a fresh home directory it builds a demo
# database and announces it on STDOUT ("Building PCSE demo database at: ... OK").
# This runner's contract is that stdout carries only the result JSON, and the
# platform parses stdout with json.loads, so that one line would break every
# first run in a fresh container. Import PCSE with stdout pointed at stderr;
# doing it here rather than in the Dockerfile keeps the guarantee wherever the
# runner is executed.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr
try:
    from pcse.base import ParameterProvider
    from pcse.base.weather import WeatherDataContainer, WeatherDataProvider
    from pcse.input import YAMLCropDataProvider, WOFOST72SiteDataProvider
    from pcse.models import Wofost72_WLP_FD
    from pcse.util import reference_ET
finally:
    sys.stdout = _REAL_STDOUT

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT_PATH = HERE / "sample_input.json"
DEFAULT_TRAJECTORY_PATH = Path("run") / "corn_yield_trajectory.output.json"
SOILS_PATH = HERE / "soils.csv"
PLANTING_PATH = HERE / "planting_dates.csv"
CLIMATOLOGY_PATH = HERE / "climatology.csv"
CLIMATOLOGY_META_PATH = HERE / "climatology.meta.json"
BASELINES_PATH = HERE / "baseline_yields.csv"
BASELINES_META_PATH = HERE / "baselines.meta.json"
# The WOFOST crop parameter repository, baked into the image by the Dockerfile
# at a pinned commit. See crop_data_provider() for why this is not left to
# PCSE's own download-and-cache behaviour.
CROP_PARAMETERS_DIR = HERE / "crop_parameters"

# --- PCSE contract -----------------------------------------------------------
# Node 1 emits exactly these, in pcse.base.WeatherDataContainer units.
PCSE_COLUMNS = ["TMIN", "TMAX", "IRRAD", "VAP", "WIND", "RAIN"]
# Angstrom fallbacks, matching node 1 and pcse.input.OpenMeteoWeatherDataProvider.
ANGSTROM_A_DEFAULT = 0.29
ANGSTROM_B_DEFAULT = 0.49
# Soil parameters pcse.soil.classic_waterbalance.WaterbalanceFD requires.
SOIL_PARAMETERS = ["SM0", "SMFCF", "SMW", "CRAIRC", "SOPE", "KSUB", "RDMSOL"]

CROP_NAME = "maize"
# Longest plausible season in days. WOFOST stops here if maturity is not
# reached, which the run treats as a failure rather than a short season.
DEFAULT_MAX_DURATION = 200

# --- agronomic defaults ------------------------------------------------------
# WOFOST's development stage: 0 at emergence, 1 at anthesis, 2 at maturity.
# Corn's heat sensitivity peaks around flowering; a killing frost matters most
# just after emergence and again while the grain is still filling.
DEFAULT_SILKING_WINDOW = (0.90, 1.20)
DEFAULT_FROST_WINDOWS = ((0.00, 0.15), (1.70, 2.00))

# Plain-language names for the development stage, by DVS.
STAGE_BANDS = (
    (0.45, "early vegetative growth"),
    (0.90, "late vegetative growth"),
    (1.20, "silking (flowering)"),
    (1.70, "grain filling"),
    (2.00, "ripening"),
)
STAGE_MATURE = "mature"
STAGE_NOT_PLANTED = "not planted"
STAGE_NOT_EMERGED = "planted, not yet emerged"

# kg/ha -> bu/acre for corn. One bushel of corn at 15.5% moisture is 25.4012 kg
# and one hectare is 2.47105 acres, so the divisor is 62.7735.
KG_HA_PER_BU_ACRE = 25.4012 * 2.47105

SNAPSHOT_COLUMNS = [
    "region_key", "state", "date",
    "dvs", "stage_name", "days_to_anthesis", "days_to_maturity",
    "date_anthesis", "date_maturity",
    "yield_projection_kg_ha", "yield_baseline_kg_ha", "yield_projection_bu_acre",
    "yield_anomaly_pct", "yield_percentile_rank",
    "heat_stress_days_in_silking_window", "frost_days_in_sensitive_window",
    "water_stress_indicator",
    "forecast_fraction", "planting_date", "variety_name", "observed_through",
]
TRAJECTORY_COLUMNS = [
    "region_key", "date", "dvs", "source", "frost_day", "heat_stress_day",
]


class RunError(RuntimeError):
    """A failure the operator should see, not a traceback."""


def log(message):
    print(message, file=sys.stderr, flush=True)


# --- committed tables --------------------------------------------------------

def read_csv_keyed(path, key="region_key"):
    if not path.exists():
        raise RunError(f"missing committed table: {path.name}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RunError(f"{path.name} is empty")
    return {row[key]: row for row in rows}


def load_climatology():
    """{region_key: {(month, day): {variable: value}}} from climatology.csv."""
    if not CLIMATOLOGY_PATH.exists():
        raise RunError("missing committed table: climatology.csv")
    normals = {}
    with CLIMATOLOGY_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = row["region_key"]
            day = (int(row["month"]), int(row["day"]))
            normals.setdefault(key, {})[day] = {
                name: float(row[name]) for name in PCSE_COLUMNS
            }
    if not normals:
        raise RunError("climatology.csv has no rows")
    return normals


def load_climatology_meta():
    if not CLIMATOLOGY_META_PATH.exists():
        raise RunError("missing committed table: climatology.meta.json")
    return json.loads(CLIMATOLOGY_META_PATH.read_text())


def load_baselines():
    """
    {region_key: [yield, ...]} -- the normal-weather yield distribution.

    Built once by build_baselines.py: one WOFOST run per historical year on that
    year's real daily weather, with the same soil, planting date, variety and
    engine this runner uses. Precomputed because it depends only on committed
    data, which keeps this model to one simulation per region.
    """
    if not BASELINES_PATH.exists():
        raise RunError("missing committed table: baseline_yields.csv")
    distribution = {}
    with BASELINES_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            distribution.setdefault(row["region_key"], []).append(float(row["yield_kg_ha"]))
    if not distribution:
        raise RunError("baseline_yields.csv has no rows")
    for key in distribution:
        distribution[key].sort()
    return distribution


def load_baselines_meta():
    if not BASELINES_META_PATH.exists():
        raise RunError("missing committed table: baselines.meta.json")
    return json.loads(BASELINES_META_PATH.read_text())


def check_crop_parameters_pin(baselines_meta):
    """
    The projection and the baseline must use the same crop model.

    The baseline distribution was computed with one exact commit of
    WOFOST_crop_parameters, recorded in baselines.meta.json. If the image was
    built from a different commit, the anomaly would be comparing two different
    crop models while the metadata claimed identical parameters -- a silent
    wrong answer, which is worse than a failed run. Returns the commit actually
    in use, or None when running outside the image.
    """
    expected = baselines_meta.get("crop_parameters_sha")
    sha_path = CROP_PARAMETERS_DIR / "COMMIT_SHA"
    if not sha_path.exists():
        # Off-platform: PCSE downloads the parameters and the commit is unknown,
        # so this cannot be checked. Said plainly in the output metadata rather
        # than claimed as verified.
        return None
    actual = sha_path.read_text().strip()
    if expected and actual != expected:
        raise RunError(
            f"crop parameters in this image are commit {actual[:12]}, but "
            f"baseline_yields.csv was built with {expected[:12]}. The yield anomaly "
            f"compares the projection with that baseline, so they must use the same "
            f"crop model. Rebuild the baseline with build_baselines.py against this "
            f"checkout, or build the image with "
            f"--build-arg WOFOST_CROP_PARAMETERS_SHA={expected}.")
    return actual


def median(sorted_values):
    count = len(sorted_values)
    middle = count // 2
    if count % 2:
        return sorted_values[middle]
    return (sorted_values[middle - 1] + sorted_values[middle]) / 2.0


def percentile_rank(sorted_values, value):
    """
    Where this season sits in the historical distribution, 0-100.

    The share of baseline years that yielded less than the projection. Reported
    alongside the percentage anomaly because the yield distribution is strongly
    skewed -- a rainfed season can fail almost completely -- which makes a
    percentage against the median a noisy statistic on its own.
    """
    below = sum(1 for entry in sorted_values if entry < value)
    return round(100.0 * below / len(sorted_values), 1)


def normals_for(normals, region_key, day):
    """
    The normal weather for one calendar day.

    29 February is absent from the table on purpose -- it falls in 8 of the 30
    baseline years, too thin to sit beside 30-year means -- so it reads the
    28 February row.
    """
    table = normals[region_key]
    month_day = (day.month, day.day)
    if month_day == (2, 29):
        month_day = (2, 28)
    values = table.get(month_day)
    if values is None:
        raise RunError(
            f"climatology.csv has no {month_day[0]:02d}-{month_day[1]:02d} row for "
            f"region '{region_key}'")
    return values


# --- input -------------------------------------------------------------------

def read_input(path):
    if not path.exists():
        raise RunError(f"input file not found: {path}")
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RunError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise RunError(f"{path} must contain a JSON object")
    return document


def coerce_optional(document, name, cast, default):
    """A missing, null or empty value falls back the same way, per convention."""
    value = document.get(name)
    if value is None or value == "" or value == []:
        return default
    try:
        return cast(value)
    except (TypeError, ValueError) as exc:
        raise RunError(f"input field '{name}' is not usable: {value!r} ({exc})") from exc


def parse_window(value, name):
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        raise RunError(f"input field '{name}' must be a two-number range, got {value!r}")
    low, high = float(value[0]), float(value[1])
    if not low < high:
        raise RunError(f"input field '{name}' must be increasing, got {value!r}")
    return (low, high)


def group_rows(document):
    """{region_key: [row, ...]} sorted by date, from node 1's long-format table."""
    rows = document.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RunError(
            "input has no 'rows'. This model takes a crop-weather "
            "'crop_weather_daily' output: {metadata, columns, rows}.")
    required = set(["region_key", "date", "is_forecast"] + PCSE_COLUMNS + ["LAT", "LON", "ELEV"])
    grouped = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RunError(f"input row {index} is not an object")
        missing = sorted(required - row.keys())
        if missing:
            raise RunError(
                f"input row {index} is missing {missing}. Expected a crop-weather "
                f"'crop_weather_daily' row.")
        grouped.setdefault(row["region_key"], []).append(row)
    # Node 1 emits rows in order, but sorting here means this node never depends
    # on that.
    for key in grouped:
        grouped[key].sort(key=lambda row: row["date"])
    return grouped


def angstrom_for(baselines_meta, region_key):
    """
    The region's Angstrom coefficients, from the committed baseline build.

    reference_ET uses these to estimate net longwave radiation, so they feed the
    water balance. The anomaly is only clean if the projection and the baseline
    differ in nothing but weather, so both use this one committed pair rather
    than node 1's per-run estimate, which is made from a partial season and
    moves between runs.
    """
    entry = (baselines_meta.get("regions") or {}).get(region_key) or {}
    return (
        float(entry.get("angstrom_a", ANGSTROM_A_DEFAULT)),
        float(entry.get("angstrom_b", ANGSTROM_B_DEFAULT)),
    )


# --- the driving weather -----------------------------------------------------

def build_series(region_rows, normals, region_key, first_day, last_day, use_normals_only):
    """
    The driving weather for one run, one entry per day from first_day to
    last_day inclusive.

    Each entry carries its provenance: 'observed', 'forecast' or 'normals'.
    The projection splices node 1's observed and forecast days and completes the
    season with normals; the baseline uses normals throughout. Days are matched
    by calendar date, never by position.
    """
    by_date = {} if use_normals_only else {row["date"]: row for row in region_rows}

    # Normals are for the tail of the season, past where node 1 reaches. A day
    # missing from the middle of node 1's own range is upstream data loss, not a
    # future day: filling it silently would change the yield and understate
    # forecast_fraction while hiding the loss. Fail on it instead.
    covered_from = covered_to = None
    if by_date:
        covered_from = date.fromisoformat(min(by_date))
        covered_to = date.fromisoformat(max(by_date))

    series = []
    day = first_day
    while day <= last_day:
        iso = day.isoformat()
        row = by_date.get(iso)
        if row is not None:
            values = {name: float(row[name]) for name in PCSE_COLUMNS}
            source = "forecast" if row["is_forecast"] else "observed"
        else:
            if covered_from is not None and covered_from <= day <= covered_to:
                raise RunError(
                    f"region '{region_key}': the weather series has no row for {iso}, "
                    f"which falls inside the range it does cover "
                    f"({covered_from} to {covered_to}). That is a gap in the upstream "
                    f"data, not a day still in the future; this model will not fill it "
                    f"with normals and pass it off as a forecast.")
            values = dict(normals_for(normals, region_key, day))
            source = "normals"
        series.append({"day": day, "source": source, **values})
        day += timedelta(days=1)
    if not series:
        raise RunError(f"region '{region_key}': empty driving series")
    return series


def build_provider(series, latitude, longitude, elevation, angstrom_a, angstrom_b, label):
    """
    Node 1's series as a real PCSE weather provider.

    PCSE offers no public "from a table" constructor, so this subclasses
    WeatherDataProvider and stores one container per day, exactly as
    crop-weather/check_weather.py demonstrates. E0, ES0 and ET0 are derived
    here: they are in WeatherDataContainer.required and WOFOST reads them
    directly, and node 1 deliberately leaves them to this node because they are
    PCSE's own physics.
    """

    class SplicedWeatherProvider(WeatherDataProvider):
        def __init__(self):
            super().__init__()
            self.latitude = latitude
            self.longitude = longitude
            self.elevation = elevation
            self.angstA = angstrom_a
            self.angstB = angstrom_b
            self.description = [f"crop-weather + climatology normals ({label})"]
            for entry in series:
                day = entry["day"]
                # reference_ET returns mm/day; the container wants cm/day.
                e0, es0, et0 = reference_ET(
                    day, latitude, elevation,
                    entry["TMIN"], entry["TMAX"], entry["IRRAD"],
                    entry["VAP"], entry["WIND"],
                    angstrom_a, angstrom_b, "PM")
                container = WeatherDataContainer(
                    DAY=day, LAT=latitude, LON=longitude, ELEV=elevation,
                    TMIN=entry["TMIN"], TMAX=entry["TMAX"], IRRAD=entry["IRRAD"],
                    VAP=entry["VAP"], WIND=entry["WIND"], RAIN=entry["RAIN"],
                    E0=e0 / 10.0, ES0=es0 / 10.0, ET0=et0 / 10.0)
                self._store_WeatherDataContainer(container, day)

    return SplicedWeatherProvider()


# --- the model run -----------------------------------------------------------

def run_wofost(provider, soil_row, planting_day, variety_name, max_duration, region_key, label):
    """One water-limited WOFOST run. Returns (summary, daily output)."""
    try:
        soil = {name: float(soil_row[name]) for name in SOIL_PARAMETERS}
    except (KeyError, ValueError) as exc:
        raise RunError(f"soils.csv row for '{region_key}' is unusable: {exc}") from exc
    try:
        wav = float(soil_row["WAV"])
    except (KeyError, ValueError) as exc:
        raise RunError(f"soils.csv row for '{region_key}' has no usable WAV: {exc}") from exc

    agromanagement = [{
        planting_day: {
            "CropCalendar": {
                "crop_name": CROP_NAME,
                "variety_name": variety_name,
                "crop_start_date": planting_day,
                "crop_start_type": "sowing",
                "crop_end_date": planting_day + timedelta(days=max_duration),
                "crop_end_type": "maturity",
                "max_duration": max_duration,
            },
            "TimedEvents": None,
            "StateEvents": None,
        }
    }]
    parameters = ParameterProvider(
        cropdata=crop_data_provider(variety_name, region_key),
        soildata=soil,
        sitedata=WOFOST72SiteDataProvider(WAV=wav))
    try:
        model = Wofost72_WLP_FD(parameters, provider, agromanagement)
        model.run_till_terminate()
    except Exception as exc:  # PCSE raises a variety of its own error types
        raise RunError(f"region '{region_key}': the {label} WOFOST run failed: {exc}") from exc

    summary = model.get_summary_output()
    if not summary:
        raise RunError(f"region '{region_key}': the {label} WOFOST run produced no summary")
    return summary[0], model.get_output()


_CROP_DATA = None


def crop_data_provider(variety_name, region_key):
    """
    PCSE's own maize parameters.

    YAMLCropDataProvider does not carry its parameters inside the package: given
    no local path it downloads them from GitHub and caches the result for seven
    days. That would make this model quietly dependent on the network, and would
    start failing a week after an image was built. The Dockerfile bakes the
    parameter repository in at a pinned commit, and this reads that local copy.

    Off-platform, with no baked copy present, it falls back to PCSE's own
    behaviour so a developer checkout still runs.

    Loaded once: rebuilding it per region per run is pure overhead.
    """
    global _CROP_DATA
    if _CROP_DATA is None:
        if CROP_PARAMETERS_DIR.is_dir():
            _CROP_DATA = YAMLCropDataProvider(fpath=str(CROP_PARAMETERS_DIR))
        else:
            # Dev fallback only; see check_crop_parameters_pin().
            try:
                _CROP_DATA = YAMLCropDataProvider()
            except Exception as exc:
                raise RunError(
                    f"could not load the WOFOST crop parameters: {exc}. They are normally "
                    f"baked into the image at {CROP_PARAMETERS_DIR}; outside the image PCSE "
                    f"fetches them from GitHub, which needs network access once.") from exc
    try:
        _CROP_DATA.set_active_crop(CROP_NAME, variety_name)
    except Exception as exc:
        raise RunError(
            f"region '{region_key}': PCSE has no {CROP_NAME} variety "
            f"'{variety_name}' ({exc})") from exc
    return _CROP_DATA


# --- derived outputs ---------------------------------------------------------

def stage_for(daily, as_of, planting_day, maturity_day):
    """
    (DVS, stage name) as of a given day.

    The run terminates at maturity, so the daily frame simply stops there. A
    date past that end is a finished crop, not an un-emerged one; without this
    the snapshot would report "planted, not yet emerged" all autumn.
    """
    if as_of < planting_day:
        return None, STAGE_NOT_PLANTED
    if maturity_day is not None and as_of >= maturity_day:
        # DVS is 2.0 by definition at maturity and the crop stays there.
        return 2.0, STAGE_MATURE
    for entry in daily:
        if entry["day"] == as_of:
            dvs = entry.get("DVS")
            if dvs is None:
                return None, STAGE_NOT_EMERGED
            for limit, name in STAGE_BANDS:
                if dvs < limit:
                    return dvs, name
            return dvs, STAGE_MATURE
    return None, STAGE_NOT_EMERGED


def signed_days(target, as_of):
    """Days from as_of to target: negative once the stage has passed."""
    if target is None:
        return None
    return (target - as_of).days


def mean_water_stress(daily):
    """
    Mean RFTRA over the days the crop is actually growing.

    RFTRA is the transpiration reduction factor, 1.0 unstressed. Outside the
    crop's life it reads 0, so averaging the whole run would report severe
    stress for every region; restrict it to days with a development stage.
    """
    values = [
        entry["RFTRA"] for entry in daily
        if entry.get("DVS") is not None and entry.get("RFTRA") is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def in_any_window(dvs, windows):
    return any(low <= dvs <= high for low, high in windows)


def stress_in_windows(daily, flags_by_date, silking_window, frost_windows):
    """
    Node 1's stress-day flags, counted only inside the lifecycle windows where
    they matter.

    Only observed and forecast days carry flags; normals days contribute
    nothing, so these are season-to-date-plus-forecast counts, not whole-season
    ones.
    """
    heat = 0
    frost = 0
    for entry in daily:
        dvs = entry.get("DVS")
        if dvs is None:
            continue
        flags = flags_by_date.get(entry["day"].isoformat())
        if flags is None:
            continue
        if flags.get("heat_stress_day") and in_any_window(dvs, [silking_window]):
            heat += 1
        if flags.get("frost_day") and in_any_window(dvs, frost_windows):
            frost += 1
    return heat, frost


def trajectory_rows(region_key, daily, series_by_date, flags_by_date):
    rows = []
    for entry in daily:
        iso = entry["day"].isoformat()
        flags = flags_by_date.get(iso) or {}
        dvs = entry.get("DVS")
        rows.append({
            "region_key": region_key,
            "date": iso,
            "dvs": None if dvs is None else round(dvs, 4),
            "source": series_by_date.get(iso, "normals"),
            "frost_day": flags.get("frost_day"),
            "heat_stress_day": flags.get("heat_stress_day"),
        })
    return rows


# --- per-region orchestration ------------------------------------------------

def process_region(region_key, region_rows, tables, settings, document):
    soils, planting, normals = tables["soils"], tables["planting"], tables["normals"]
    # Fail loudly on a region node 1 defines and this node has no row for,
    # rather than silently dropping it.
    for name, table in (("soils.csv", soils), ("planting_dates.csv", planting)):
        if region_key not in table:
            raise RunError(
                f"region '{region_key}' appears in the input but has no row in {name}. "
                f"Node 1 owns the region set; add the row or remove the region.")
    if region_key not in normals:
        raise RunError(
            f"region '{region_key}' appears in the input but has no rows in "
            f"climatology.csv. Rebuild it with build_climatology.py.")
    if region_key not in tables["baselines"]:
        raise RunError(
            f"region '{region_key}' appears in the input but has no rows in "
            f"baseline_yields.csv. Rebuild it with build_baselines.py.")

    soil_row = soils[region_key]
    planting_row = planting[region_key]
    first_row = region_rows[0]
    latitude = float(first_row["LAT"])
    longitude = float(first_row["LON"])
    elevation = float(first_row["ELEV"])
    angstrom_a, angstrom_b = angstrom_for(tables["baselines_meta"], region_key)

    observed_days = [row["date"] for row in region_rows if not row["is_forecast"]]
    observed_through = max(observed_days) if observed_days else None
    as_of = date.fromisoformat(settings["as_of"])

    year = date.fromisoformat(region_rows[0]["date"]).year
    # The planting date and variety are not overridable: the committed baseline
    # distribution was built for these exact values, and letting a caller change
    # one would silently invalidate the anomaly it is compared against.
    planting_day = date(
        year, int(planting_row["planting_month"]), int(planting_row["planting_day"]))
    variety_name = planting_row["variety_name"]
    max_duration = settings["max_duration"]
    last_day = planting_day + timedelta(days=max_duration)

    # The projection: observed + forecast + normals for the remainder. The
    # normals only ever fill the tail of a season whose soil profile is already
    # charged by real weather, which is why using them here is sound and using
    # them for a whole baseline season was not -- see README.md.
    projection_series = build_series(
        region_rows, normals, region_key, planting_day, last_day, use_normals_only=False)

    projection_summary, projection_daily = run_wofost(
        build_provider(projection_series, latitude, longitude, elevation,
                       angstrom_a, angstrom_b, "projection"),
        soil_row, planting_day, variety_name, max_duration, region_key, "projection")

    if projection_summary.get("DOM") is None:
        raise RunError(
            f"region '{region_key}': the crop did not reach maturity within "
            f"{max_duration} days of {planting_day}. Raise max_duration or check the "
            f"variety and planting date.")

    projection_yield = projection_summary.get("TWSO")
    if projection_yield is None:
        raise RunError(f"region '{region_key}': WOFOST reported no grain yield")

    distribution = tables["baselines"][region_key]
    baseline_yield = median(distribution)
    if baseline_yield <= 0:
        raise RunError(
            f"region '{region_key}': the baseline median yield is {baseline_yield} kg/ha, so "
            f"a percentage anomaly against it would be meaningless. Rebuild "
            f"baseline_yields.csv and check the soil row.")
    anomaly_pct = round((projection_yield - baseline_yield) / baseline_yield * 100.0, 2)
    rank = percentile_rank(distribution, projection_yield)

    flags_by_date = {
        row["date"]: {
            "frost_day": bool(row.get("frost_day")),
            "heat_stress_day": bool(row.get("heat_stress_day")),
        }
        for row in region_rows
    }
    series_by_date = {entry["day"].isoformat(): entry["source"] for entry in projection_series}

    heat_days, frost_days = stress_in_windows(
        projection_daily, flags_by_date,
        settings["silking_window"], settings["frost_windows"])

    # How much of the simulated season was not yet observed.
    simulated = [entry for entry in projection_series if entry["day"] <= projection_summary["DOM"]]
    not_observed = sum(1 for entry in simulated if entry["source"] != "observed")
    forecast_fraction = round(not_observed / len(simulated), 4) if simulated else None

    dvs, stage = stage_for(
        projection_daily, as_of, planting_day, projection_summary.get("DOM"))
    snapshot = {
        "region_key": region_key,
        "state": first_row.get("state"),
        "date": as_of.isoformat(),
        "dvs": None if dvs is None else round(dvs, 4),
        "stage_name": stage,
        "days_to_anthesis": signed_days(projection_summary.get("DOA"), as_of),
        "days_to_maturity": signed_days(projection_summary.get("DOM"), as_of),
        "date_anthesis": (projection_summary.get("DOA").isoformat()
                          if projection_summary.get("DOA") else None),
        "date_maturity": projection_summary["DOM"].isoformat(),
        "yield_projection_kg_ha": round(projection_yield, 1),
        "yield_baseline_kg_ha": round(baseline_yield, 1),
        # kg/ha -> bu/acre. Illustrative only: these are uncalibrated WOFOST
        # yields, not a forecast of the USDA number. See README.md.
        "yield_projection_bu_acre": round(projection_yield / KG_HA_PER_BU_ACRE, 2),
        "yield_anomaly_pct": anomaly_pct,
        "yield_percentile_rank": rank,
        "heat_stress_days_in_silking_window": heat_days,
        "frost_days_in_sensitive_window": frost_days,
        "water_stress_indicator": mean_water_stress(projection_daily),
        "forecast_fraction": forecast_fraction,
        "planting_date": planting_day.isoformat(),
        "variety_name": variety_name,
        "observed_through": observed_through,
    }
    return snapshot, trajectory_rows(region_key, projection_daily, series_by_date, flags_by_date)


# --- output ------------------------------------------------------------------

def build_metadata(document, settings, climatology_meta, baselines_meta,
                   soils, planting, region_keys, crop_parameters_sha):
    node1_metadata = document.get("metadata") or {}
    return {
        "date": settings["as_of"],
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pcse_version": pcse_version(),
        "wofost_engine": "Wofost72_WLP_FD (water-limited, free-draining)",
        "crop": CROP_NAME,
        "crop_parameters": (
            "pcse.input.YAMLCropDataProvider reading github.com/ajwdewit/WOFOST_crop_parameters, "
            "baked into the image at a pinned commit rather than downloaded at run time. "
            "Uncalibrated for US conditions: absolute yields are illustrative, the anomaly "
            "is the signal."),
        # Stated rather than assumed: the projection and the baseline must use the
        # same crop model, and outside the image the commit cannot be determined.
        "crop_parameters_sha": crop_parameters_sha,
        "crop_parameters_pin_verified": crop_parameters_sha is not None and
        crop_parameters_sha == baselines_meta.get("crop_parameters_sha"),
        "yield_anomaly_definition": (
            "(projection - baseline) / baseline x 100, where the projection is this season's "
            "Wofost72_WLP_FD run and the baseline is the median of the same run repeated over "
            f"each year of {baselines_meta.get('period')} on that year's real daily weather, "
            "with identical soil, planting date, variety and engine. Using the same "
            "uncalibrated parameters throughout cancels most of the calibration error. "
            "yield_percentile_rank gives the projection's place in that distribution, which "
            "is the more robust statistic where the distribution is skewed."),
        "season_completion": (
            "observed (planting -> last observed day) + node 1 forecast + climatology "
            "normals to maturity. forecast_fraction reports the share that was not observed. "
            "Normals complete only the tail of a season whose soil profile is already charged "
            "by real weather; they are deliberately not used to drive a whole season."),
        "kg_ha_to_bu_acre_divisor": round(KG_HA_PER_BU_ACRE, 4),
        "silking_window_dvs": list(settings["silking_window"]),
        "frost_windows_dvs": [list(window) for window in settings["frost_windows"]],
        "stress_overlay": (
            "None. Stage-specific stress is reported as day counts only; no yield "
            "adjustment is applied, so every yield figure here is pure WOFOST. Base WOFOST "
            "does not model corn heat-sterility at silking -- see README.md."),
        "max_duration_days": settings["max_duration"],
        "determinism": (
            "Deterministic and offline: a pure function of the input document and the "
            "committed tables. No network calls, no randomness, no wall-clock dependence."),
        "climatology": climatology_meta,
        "baselines": baselines_meta,
        "soils": {
            key: {
                "texture_class": soils[key]["texture_class"],
                "RDMSOL": float(soils[key]["RDMSOL"]),
                "WAV": float(soils[key]["WAV"]),
                "method": soils[key]["method"],
                "source": soils[key]["source"],
            }
            for key in region_keys if key in soils
        },
        "planting": {
            key: {
                "planting_date": None,  # filled per region in the snapshot rows
                "variety_name": planting[key]["variety_name"],
                "method": planting[key]["method"],
                "source": planting[key]["source"],
            }
            for key in region_keys if key in planting
        },
        "upstream": {
            "model": "agromet-bundles/crop-weather",
            "date": node1_metadata.get("date"),
            "season_start": node1_metadata.get("season_start"),
            "retrieved_at": node1_metadata.get("retrieved_at"),
            "data_source": node1_metadata.get("data_source"),
            "pcse_convention": node1_metadata.get("pcse_convention"),
        },
    }


def pcse_version():
    try:
        import pcse
        return getattr(pcse, "__version__", "unknown")
    except Exception:  # pragma: no cover - pcse is a hard dependency
        return "unknown"


def write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT_PATH
    trajectory_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TRAJECTORY_PATH

    document = read_input(input_path)
    grouped = group_rows(document)
    soils = read_csv_keyed(SOILS_PATH)
    planting = read_csv_keyed(PLANTING_PATH)
    normals = load_climatology()
    climatology_meta = load_climatology_meta()
    baselines = load_baselines()
    baselines_meta = load_baselines_meta()
    crop_parameters_sha = check_crop_parameters_pin(baselines_meta)

    node1_metadata = document.get("metadata") or {}
    # Node 1's `date` is the last observed day, which is the snapshot's "as of".
    # Fall back to the newest observed row when a caller hands over rows alone.
    default_as_of = node1_metadata.get("date")
    if not default_as_of:
        observed = [row["date"] for rows in grouped.values() for row in rows
                    if not row["is_forecast"]]
        if not observed:
            raise RunError("input has no observed days and no metadata.date to report as of")
        default_as_of = max(observed)

    settings = {
        "as_of": coerce_optional(document, "date", str, default_as_of),
        "max_duration": coerce_optional(document, "max_duration", int, DEFAULT_MAX_DURATION),
        "silking_window": parse_window(
            document.get("silking_window") or list(DEFAULT_SILKING_WINDOW), "silking_window"),
        "frost_windows": tuple(
            parse_window(window, "frost_windows")
            for window in (document.get("frost_windows") or [list(w) for w in DEFAULT_FROST_WINDOWS])
        ),
    }

    region_keys = sorted(grouped)
    log(f"corn-yield: {len(region_keys)} region(s) as of {settings['as_of']}: "
        f"{', '.join(region_keys)}")

    snapshots = []
    trajectories = {}
    for region_key in region_keys:
        snapshot, rows = process_region(
            region_key, grouped[region_key],
            {"soils": soils, "planting": planting, "normals": normals,
             "baselines": baselines, "baselines_meta": baselines_meta},
            settings, document)
        snapshots.append(snapshot)
        trajectories[region_key] = rows
        log(f"  {region_key}: {snapshot['stage_name']} (DVS "
            f"{snapshot['dvs']}), yield {snapshot['yield_projection_kg_ha']} vs baseline "
            f"{snapshot['yield_baseline_kg_ha']} kg/ha, anomaly "
            f"{snapshot['yield_anomaly_pct']}% (percentile "
            f"{snapshot['yield_percentile_rank']})")

    metadata = build_metadata(
        document, settings, climatology_meta, baselines_meta, soils, planting,
        region_keys, crop_parameters_sha)

    trajectory_document = {
        "generated_at": metadata["generated_at"],
        "metadata": metadata,
        "regions": [
            {
                **snapshot,
                "days": [
                    {key: row[key] for key in TRAJECTORY_COLUMNS if key != "region_key"}
                    for row in trajectories[snapshot["region_key"]]
                ],
            }
            for snapshot in snapshots
        ],
    }

    trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    compact = {"separators": (",", ":")}
    trajectory_path.write_text(json.dumps(trajectory_document, **compact) + "\n")

    # Model Home keeps only JSON outputs; this CSV is for off-platform use.
    write_csv(trajectory_path.parent / "corn_yield_snapshot.csv", SNAPSHOT_COLUMNS, snapshots)

    print(json.dumps(
        {"metadata": metadata, "columns": SNAPSHOT_COLUMNS, "rows": snapshots}, **compact))


if __name__ == "__main__":
    try:
        main()
    except RunError as exc:
        log(f"error: {exc}")
        raise SystemExit(1)
