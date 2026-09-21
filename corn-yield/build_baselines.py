#!/usr/bin/env python3
"""
One-time build of baseline_yields.csv: the normal-weather yield distribution
each season's projection is measured against.

For every region this script runs WOFOST once per year of the baseline period,
on that year's *real* daily weather, with the same soil, planting date, variety
and engine the runner uses for its projection. The resulting 30 yields are the
region's normal-weather distribution: the runner takes their median as the
baseline and the projection's rank within them as its percentile.

Why not the climatology normals. Driving the baseline with a mean-by-calendar-
day climatology was the original design and it does not work. Averaging
preserves the season's rainfall total but destroys its structure -- for Iowa,
148 wet days instead of 59 and no dry day at all -- and WOFOST's free-draining
water balance is driven by the structure, not the total. Light daily rain is
largely lost to soil evaporation before it reaches the root zone, so the
baseline crop starves and every anomaly is inflated. Real historical years keep
the structure. The climatology is still used, but only to complete the tail of
a season whose profile is already charged by real weather, which is sound.

Why precomputed. The baseline depends only on committed data -- soil, planting
date, variety, and historical weather -- and never on the model's input, so it
can be computed once here. The runner then does one WOFOST run per region
instead of thirty-one, and stays fast, offline and deterministic.

This also fixes the Angstrom coefficients per region. reference_ET uses them to
estimate net longwave radiation, so they affect the water balance; the anomaly
is only clean if the projection and the baseline differ in nothing but weather.
A single estimate from 30 years is both more stable than one from a partial
season and shared by both runs, so it is committed here and used for both.

For the same reason this build takes the crop parameters from an explicit local
checkout rather than letting PCSE download them. `YAMLCropDataProvider()` with
no path fetches whatever the upstream `wofost72` branch happens to be at the
time, so a baseline built that way would drift out of step with the pinned
checkout baked into the image, and the anomaly would silently compare two
different crop models. The checkout's commit is recorded in baselines.meta.json
and the runner refuses to run against a different one.

The model never runs this. It reads the committed baseline_yields.csv.

Run from the repo root, after build_climatology.py has populated the cache:

    git clone --no-checkout https://github.com/ajwdewit/WOFOST_crop_parameters.git \\
        corn-yield/.crop-parameters-cache
    git -C corn-yield/.crop-parameters-cache checkout <the Dockerfile's SHA>

    uv run --no-project --python 3.12 --with pcse==6.0.13 \\
        python corn-yield/build_baselines.py ../agromet-bundles/crop-weather/regions.csv \\
            corn-yield/.crop-parameters-cache
"""
import csv
import json
import math
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pcse.base import ParameterProvider
from pcse.base.weather import WeatherDataContainer, WeatherDataProvider
from pcse.input import YAMLCropDataProvider, WOFOST72SiteDataProvider
from pcse.models import Wofost72_WLP_FD
from pcse.util import reference_ET

import build_climatology as clim
# The agromanagement builder is imported from the runner rather than copied.
# The baseline distribution and the run-time projection must differ in nothing
# but weather; two copies of this logic could drift apart and the anomaly would
# quietly start comparing two different models. runner.py is in the image and
# this script is not, so the dependency runs the safe direction.
import runner as node2

HERE = Path(__file__).resolve().parent
BASELINES_PATH = HERE / "baseline_yields.csv"
META_PATH = HERE / "baselines.meta.json"

SOIL_PARAMETERS = ["SM0", "SMFCF", "SMW", "CRAIRC", "SOPE", "KSUB", "RDMSOL"]
CROP_NAME = "maize"
MAX_DURATION = 200

# Angstrom estimation, identical to node 1's (agromet-bundles/crop-weather
# runner.py, angstrom_ab) so both nodes derive the coefficients the same way.
SOLAR_CONSTANT_W_M2 = 1361.0
ANGSTROM_A_DEFAULT = 0.29
ANGSTROM_B_DEFAULT = 0.49
ANGSTROM_MIN_DAYS = 200

ENGINE = "Wofost72_WLP_FD (water-limited, free-draining)"


def percentile(sorted_values, q):
    """numpy.percentile's default linear interpolation, without numpy."""
    if not sorted_values:
        raise clim.BuildError("percentile of an empty series")
    position = (q / 100.0) * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[int(position)]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def toa_radiation_mj(day, latitude):
    """FAO-56 top-of-atmosphere daily radiation, MJ/m2/day. Mirrors PCSE."""
    doy = day.timetuple().tm_yday
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    declination = math.radians(23.45 * math.sin(2 * math.pi * (doy - 81) / 365))
    phi = math.radians(latitude)
    cos_hs = max(-1.0, min(1.0, -math.tan(phi) * math.tan(declination)))
    hs = math.acos(cos_hs)
    h0 = (24 * 3600 / math.pi) * SOLAR_CONSTANT_W_M2 * dr * (
        math.cos(phi) * math.cos(declination) * math.sin(hs)
        + hs * math.sin(phi) * math.sin(declination))
    return h0 / 1e6


def angstrom_ab(observed, latitude):
    """A is the 5th percentile and A+B the 98th of measured over top-of-atmosphere."""
    ratios = []
    for iso, values in observed.items():
        day = date.fromisoformat(iso)
        toa = toa_radiation_mj(day, latitude)
        if toa > 0:
            ratios.append((values["shortwave_radiation_sum"] * 1e6 / 1e6) / toa)
    if len(ratios) < ANGSTROM_MIN_DAYS:
        return ANGSTROM_A_DEFAULT, ANGSTROM_B_DEFAULT, "default (fewer than 200 usable days)"
    ratios.sort()
    a = percentile(ratios, 5)
    b = percentile(ratios, 98) - a
    # pcse.util.check_angstromAB's bounds.
    if not (0.1 <= a <= 0.4 and 0.3 <= a + b <= 0.9 and b > 0):
        return ANGSTROM_A_DEFAULT, ANGSTROM_B_DEFAULT, "default (estimate out of range)"
    return round(a, 4), round(b, 4), f"estimated from the {clim.PERIOD} radiation series"


def build_provider(series, site, angstrom_a, angstrom_b):
    class HistoricalProvider(WeatherDataProvider):
        def __init__(self):
            super().__init__()
            self.latitude = site["lat"]
            self.longitude = site["lon"]
            self.elevation = site["elev_m"]
            self.angstA, self.angstB = angstrom_a, angstrom_b
            self.description = [f"{clim.PERIOD} ERA5 via Open-Meteo archive"]
            for day, row in series:
                # reference_ET returns mm/day; the container wants cm/day.
                e0, es0, et0 = reference_ET(
                    day, site["lat"], site["elev_m"], row["TMIN"], row["TMAX"],
                    row["IRRAD"], row["VAP"], row["WIND"], angstrom_a, angstrom_b, "PM")
                self._store_WeatherDataContainer(WeatherDataContainer(
                    DAY=day, LAT=site["lat"], LON=site["lon"], ELEV=site["elev_m"],
                    TMIN=row["TMIN"], TMAX=row["TMAX"], IRRAD=row["IRRAD"],
                    VAP=row["VAP"], WIND=row["WIND"], RAIN=row["RAIN"],
                    E0=e0 / 10.0, ES0=es0 / 10.0, ET0=et0 / 10.0), day)
    return HistoricalProvider()


def run_year(observed, site, soil_row, planting_row, regime_row, year,
             angstrom_a, angstrom_b, crop_data):
    """One historical season. Returns TWSO kg/ha, or None when the year is short."""
    sowing = date(year, int(planting_row["planting_month"]), int(planting_row["planting_day"]))
    series = []
    for offset in range(MAX_DURATION + 1):
        day = sowing + timedelta(days=offset)
        values = observed.get(day.isoformat())
        if values is None:
            return None
        series.append((day, clim.to_pcse_row(values)))

    variety = planting_row["variety_name"]
    crop_data.set_active_crop(CROP_NAME, variety)
    soil = {name: float(soil_row[name]) for name in SOIL_PARAMETERS}
    agromanagement = node2.agromanagement_for(
        sowing, variety, MAX_DURATION, soil, regime_row, soil_row["region_key"])
    parameters = ParameterProvider(
        cropdata=crop_data,
        soildata=soil,
        sitedata=WOFOST72SiteDataProvider(WAV=float(soil_row["WAV"])))
    model = Wofost72_WLP_FD(
        parameters, build_provider(series, site, angstrom_a, angstrom_b), agromanagement)
    model.run_till_terminate()
    summary = model.get_summary_output()
    if not summary:
        return None
    return summary[0].get("TWSO")


def crop_parameters_commit(path):
    """The exact commit of the crop-parameter checkout this build used."""
    try:
        sha = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise clim.BuildError(
            f"{path} is not a git checkout, so the crop-parameter commit cannot be "
            f"recorded: {exc}. The baseline must be pinned to the same commit the "
            f"image bakes in, or the anomaly compares two different crop models.") from exc
    if not sha:
        raise clim.BuildError(f"{path}: empty commit id")
    return sha


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: build_baselines.py <path to crop-weather/regions.csv> "
            "<path to a WOFOST_crop_parameters checkout>\n"
            "Node 1 owns the region points; this script never invents them.\n"
            "The crop parameters must come from the same pinned commit the Dockerfile "
            "bakes into the image, not from PCSE's own download.")
    regions = clim.read_regions(sys.argv[1])
    crop_parameters_path = Path(sys.argv[2]).resolve()
    if not crop_parameters_path.is_dir():
        raise clim.BuildError(f"crop-parameter checkout not found: {crop_parameters_path}")
    crop_sha = crop_parameters_commit(crop_parameters_path)
    clim.log(f"crop parameters: {crop_parameters_path} at {crop_sha}")
    soils = {row["region_key"]: row for row in
             csv.DictReader(open(HERE / "soils.csv", newline="", encoding="utf-8"))}
    planting = {row["region_key"]: row for row in
                csv.DictReader(open(HERE / "planting_dates.csv", newline="", encoding="utf-8"))}
    regimes = {row["region_key"]: row for row in
               csv.DictReader(open(HERE / "water_regime.csv", newline="", encoding="utf-8"))}

    crop_data = YAMLCropDataProvider(fpath=str(crop_parameters_path))
    rows = []
    summary_by_region = {}
    for region in regions:
        key = region["region_key"]
        for name, table in (("soils.csv", soils), ("planting_dates.csv", planting),
                            ("water_regime.csv", regimes)):
            if key not in table:
                raise clim.BuildError(f"region '{key}' has no row in {name}")
        observed = clim.fetch_region(region)
        angstrom_a, angstrom_b, angstrom_source = angstrom_ab(observed, region["lat"])

        yields = []
        for year in range(clim.PERIOD_START.year, clim.PERIOD_END.year + 1):
            value = run_year(observed, region, soils[key], planting[key], regimes[key],
                             year, angstrom_a, angstrom_b, crop_data)
            if value is None:
                clim.log(f"  {key} {year}: incomplete season, skipped")
                continue
            yields.append((year, round(value, 1)))
            rows.append({"region_key": key, "year": year, "yield_kg_ha": round(value, 1)})

        if len(yields) < 20:
            raise clim.BuildError(
                f"region '{key}': only {len(yields)} usable years; a baseline distribution "
                f"needs most of the period")
        values = sorted(value for _, value in yields)
        summary_by_region[key] = {
            "state": region["state"],
            "n_years": len(values),
            "median_kg_ha": round(statistics.median(values), 1),
            "mean_kg_ha": round(statistics.mean(values), 1),
            "p10_kg_ha": round(percentile(values, 10), 1),
            "p90_kg_ha": round(percentile(values, 90), 1),
            "min_kg_ha": values[0],
            "max_kg_ha": values[-1],
            "planting_date": (f"{int(planting[key]['planting_month']):02d}-"
                              f"{int(planting[key]['planting_day']):02d}"),
            "variety_name": planting[key]["variety_name"],
            # Recorded per region so the runner can state, and check, that a
            # region's baseline was built under the same regime its projection uses.
            "regime": regimes[key]["regime"],
            "angstrom_a": angstrom_a,
            "angstrom_b": angstrom_b,
            "angstrom_source": angstrom_source,
        }
        clim.log(f"  {key}: median {summary_by_region[key]['median_kg_ha']} kg/ha "
                 f"over {len(values)} years (p10 {summary_by_region[key]['p10_kg_ha']}, "
                 f"p90 {summary_by_region[key]['p90_kg_ha']})")

    with BASELINES_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["region_key", "year", "yield_kg_ha"])
        writer.writeheader()
        writer.writerows(rows)

    META_PATH.write_text(json.dumps({
        "period": clim.PERIOD,
        "engine": ENGINE,
        "crop_parameters_repository": "github.com/ajwdewit/WOFOST_crop_parameters",
        # The runner refuses to run against a different commit: the projection and
        # the baseline must use the same crop model, not just the same weather.
        "crop_parameters_sha": crop_sha,
        "crop": CROP_NAME,
        "max_duration_days": MAX_DURATION,
        "source": clim.SOURCE,
        "method": (
            f"One Wofost72_WLP_FD run per year of {clim.PERIOD} on that year's real daily "
            "ERA5 weather, with the same soil, planting date, variety and engine the runner "
            "uses for its projection. Real years are used rather than a mean climatology "
            "because averaging destroys the structure of rainfall, which is what the water "
            "balance responds to. The baseline is the median of these yields; the percentile "
            "rank is the share of them below the projection."),
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "regions": summary_by_region,
    }, indent=2) + "\n")

    clim.log(f"wrote {BASELINES_PATH} ({len(rows)} rows) and {META_PATH}")


if __name__ == "__main__":
    try:
        main()
    except clim.BuildError as exc:
        clim.log(f"error: {exc}")
        raise SystemExit(1)
