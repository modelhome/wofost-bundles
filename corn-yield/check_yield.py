#!/usr/bin/env python3
"""
Validation for the corn-yield bundle. Not part of the model image.

The bundle's claims are modelling claims, so the checks are too:

- WOFOST really runs in water-limited mode and reaches maturity (AC-5);
- the water-limited yield stays materially below potential, which is what the
  per-region soil table exists to ensure -- a regression here would silently
  restore the 1% gap that made the anomaly meaningless (AC-5);
- injecting a hot, dry spell into the silking window moves the anomaly down and
  the silking heat count up (AC-6);
- the stage-specific stress windows intersect the flags exactly as a hand-worked
  case says they should (AC-7);
- an unknown region_key fails loudly rather than being dropped (AC-8);
- no yield figure carries an undocumented overlay (AC-7).

Run from the repo root, after a model run:

    uv run --no-project --python 3.12 --with pcse==6.0.13 \\
        python corn-yield/check_yield.py run/corn_yield_snapshot.output.json
"""
import json
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import runner  # noqa: E402

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


def close(a, b, tolerance):
    return a is not None and b is not None and abs(a - b) <= tolerance


# --- AC-5: a real water-limited run that reaches maturity --------------------

def check_wofost_run(output):
    print("WOFOST run (AC-5)")
    rows = output["rows"]
    check("every region produced a snapshot row", bool(rows), str(len(rows)))
    for row in rows:
        key = row["region_key"]
        check(f"{key}: the crop reached maturity", row["date_maturity"] is not None)
        check(f"{key}: the crop flowered", row["date_anthesis"] is not None)
        if row["date_anthesis"] and row["date_maturity"]:
            anthesis = date.fromisoformat(row["date_anthesis"])
            maturity = date.fromisoformat(row["date_maturity"])
            planting = date.fromisoformat(row["planting_date"])
            check(f"{key}: planting < flowering < ripeness",
                  planting < anthesis < maturity,
                  f"{planting} {anthesis} {maturity}")
            check(f"{key}: the season is a plausible length (90-200 d)",
                  90 <= (maturity - planting).days <= 200,
                  str((maturity - planting).days))
        check(f"{key}: projected yield is credible for corn (0.5-25 t/ha)",
              500 <= row["yield_projection_kg_ha"] <= 25000,
              str(row["yield_projection_kg_ha"]))
        check(f"{key}: the water-stress indicator is a fraction",
              row["water_stress_indicator"] is None
              or 0.0 <= row["water_stress_indicator"] <= 1.0,
              str(row["water_stress_indicator"]))
    check("the engine is the water-limited one",
          "WLP_FD" in output["metadata"]["wofost_engine"],
          output["metadata"]["wofost_engine"])


def check_water_limitation(sample_path):
    """
    The decisive soil check.

    PCSE's generic DummySoilDataProvider with WAV=100 makes the water-limited
    engine behave like the potential one: on this sample it left a 1.2% gap,
    which is why soils.csv exists. If a future edit restores that, the yield
    anomaly stops measuring drought and this check is what catches it.
    """
    print("water limitation is real, not nominal (AC-5)")
    from pcse.base import ParameterProvider
    from pcse.input import YAMLCropDataProvider, WOFOST72SiteDataProvider, DummySoilDataProvider
    from pcse.models import Wofost72_PP, Wofost72_WLP_FD

    document = json.loads(Path(sample_path).read_text())
    grouped = runner.group_rows(document)
    soils = runner.read_csv_keyed(runner.SOILS_PATH)
    planting = runner.read_csv_keyed(runner.PLANTING_PATH)
    normals = runner.load_climatology()
    baselines_meta = runner.load_baselines_meta()
    gaps = {}

    for key, rows in sorted(grouped.items()):
        soil_row, planting_row = soils[key], planting[key]
        year = date.fromisoformat(rows[0]["date"]).year
        planting_day = date(year, int(planting_row["planting_month"]),
                            int(planting_row["planting_day"]))
        last_day = planting_day + timedelta(days=runner.DEFAULT_MAX_DURATION)
        series = runner.build_series(rows, normals, key, planting_day, last_day, False)
        a, b = runner.angstrom_for(baselines_meta, key)
        provider = runner.build_provider(
            series, float(rows[0]["LAT"]), float(rows[0]["LON"]), float(rows[0]["ELEV"]),
            a, b, "check")

        # A quarter of the season's rain, to prove the water balance is live for
        # this region's soil row. A region can legitimately show no water
        # limitation in a wet year -- Iowa 2026 does -- so demanding drought
        # everywhere would be testing the weather, not the model. Drying the
        # weather out tests the mechanism per region regardless of the season.
        dry_series = [dict(entry, RAIN=entry["RAIN"] * 0.25) for entry in series]
        dry_provider = runner.build_provider(
            dry_series, float(rows[0]["LAT"]), float(rows[0]["LON"]),
            float(rows[0]["ELEV"]), a, b, "check-dry")

        yields = {}
        for label, model_class, soil, wav, weather in (
                ("potential", Wofost72_PP, DummySoilDataProvider(), 100.0, provider),
                ("water-limited", Wofost72_WLP_FD,
                 {n: float(soil_row[n]) for n in runner.SOIL_PARAMETERS},
                 float(soil_row["WAV"]), provider),
                ("dry", Wofost72_WLP_FD,
                 {n: float(soil_row[n]) for n in runner.SOIL_PARAMETERS},
                 float(soil_row["WAV"]), dry_provider)):
            crop = YAMLCropDataProvider()
            crop.set_active_crop(runner.CROP_NAME, planting_row["variety_name"])
            agro = [{planting_day: {"CropCalendar": {
                "crop_name": runner.CROP_NAME,
                "variety_name": planting_row["variety_name"],
                "crop_start_date": planting_day, "crop_start_type": "sowing",
                "crop_end_date": last_day, "crop_end_type": "maturity",
                "max_duration": runner.DEFAULT_MAX_DURATION},
                "TimedEvents": None, "StateEvents": None}}]
            model = model_class(
                ParameterProvider(cropdata=crop, soildata=soil,
                                  sitedata=WOFOST72SiteDataProvider(WAV=wav)),
                weather, agro)
            model.run_till_terminate()
            yields[label] = model.get_summary_output()[0]["TWSO"]

        gap = (yields["potential"] - yields["water-limited"]) / yields["potential"] * 100
        dry_gap = (yields["potential"] - yields["dry"]) / yields["potential"] * 100
        gaps[key] = gap
        print(f"        {key}: potential {yields['potential']:.0f}, water-limited "
              f"{yields['water-limited']:.0f} kg/ha (gap {gap:.1f}%), "
              f"on a quarter of the rain {yields['dry']:.0f} kg/ha (gap {dry_gap:.1f}%)")
        check(f"{key}: the water balance is live -- drying the season out costs yield",
              dry_gap > 10.0,
              f"gap {dry_gap:.1f}% on a quarter of the rain -- check soils.csv WAV and RDMSOL")

    # And at least one region must show real water limitation on the actual
    # weather, or the sample is not exercising the thing this model is for.
    check("water limitation binds somewhere in the sample (gap > 2%)",
          any(value > 2.0 for value in gaps.values()),
          ", ".join(f"{k} {v:.1f}%" for k, v in sorted(gaps.items())))


# --- AC-6: the anomaly responds to a hot, dry silking spell ------------------

def perturb_silking(document, snapshot_rows):
    """
    A hot, dry fortnight over each region's flowering date.

    Built from the model's own projected anthesis date, so the spell really does
    land in the silking window rather than near it.
    """
    perturbed = json.loads(json.dumps(document))
    anthesis = {row["region_key"]: date.fromisoformat(row["date_anthesis"])
                for row in snapshot_rows if row["date_anthesis"]}
    touched = 0
    for row in perturbed["rows"]:
        centre = anthesis.get(row["region_key"])
        if centre is None:
            continue
        day = date.fromisoformat(row["date"])
        if abs((day - centre).days) <= 7:
            row["TMAX"] = max(row["TMAX"], 38.0)
            row["TMIN"] = max(row["TMIN"], 24.0)
            row["RAIN"] = 0.0
            row["heat_stress_day"] = True
            touched += 1
    return perturbed, touched


def run_model(input_path, trajectory_path):
    """Run runner.py exactly as the Modelfile does, and parse stdout."""
    result = subprocess.run(
        [sys.executable, str(HERE / "runner.py"), str(input_path), str(trajectory_path)],
        capture_output=True, text=True)
    return result


def check_anomaly_responds(sample_path, output):
    print("the anomaly responds to weather in the silking window (AC-6)")
    document = json.loads(Path(sample_path).read_text())
    perturbed, touched = perturb_silking(document, output["rows"])
    check("the perturbation touched some days", touched > 0, str(touched))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        perturbed_path = tmp / "perturbed.json"
        perturbed_path.write_text(json.dumps(perturbed))
        result = run_model(perturbed_path, tmp / "trajectory.json")
        check("the perturbed run succeeded", result.returncode == 0, result.stderr[-300:])
        if result.returncode != 0:
            return
        hot = json.loads(result.stdout)

    before = {row["region_key"]: row for row in output["rows"]}
    for row in hot["rows"]:
        key = row["region_key"]
        base = before[key]
        print(f"        {key}: anomaly {base['yield_anomaly_pct']}% -> "
              f"{row['yield_anomaly_pct']}%, silking heat days "
              f"{base['heat_stress_days_in_silking_window']} -> "
              f"{row['heat_stress_days_in_silking_window']}")
        check(f"{key}: the yield anomaly falls",
              row["yield_anomaly_pct"] < base["yield_anomaly_pct"],
              f"{base['yield_anomaly_pct']} -> {row['yield_anomaly_pct']}")
        check(f"{key}: the silking heat-day count rises",
              row["heat_stress_days_in_silking_window"]
              > base["heat_stress_days_in_silking_window"],
              f"{base['heat_stress_days_in_silking_window']} -> "
              f"{row['heat_stress_days_in_silking_window']}")
        check(f"{key}: the percentile rank falls",
              row["yield_percentile_rank"] <= base["yield_percentile_rank"],
              f"{base['yield_percentile_rank']} -> {row['yield_percentile_rank']}")


# --- AC-7: the stress windows, hand-worked -----------------------------------

def check_stress_windows():
    print("stage-specific stress windows (AC-7)")
    silking = (0.90, 1.20)
    frost_windows = ((0.00, 0.15), (1.70, 2.00))

    # A hand-built five-day crop: one day in each of the stages that matter.
    daily = [
        {"day": date(2026, 6, 1), "DVS": 0.10},   # just emerged: frost window
        {"day": date(2026, 6, 2), "DVS": 0.50},   # vegetative: neither window
        {"day": date(2026, 7, 1), "DVS": 1.00},   # flowering: silking window
        {"day": date(2026, 7, 2), "DVS": 1.50},   # grain fill: neither window
        {"day": date(2026, 8, 1), "DVS": 1.80},   # late fill: frost window
        {"day": date(2026, 8, 2), "DVS": None},   # not in the ground
    ]
    # Every day flagged both hot and frosty, so only the windows can filter.
    flags = {entry["day"].isoformat(): {"frost_day": True, "heat_stress_day": True}
             for entry in daily}

    heat, frost = runner.stress_in_windows(daily, flags, silking, frost_windows)
    check("only the flowering day counts as silking heat", heat == 1, str(heat))
    check("only the two frost-sensitive days count as frost", frost == 2, str(frost))

    # A heat day outside the window must not count, and a day with no flag
    # (a normals day) must not count either.
    partial = {date(2026, 7, 1).isoformat(): {"frost_day": False, "heat_stress_day": True}}
    heat, frost = runner.stress_in_windows(daily, partial, silking, frost_windows)
    check("days the weather model did not cover contribute nothing",
          heat == 1 and frost == 0, f"{heat} {frost}")

    # Window edges are inclusive, and a day just outside is excluded.
    edge = [{"day": date(2026, 7, 1), "DVS": 0.90},
            {"day": date(2026, 7, 2), "DVS": 1.20},
            {"day": date(2026, 7, 3), "DVS": 1.2001}]
    edge_flags = {e["day"].isoformat(): {"frost_day": False, "heat_stress_day": True}
                  for e in edge}
    heat, _ = runner.stress_in_windows(edge, edge_flags, silking, frost_windows)
    check("the silking window includes both ends and excludes just past it",
          heat == 2, str(heat))


def check_no_overlay(output):
    print("no undocumented overlay on the yield figures (AC-7)")
    columns = set(output["columns"])
    check("no adjusted-anomaly column is published",
          not any("adjusted" in name for name in columns),
          str(sorted(name for name in columns if "adjusted" in name)))
    check("the metadata states that no overlay is applied",
          "None." in output["metadata"]["stress_overlay"],
          output["metadata"]["stress_overlay"][:80])
    for row in output["rows"]:
        if row["yield_anomaly_pct"] is None:
            continue
        expected = ((row["yield_projection_kg_ha"] - row["yield_baseline_kg_ha"])
                    / row["yield_baseline_kg_ha"] * 100)
        check(f"{row['region_key']}: the anomaly is exactly (projection - baseline) / baseline",
              close(row["yield_anomaly_pct"], expected, 0.02),
              f"{row['yield_anomaly_pct']} vs {expected:.2f}")


# --- AC-8: an unknown region fails loudly ------------------------------------

def check_unknown_region_fails(sample_path):
    print("an unknown region fails loudly (AC-8)")
    document = json.loads(Path(sample_path).read_text())
    stranger = json.loads(json.dumps(document))
    for row in stranger["rows"]:
        if row["region_key"] == "ia":
            row["region_key"] = "zz"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = tmp / "stranger.json"
        path.write_text(json.dumps(stranger))
        result = run_model(path, tmp / "trajectory.json")
    check("the run exits non-zero", result.returncode != 0, str(result.returncode))
    check("the message names the unknown key", "zz" in result.stderr, result.stderr[-200:])
    check("the message names the table to fix",
          "soils.csv" in result.stderr, result.stderr[-200:])
    check("it is a readable error, not a traceback",
          "Traceback" not in result.stderr, result.stderr[-200:])


# --- tables ------------------------------------------------------------------

def check_tables(output):
    print("committed tables")
    soils = runner.read_csv_keyed(runner.SOILS_PATH)
    planting = runner.read_csv_keyed(runner.PLANTING_PATH)
    normals = runner.load_climatology()
    baselines = runner.load_baselines()
    node1_keys = {"ia", "il", "mn", "ne", "in", "sd", "oh", "wi", "ks", "mo"}
    for name, table in (("soils.csv", soils), ("planting_dates.csv", planting),
                        ("climatology.csv", normals), ("baseline_yields.csv", baselines)):
        check(f"{name} covers node 1's ten region keys",
              node1_keys <= set(table), str(sorted(node1_keys - set(table))))
    for key, days in normals.items():
        check(f"climatology.csv has a full year for {key}", len(days) == 365, str(len(days)))
        break
    check("29 February falls back to 28 February",
          runner.normals_for(normals, "ia", date(2024, 2, 29))
          == runner.normals_for(normals, "ia", date(2024, 2, 28)))
    for key, values in baselines.items():
        check(f"baseline_yields.csv has a full period for {key}",
              len(values) >= 25, str(len(values)))
        break
    check("the baseline period is the documented one",
          output["metadata"]["baselines"]["period"] == "1995-2024",
          output["metadata"]["baselines"]["period"])
    check("the baseline is built on real years, not the mean climatology",
          "real daily" in output["metadata"]["baselines"]["method"])


def check_percentile_rank():
    print("percentile rank")
    distribution = [1.0, 2.0, 3.0, 4.0, 5.0]
    check("a value above every year ranks 100",
          runner.percentile_rank(distribution, 6.0) == 100.0)
    check("a value below every year ranks 0",
          runner.percentile_rank(distribution, 0.5) == 0.0)
    check("the middle value ranks 40 (two of five below it)",
          runner.percentile_rank(distribution, 3.0) == 40.0,
          str(runner.percentile_rank(distribution, 3.0)))
    check("the median of an even-length distribution is the midpoint",
          runner.median([1.0, 2.0, 3.0, 4.0]) == 2.5)


def check_units(output):
    print("units and conversions")
    divisor = output["metadata"]["kg_ha_to_bu_acre_divisor"]
    check("the kg/ha -> bu/acre divisor is 25.4012 x 2.47105",
          close(divisor, 25.4012 * 2.47105, 1e-4), str(divisor))
    for row in output["rows"]:
        check(f"{row['region_key']}: bu/acre matches kg/ha through that divisor",
              close(row["yield_projection_bu_acre"],
                    row["yield_projection_kg_ha"] / divisor, 0.01))
    check("the model declares itself deterministic and offline",
          "offline" in output["metadata"]["determinism"].lower())


def main():
    if len(sys.argv) < 2:
        print("usage: check_yield.py <run/corn_yield_snapshot.output.json> [sample_input.json]")
        raise SystemExit(2)
    output = json.loads(Path(sys.argv[1]).read_text())
    sample_path = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "sample_input.json"

    check_wofost_run(output)
    check_tables(output)
    check_units(output)
    check_percentile_rank()
    check_stress_windows()
    check_no_overlay(output)
    check_water_limitation(sample_path)
    check_anomaly_responds(sample_path, output)
    check_unknown_region_fails(sample_path)

    print(f"\n{PASS}/{PASS + FAIL} checks pass")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
