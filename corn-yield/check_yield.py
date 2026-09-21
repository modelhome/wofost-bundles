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
- a gap in the upstream series fails rather than being filled with normals;
- every output field the Modelfile declares required is actually never null;
- the baseline and the runtime use the same pinned crop parameters;
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

def check_gap_fails(sample_path):
    """
    A hole in the upstream series must fail, not be papered over with normals.

    Normals legitimately complete the tail of a season. The same fallback
    applied to a missing day in the middle of node 1's own range would change
    the yield and understate forecast_fraction while hiding upstream data loss.
    """
    print("a gap in the upstream series fails loudly")
    document = json.loads(Path(sample_path).read_text())
    gapped = json.loads(json.dumps(document))
    # Drop a single midsummer day for one region only.
    missing = "2026-07-15"
    before = len(gapped["rows"])
    gapped["rows"] = [r for r in gapped["rows"]
                      if not (r["region_key"] == "ia" and r["date"] == missing)]
    check("exactly one row was removed", before - len(gapped["rows"]) == 1,
          str(before - len(gapped["rows"])))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = tmp / "gapped.json"
        path.write_text(json.dumps(gapped))
        result = run_model(path, tmp / "trajectory.json")
    check("the run exits non-zero", result.returncode != 0, str(result.returncode))
    check("the message names the missing day", missing in result.stderr, result.stderr[-250:])
    check("the message says it will not fill it with normals",
          "normals" in result.stderr, result.stderr[-250:])
    check("it is a readable error, not a traceback",
          "Traceback" not in result.stderr, result.stderr[-250:])


def check_schema_honesty(output):
    """
    Every field the Modelfile declares required must actually be non-null.

    The platform's schema format has no nullable type, so a required field that
    the runner can leave empty is a promise the model cannot keep. This is why
    the planting-date and variety overrides were removed.
    """
    print("declared-required output fields are never null")
    import tomllib
    definition = tomllib.loads((HERE / "Modelfile.toml").read_text())
    snapshot = next(o for o in definition["outputs"] if o["name"] == "corn_yield_snapshot")
    required = snapshot["schema"]["properties"]["rows"]["items"]["required"]
    for row in output["rows"]:
        for name in required:
            check(f"{row['region_key']}: required field '{name}' is present and not null",
                  row.get(name) is not None, repr(row.get(name)))
    check("no override input can suppress the anomaly",
          "planting_date" not in definition["inputs"][0]["schema"].get("properties", {}))


def check_crop_parameter_pin(output):
    print("the baseline and the runtime use the same crop parameters")
    meta = output["metadata"]
    baseline_sha = meta["baselines"].get("crop_parameters_sha")
    check("the baseline records the crop-parameter commit it was built with",
          bool(baseline_sha), str(baseline_sha))
    runtime_sha = meta.get("crop_parameters_sha")
    if runtime_sha is None:
        print("        running outside the image: the runtime commit is unknowable, "
              "and the metadata says so rather than claiming a match")
        check("the metadata does not claim a verified pin",
              meta.get("crop_parameters_pin_verified") is False)
    else:
        check("the runtime commit matches the baseline's",
              runtime_sha == baseline_sha, f"{runtime_sha} vs {baseline_sha}")
        check("the metadata records the pin as verified",
              meta.get("crop_parameters_pin_verified") is True)


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

NODE1_REGIONS_CSV = (
    HERE.parent.parent / "agromet-bundles" / "crop-weather" / "regions.csv")


def node1_region_keys(output):
    """
    Node 1's region set, which node 1 owns and this node only joins on.

    Read from agromet-bundles/crop-weather/regions.csv when the sibling repo is
    checked out beside this one, so the check is against the real upstream
    contract rather than a list copied into this file that would rot the next
    time node 1 splits a state. Falling back to the regions actually present in
    the output keeps the check runnable in a bare checkout, and says so.
    """
    if NODE1_REGIONS_CSV.exists():
        import csv as _csv
        with NODE1_REGIONS_CSV.open(newline="", encoding="utf-8") as handle:
            return {row["region_key"] for row in _csv.DictReader(handle)}, "regions.csv"
    return {row["region_key"] for row in output["rows"]}, "this run's output"


def check_tables(output):
    print("committed tables")
    soils = runner.read_csv_keyed(runner.SOILS_PATH)
    planting = runner.read_csv_keyed(runner.PLANTING_PATH)
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    normals = runner.load_climatology()
    baselines = runner.load_baselines()
    node1_keys, origin = node1_region_keys(output)
    print(f"        node 1's region set, from {origin}: {len(node1_keys)} keys")
    tables = (("soils.csv", soils), ("planting_dates.csv", planting),
              ("water_regime.csv", regimes), ("climatology.csv", normals),
              ("baseline_yields.csv", baselines))
    for name, table in tables:
        check(f"{name} covers node 1's {len(node1_keys)} region keys",
              node1_keys <= set(table), str(sorted(node1_keys - set(table))))
        # A leftover key is as wrong as a missing one: it means a table still
        # carries a region node 1 has stopped emitting, such as the pre-split
        # 'ne' and 'ks'.
        check(f"{name} carries no region node 1 does not define",
              set(table) <= node1_keys, str(sorted(set(table) - node1_keys)))
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


# --- AC-2/AC-5: the water regime ---------------------------------------------

UNSPLIT_REGRESSION_PATH = HERE / "unsplit_regression.json"


def check_regime_table(output):
    print("the water regime is declared, sourced and sane (AC-2)")
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    soils = runner.read_csv_keyed(runner.SOILS_PATH)

    for key, row in sorted(regimes.items()):
        check(f"{key}: regime is one of rainfed/irrigated",
              row["regime"] in ("rainfed", "irrigated"), row["regime"])
        check(f"{key}: the row carries method and source text",
              bool(row["method"].strip()) and bool(row["source"].strip()))

    irrigated = {key for key, row in regimes.items() if row["regime"] == "irrigated"}
    # Node 1 decides which states are split, by its own 20 percent threshold.
    # If node 1 splits another state, this check fails and the table must be
    # extended deliberately rather than by a rule that guesses from the key.
    check("exactly node 1's two irrigated strata are declared irrigated",
          irrigated == {"ne_irrigated", "ks_irrigated"}, str(sorted(irrigated)))

    for key in sorted(irrigated):
        row = regimes[key]
        soil = {name: float(soils[key][name]) for name in runner.SOIL_PARAMETERS}
        amount_cm = float(row["irrigation_amount_cm"])
        available_cm = (soil["SMFCF"] - soil["SMW"]) * soil["RDMSOL"]
        # The cm/mm trap. PCSE reads irrigation_amount as cm (RIRR is cm/day),
        # but its own docstring example reads as mm. A value entered as though
        # it were mm -- 25.4 for one inch instead of 2.54 -- would exceed the
        # whole profile's plant-available water in a single application and
        # still produce plausible-looking output.
        check(f"{key}: one application ({amount_cm} cm) is less than the profile's "
              f"plant-available water ({available_cm:.1f} cm), so the amount is cm not mm",
              0 < amount_cm < available_cm, f"{amount_cm} vs {available_cm:.1f}")
        trigger = runner.irrigation_trigger_sm(soil, row, key)
        check(f"{key}: the trigger SM {trigger:.4f} lies between SMW {soil['SMW']} "
              f"and SMFCF {soil['SMFCF']}",
              soil["SMW"] < trigger < soil["SMFCF"], str(trigger))

    # AC-2's second sentence: the regime is a table lookup, never a guess at the
    # spelling of a region key.
    for name in ("runner.py", "build_baselines.py"):
        text = (HERE / name).read_text()
        offenders = [line.strip() for line in text.splitlines()
                     if ("_irrigated" in line or "_rainfed" in line)
                     and ("==" in line or "endswith" in line or "startswith" in line
                          or "in region_key" in line)]
        check(f"{name} never infers the regime from the region key's spelling",
              not offenders, "; ".join(offenders[:2]))

    # The agromanagement a rainfed region gets must be the campaign this bundle
    # used before brief 0002: no events at all.
    soil = {name: float(soils["ia"][name]) for name in runner.SOIL_PARAMETERS}
    rainfed = runner.agromanagement_for(
        date(2026, 5, 4), "Grain_maize_203", 200, soil, regimes["ia"], "ia")
    campaign = list(rainfed[0].values())[0]
    check("a rainfed region is given no irrigation events at all",
          campaign["StateEvents"] is None and campaign["TimedEvents"] is None)

    soil_ks = {name: float(soils["ks_irrigated"][name]) for name in runner.SOIL_PARAMETERS}
    irr = runner.agromanagement_for(
        date(2026, 4, 22), "Grain_maize_205", 200, soil_ks,
        regimes["ks_irrigated"], "ks_irrigated")
    events = list(irr[0].values())[0]["StateEvents"]
    check("an irrigated region is given one SM-triggered irrigation event",
          events is not None and len(events) == 1
          and events[0]["event_signal"] == "irrigate"
          and events[0]["event_state"] == "SM")
    check("the irrigation event fires on falling soil moisture, so it does not "
          "re-fire on the rebound it just caused",
          events[0]["zero_condition"] == "falling", events[0]["zero_condition"])


def check_baseline_regime_matches(output):
    print("each baseline was built under the regime its projection uses")
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    per_region = output["metadata"]["baselines"].get("regions", {})
    for key, row in sorted(regimes.items()):
        recorded = per_region.get(key, {}).get("regime")
        # If these disagree the anomaly compares two different models, which is
        # the silent wrong answer this bundle refuses to produce.
        check(f"{key}: baseline regime '{recorded}' matches the run's '{row['regime']}'",
              recorded == row["regime"], f"{recorded} vs {row['regime']}")


def check_baseline_regime_mismatch_fails():
    print("a baseline built under the wrong regime fails before any projection runs")
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    real_meta = runner.load_baselines_meta()
    keys = sorted(regimes)

    # The real pair must pass, or the check below proves nothing.
    try:
        runner.check_baseline_regimes(regimes, real_meta, keys)
        check("the committed baseline and regime table agree", True)
    except runner.RunError as exc:
        check("the committed baseline and regime table agree", False, str(exc)[:200])

    for scenario, doctored in (
            ("a region's baseline was built under the other regime",
             {"regions": {k: dict(v, regime=("rainfed" if v.get("regime") == "irrigated"
                                             else "irrigated"))
                          for k, v in real_meta.get("regions", {}).items()}}),
            ("the baseline predates water_regime.csv and records no regime",
             {"regions": {k: {key: value for key, value in v.items() if key != "regime"}
                          for k, v in real_meta.get("regions", {}).items()}})):
        try:
            runner.check_baseline_regimes(regimes, doctored, keys)
        except runner.RunError as exc:
            message = str(exc)
            check(f"{scenario}: the run refuses", True)
            check(f"{scenario}: the message names the two tables",
                  "water_regime.csv" in message and "baseline_yields.csv" in message,
                  message[:160])
            check(f"{scenario}: the message says how to fix it",
                  "build_baselines.py" in message, message[:160])
        else:
            check(f"{scenario}: the run refuses", False, "it was accepted")


def check_irrigation_gap(output):
    print("the irrigated stratum out-yields the rainfed one (AC-5)")
    rows = {row["region_key"]: row for row in output["rows"]}
    # NASS 2022 Census, operation-level: operations irrigating their entire corn
    # crop out-yield those irrigating none by 105 percent in Kansas and 55
    # percent in Nebraska. That is an operation-class comparison confounded with
    # soil quality and management, while this is a two-point simulation that
    # differs in regime and in weather -- they are not the same estimand. So the
    # band is deliberately wide: it checks the sign and the order of magnitude,
    # and is a sanity bound, not a calibration target. A result outside it is a
    # finding to report, not a number to tune.
    reference = {"ks": 105.0, "ne": 55.0}
    for state in ("ks", "ne"):
        irrigated = rows.get(f"{state}_irrigated")
        rainfed = rows.get(f"{state}_rainfed")
        if irrigated is None or rainfed is None:
            check(f"{state}: both strata are present in the output", False,
                  "one of the two strata is missing")
            continue
        wet = irrigated["yield_projection_kg_ha"]
        dry = rainfed["yield_projection_kg_ha"]
        gap = (wet - dry) / dry * 100.0
        print(f"        {state}: irrigated {wet:.0f} vs rainfed {dry:.0f} kg/ha, "
              f"gap {gap:+.1f}% (NASS operation-level reference {reference[state]:+.0f}%)")
        check(f"{state}: the simulated irrigated-minus-rainfed gap is positive",
              gap > 0, f"{gap:+.1f}%")
        check(f"{state}: the gap is within the documented 25-200% sanity band",
              25.0 <= gap <= 200.0, f"{gap:+.1f}%")


def check_unsplit_regions_unchanged():
    print("the eight unsplit states are unchanged by this feature (AC-3)")
    if not UNSPLIT_REGRESSION_PATH.exists():
        check("the pre-change regression fixture is committed", False,
              f"missing {UNSPLIT_REGRESSION_PATH.name}")
        return
    fixture = json.loads(UNSPLIT_REGRESSION_PATH.read_text())
    keys = set(fixture["rows"])
    # The fixture stores expected figures only, not a second copy of the input.
    # The eight unsplit states' rows in sample_input.json are byte-identical to
    # the ones the pre-change run was given, so subsetting it here reconstructs
    # that exact input.
    document = json.loads((HERE / "sample_input.json").read_text())
    subset = dict(document)
    subset["rows"] = [row for row in document["rows"] if row["region_key"] in keys]
    subset["metadata"] = dict(document["metadata"])
    subset["metadata"]["regions"] = [
        region for region in document["metadata"]["regions"]
        if region["region_key"] in keys]
    subset["metadata"]["angstrom"] = {
        key: value for key, value in document["metadata"]["angstrom"].items()
        if key in keys}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = tmp / "unsplit.json"
        path.write_text(json.dumps(subset))
        result = run_model(path, tmp / "trajectory.json")
    if result.returncode != 0:
        check("the run over the eight unsplit states succeeds", False,
              result.stderr[-300:])
        return
    now = {row["region_key"]: row for row in json.loads(result.stdout)["rows"]}
    for key, expected in sorted(fixture["rows"].items()):
        row = now.get(key)
        if row is None:
            check(f"{key}: still present in the output", False, "missing")
            continue
        differences = [name for name, value in expected.items() if row.get(name) != value]
        check(f"{key}: every figure is identical to the pre-change run",
              not differences,
              "; ".join(f"{n}: {expected[n]} -> {row.get(n)}" for n in differences[:3]))


def check_every_table_fails_loudly(sample_path):
    print("a region missing from any one table fails loudly, naming that table (AC-7)")
    document = json.loads(Path(sample_path).read_text())
    victim = document["rows"][0]["region_key"]
    region_rows = [row for row in document["rows"] if row["region_key"] == victim]
    settings = {"as_of": document["metadata"]["date"], "max_duration": 200,
                "silking_window": (0.9, 1.2), "frost_windows": ((0.0, 0.15), (1.7, 2.0))}
    full = {
        "soils": runner.read_csv_keyed(runner.SOILS_PATH),
        "planting": runner.read_csv_keyed(runner.PLANTING_PATH),
        "regimes": runner.read_csv_keyed(runner.WATER_REGIME_PATH),
        "normals": runner.load_climatology(),
        "baselines": runner.load_baselines(),
        "baselines_meta": runner.load_baselines_meta(),
    }
    for table_name, name in (("soils", "soils.csv"), ("planting", "planting_dates.csv"),
                             ("regimes", "water_regime.csv"),
                             ("normals", "climatology.csv"),
                             ("baselines", "baseline_yields.csv")):
        tables = dict(full)
        tables[table_name] = {k: v for k, v in full[table_name].items() if k != victim}
        try:
            runner.process_region(victim, region_rows, tables, settings, document)
        except runner.RunError as exc:
            message = str(exc)
            check(f"a missing {name} row raises, and the message names {name}",
                  name in message and victim in message, message[:160])
        except Exception as exc:  # noqa: BLE001 - any other type is the failure
            check(f"a missing {name} row raises a readable RunError", False,
                  f"{type(exc).__name__}: {exc}")
        else:
            check(f"a missing {name} row raises", False, "the run completed")


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
    check_regime_table(output)
    check_baseline_regime_matches(output)
    check_baseline_regime_mismatch_fails()
    check_irrigation_gap(output)
    check_unsplit_regions_unchanged()
    check_units(output)
    check_percentile_rank()
    check_stress_windows()
    check_no_overlay(output)
    check_schema_honesty(output)
    check_crop_parameter_pin(output)
    check_water_limitation(sample_path)
    check_anomaly_responds(sample_path, output)
    check_unknown_region_fails(sample_path)
    check_every_table_fails_loudly(sample_path)
    check_gap_fails(sample_path)

    print(f"\n{PASS}/{PASS + FAIL} checks pass")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
