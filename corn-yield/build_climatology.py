#!/usr/bin/env python3
"""
One-time build of climatology.csv: the per-region daily weather normals the
corn-yield model uses to complete a season past the forecast horizon and to run
its normal-weather baseline.

For every region in agromet-bundles/crop-weather/regions.csv this script:

1. downloads daily ERA5 weather for the region's point from Open-Meteo's
   archive endpoint (the same endpoint, variables and units node 1 uses at run
   time) over the whole baseline period;
2. converts each day into PCSE's WeatherDataContainer convention with node 1's
   exact conversions, so the normals and the observed series are the same kind
   of number and need no second conversion path;
3. averages each variable over the period by calendar (month, day);
4. writes climatology.csv plus climatology.meta.json, which carries the
   provenance the runner copies into its output metadata.

The model never runs this. It reads the committed climatology.csv.

Why Open-Meteo rather than the Copernicus CDS that thermofeel-bundles uses: it
is keyless, and it is node 1's own source, so the normals are drop-in for the
same weather-provider code.

Why 1995-2024 rather than the WMO-standard 1991-2020: this table is not a
published climate normal, it is the reference weather for an internal baseline
run, and what it should represent is the weather a grower and a grain market
currently treat as unremarkable. The deviation is deliberate; see README.md.

29 February is deliberately absent from the table: it falls in only 8 of the 30
years, which is too thin an average to sit beside 30-year means. The runner
falls back to 28 February for it.

Run from the repo root:

    python corn-yield/build_climatology.py ../agromet-bundles/crop-weather/regions.csv

Downloads are cached in corn-yield/.climatology-cache/ (git-ignored), so an
interrupted build resumes where it stopped.
"""
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / ".climatology-cache"
CLIMATOLOGY_PATH = HERE / "climatology.csv"
META_PATH = HERE / "climatology.meta.json"

PERIOD_START = date(1995, 1, 1)
PERIOD_END = date(2024, 12, 31)
PERIOD = "1995-2024"

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
USER_AGENT = "modelhome-wofost-bundles/corn-yield-climatology"
RETRIES = 6
# A 30-year daily request is large enough that a few regions in a row trip
# Open-Meteo's per-minute limit. That limit clears on a wall-clock minute, so
# backing off in seconds is useless: wait out the minute instead.
RATE_LIMIT_WAIT_S = 65
# Courtesy pause between regions, to stay under the limit rather than recover
# from it.
REGION_PAUSE_S = 20

# Node 1's six daily aggregations, verbatim.
DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "shortwave_radiation_sum",
    "dew_point_2m_mean",
    "wind_speed_10m_mean",
]

PCSE_COLUMNS = ["TMIN", "TMAX", "IRRAD", "VAP", "WIND", "RAIN"]

# pcse.util.wind10to2: a log wind profile over a 0.033 m roughness length.
# 0.71833, not the 0.75 rule of thumb. Identical to node 1's constant.
WIND_10M_TO_2M = math.log10(2.0 / 0.033) / math.log10(10.0 / 0.033)

SOURCE = (
    "Open-Meteo archive (archive-api.open-meteo.com/v1/archive, models=era5), "
    "ERA5 reanalysis. Weather data by Open-Meteo.com, CC BY 4.0."
)
METHOD = (
    "Per-region mean of each variable by calendar (month, day) over "
    f"{PERIOD}, in pcse.base.WeatherDataContainer units, using node 1's "
    "conversions (IRRAD MJ/m2/day x 1e6; RAIN mm x 0.1; VAP Magnus on the daily "
    "mean dew point; WIND 10 m -> 2 m by a 0.033 m log profile, requested in "
    "m/s). 29 February is absent and falls back to 28 February."
)


class BuildError(RuntimeError):
    pass


def log(message):
    print(message, file=sys.stderr, flush=True)


def get_json(params):
    """GET with a few retries. Raises BuildError rather than returning junk."""
    url = f"{ARCHIVE_URL}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            # 4xx other than 429 will not get better by trying again.
            if exc.code != 429 and exc.code < 500:
                raise BuildError(f"{ARCHIVE_URL} returned HTTP {exc.code}: {body}") from exc
            last = f"HTTP {exc.code}: {body}"
            if exc.code == 429 and attempt < RETRIES - 1:
                log(f"  rate limited; waiting {RATE_LIMIT_WAIT_S}s for the minute to clear")
                time.sleep(RATE_LIMIT_WAIT_S)
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last = str(exc)
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)
    raise BuildError(f"{ARCHIVE_URL} failed after {RETRIES} attempts: {last}")


def vapour_pressure_hpa(dewpoint_c):
    """Magnus, as node 1 and pcse.input.OpenMeteoWeatherDataProvider do it."""
    return 6.108 * math.exp((17.27 * dewpoint_c) / (dewpoint_c + 237.3))


def to_pcse_row(values):
    """One Open-Meteo day -> PCSE WeatherDataContainer variables and units."""
    return {
        # degC -> degC
        "TMIN": values["temperature_2m_min"],
        "TMAX": values["temperature_2m_max"],
        # MJ/m2/day -> J/m2/day
        "IRRAD": values["shortwave_radiation_sum"] * 1e6,
        # degC dew point -> hPa
        "VAP": vapour_pressure_hpa(values["dew_point_2m_mean"]),
        # m/s at 10 m -> m/s at 2 m
        "WIND": values["wind_speed_10m_mean"] * WIND_10M_TO_2M,
        # mm/day -> cm/day
        "RAIN": values["precipitation_sum"] * 0.1,
    }


def fetch_region(region):
    """{iso date: {variable: value}} for the whole baseline period, cached."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = CACHE_DIR / f"{region['region_key']}-{PERIOD}.json"
    if cache_path.exists():
        log(f"  {region['region_key']}: cache hit")
        return json.loads(cache_path.read_text())

    payload = get_json({
        "latitude": f"{region['lat']:.4f}",
        "longitude": f"{region['lon']:.4f}",
        "start_date": PERIOD_START.isoformat(),
        "end_date": PERIOD_END.isoformat(),
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "UTC",
        "models": "era5",
        # Open-Meteo defaults wind to km/h. PCSE's own Open-Meteo provider omits
        # this and feeds km/h into its 10m-to-2m conversion as if it were m/s,
        # overstating WIND by about 3.6x. Always ask for m/s.
        "wind_speed_unit": "ms",
    })
    daily = payload.get("daily")
    if not daily or "time" not in daily:
        raise BuildError(f"{region['region_key']}: archive returned no daily block")
    absent = [name for name in DAILY_VARIABLES if name not in daily]
    if absent:
        raise BuildError(
            f"{region['region_key']}: archive returned no {', '.join(absent)}; the API's "
            f"daily variables may have changed. Expected all of: {', '.join(DAILY_VARIABLES)}.")

    out = {}
    for index, day in enumerate(daily["time"]):
        values = {name: daily[name][index] for name in DAILY_VARIABLES}
        # A day short of any variable is not data; drop it whole so every day
        # that reaches to_pcse_row carries all six.
        if any(value is None for value in values.values()):
            continue
        out[day] = values
    cache_path.write_text(json.dumps(out))
    log(f"  {region['region_key']}: {len(out)} days fetched")
    return out


def normals_for_region(region, observed):
    """Mean of each PCSE variable by (month, day). 29 February is omitted."""
    buckets = defaultdict(list)
    for iso, values in observed.items():
        day = date.fromisoformat(iso)
        if (day.month, day.day) == (2, 29):
            continue
        buckets[(day.month, day.day)].append(to_pcse_row(values))

    expected = 365
    if len(buckets) != expected:
        missing = sorted(
            (m, d) for m, d in (
                ((date(2001, 1, 1) + timedelta(days=n)).month,
                 (date(2001, 1, 1) + timedelta(days=n)).day)
                for n in range(365))
            if (m, d) not in buckets)
        raise BuildError(
            f"{region['region_key']}: {len(buckets)} calendar days, expected {expected}; "
            f"missing {missing[:5]}")

    rows = []
    for (month, day) in sorted(buckets):
        sample = buckets[(month, day)]
        # A thin bucket means the archive was short for that day across the
        # period; averaging it beside 30-year means would be misleading.
        if len(sample) < 25:
            raise BuildError(
                f"{region['region_key']}: only {len(sample)} years for {month:02d}-{day:02d}")
        row = {"region_key": region["region_key"], "month": month, "day": day}
        for name in PCSE_COLUMNS:
            mean = sum(entry[name] for entry in sample) / len(sample)
            # IRRAD is ~1e7; the rest are small. Match node 1's precision.
            row[name] = round(mean, 1) if name == "IRRAD" else round(mean, 4)
        row["n_years"] = len(sample)
        rows.append(row)
    return rows


def read_regions(path):
    with open(path, newline="", encoding="utf-8") as handle:
        regions = [
            {
                "region_key": row["region_key"],
                "state": row["state"],
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "elev_m": float(row["elev_m"]),
            }
            for row in csv.DictReader(handle)
        ]
    if not regions:
        raise BuildError(f"{path} defines no regions")
    return regions


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: build_climatology.py <path to crop-weather/regions.csv>\n"
            "Node 1 owns the region points; this script never invents them.")
    regions = read_regions(sys.argv[1])
    log(f"building {PERIOD} normals for {len(regions)} regions from {ARCHIVE_URL}")

    rows = []
    for index, region in enumerate(regions):
        observed = fetch_region(region)
        rows.extend(normals_for_region(region, observed))
        if index < len(regions) - 1 and not (CACHE_DIR / f"{regions[index + 1]['region_key']}-{PERIOD}.json").exists():
            time.sleep(REGION_PAUSE_S)

    columns = ["region_key", "month", "day"] + PCSE_COLUMNS + ["n_years"]
    with open(CLIMATOLOGY_PATH, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    META_PATH.write_text(json.dumps({
        "period": PERIOD,
        "period_start": PERIOD_START.isoformat(),
        "period_end": PERIOD_END.isoformat(),
        "source": SOURCE,
        "endpoint": ARCHIVE_URL,
        "method": METHOD,
        "pcse_convention": (
            "pcse.base.WeatherDataContainer: TMIN/TMAX degC, IRRAD J/m2/day, VAP hPa, "
            "WIND m/s at 2 m, RAIN cm/day. NOT PCSE's CSV file convention."),
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "regions": [
            {"region_key": r["region_key"], "state": r["state"],
             "lat": r["lat"], "lon": r["lon"], "elev_m": r["elev_m"]}
            for r in regions
        ],
    }, indent=2) + "\n")

    log(f"wrote {CLIMATOLOGY_PATH} ({len(rows)} rows) and {META_PATH}")


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        log(f"error: {exc}")
        raise SystemExit(1)
