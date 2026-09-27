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
- no yield figure carries an undocumented overlay (AC-7);
- the irrigated strata's supply limits are sourced, respected and auditable,
  and with the limits removed they reproduce brief 0002 exactly (brief 0003).

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
            return None
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
    return hot


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

    # Brief 0003 AC-10: the supply limits and what they did are in the metadata,
    # additively -- the row schema above is unchanged.
    season_keys = {"applied_gross_cm", "irrigation_days", "max_daily_gross_cm",
                   "days_at_capacity_ceiling", "first_cap_reached_date",
                   "yield_unlimited_kg_ha", "yield_loss_to_limits_pct", "limits_bound",
                   "binding_limits"}
    for key, block in sorted(output["metadata"]["water_regime"].items()):
        if block["regime"] == "irrigated":
            check(f"{key}: the metadata reports the limits in force and what the season "
                  f"used", block["irrigation"] is not None and block["season"] is not None
                  and season_keys <= set(block["season"])
                  and block["limits_method"] and block["limits_source"],
                  str(sorted(season_keys - set(block["season"] or {}))))
        else:
            check(f"{key}: a rainfed region reports no irrigation",
                  block["irrigation"] is None and block["season"] is None)
    check("the snapshot columns are unchanged by brief 0003",
          output["columns"] == runner.SNAPSHOT_COLUMNS
          and all(set(row) == set(runner.SNAPSHOT_COLUMNS) for row in output["rows"]))


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
REGIME_REGRESSION_PATH = HERE / "regime_regression.json"
IRRIGATED_KEYS = ("ne_irrigated", "ks_irrigated")


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
    # spelling of a region key. Brief 0003 extends this to the supply limits,
    # which live in the same table and are read through the same lookup.
    for name in ("runner.py", "build_baselines.py"):
        text = (HERE / name).read_text()
        offenders = [line.strip() for line in text.splitlines()
                     if ("_irrigated" in line or "_rainfed" in line)
                     and ("==" in line or "endswith" in line or "startswith" in line
                          or "in region_key" in line)]
        check(f"{name} never infers the regime or a limit from the region key's spelling",
              not offenders, "; ".join(offenders[:2]))

    # Every region, irrigated or not, gets the single sowing-to-maturity campaign
    # this bundle used before brief 0002: no events at all. Irrigation is now
    # applied from inside the engine, so the campaign no longer differs by
    # regime (this assertion replaced 0002's two StateEvent assertions).
    campaign = list(runner.agromanagement_for(
        date(2026, 5, 4), "Grain_maize_203", 200)[0].values())[0]
    check("every region is given a campaign with no events at all",
          campaign["StateEvents"] is None and campaign["TimedEvents"] is None)

    # A rainfed region runs the plain engine with no controller; an irrigated one
    # runs the subclass, with the controller configured from its table row.
    from pcse.base import ParameterProvider
    from pcse.input import WOFOST72SiteDataProvider
    from pcse.models import Wofost72_WLP_FD
    crop = runner.crop_data_provider("Grain_maize_203", "ia")
    document = json.loads((HERE / "sample_input.json").read_text())
    grouped = runner.group_rows(document)
    normals = runner.load_climatology()
    meta = runner.load_baselines_meta()
    for key in ("ia", "ks_irrigated"):
        rows = grouped[key]
        soil = {name: float(soils[key][name]) for name in runner.SOIL_PARAMETERS}
        planting_day = date(2026, 5, 1)
        series = runner.build_series(rows, normals, key, planting_day,
                                     planting_day + timedelta(days=200), False)
        a, b = runner.angstrom_for(meta, key)
        provider = runner.build_provider(series, float(rows[0]["LAT"]), float(rows[0]["LON"]),
                                         float(rows[0]["ELEV"]), a, b, "check")
        crop.set_active_crop(runner.CROP_NAME, "Grain_maize_203")
        parameters = ParameterProvider(cropdata=crop, soildata=soil,
                                       sitedata=WOFOST72SiteDataProvider(WAV=float(soils[key]["WAV"])))
        model, controller = runner.model_for(
            parameters, provider, runner.agromanagement_for(planting_day, "Grain_maize_203", 200),
            soil, regimes[key], key)
        if regimes[key]["regime"] == "rainfed":
            check(f"{key} (rainfed) runs the plain Wofost72_WLP_FD with no controller",
                  type(model) is Wofost72_WLP_FD and controller is None, type(model).__name__)
        else:
            expected = runner.irrigation_parameters(soil, regimes[key], key)
            check(f"{key} (irrigated) runs the irrigated subclass, its controller "
                  f"configured from water_regime.csv",
                  isinstance(model, runner.IrrigatedWofost72_WLP_FD)
                  and controller is not None and controller.parameters == expected,
                  f"{type(model).__name__}: {controller and controller.parameters}")
            limitless = runner.unlimited(expected)
            check(f"{key}: the unlimited counterfactual removes both limits and nothing else",
                  limitless["allocation_cap_cm"] is None
                  and limitless["capacity_cm_day_gross"] == expected["irrigation_amount_cm"]
                  and {k: v for k, v in limitless.items()
                       if k not in ("allocation_cap_cm", "capacity_cm_day_gross")}
                  == {k: v for k, v in expected.items()
                      if k not in ("allocation_cap_cm", "capacity_cm_day_gross")})


def check_limits_table():
    print("the supply limits are declared, sourced and converted correctly (brief 0003 AC-1)")
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    limit_columns = ("allocation_cap_cm", "capacity_net_gpm_ac", "capacity_cm_day_gross",
                     "limits_method", "limits_source")
    for key, row in sorted(regimes.items()):
        check(f"{key}: the limit columns exist", all(c in row for c in limit_columns),
              str([c for c in limit_columns if c not in row]))
        if row["regime"] == "rainfed":
            check(f"{key}: a rainfed row declares no limits",
                  all(not (row.get(c) or "").strip() for c in limit_columns))
            continue
        # The ceiling is stored gross, cm/day; re-derive it from the recorded net
        # gpm/ac so a hand edit to either cannot drift silently. 1 acre-inch is
        # 27,154 US gal, so 1 gpm/ac pumped for 24 h is 1,440 / 27,154 in/day =
        # 0.05303 in/day = 0.13470 cm/day; dividing by efficiency makes it gross.
        cm_day_per_gpm_ac = 1440.0 / (43560.0 / 12.0 * 7.48052) * 2.54
        net = float(row["capacity_net_gpm_ac"])
        derived = net * cm_day_per_gpm_ac / float(row["efficiency"])
        stored = float(row["capacity_cm_day_gross"])
        check(f"{key}: {net} net gpm/ac converts to the stored {stored} cm/day gross",
              abs(derived - stored) < 0.0001, f"derived {derived:.5f}")
        check(f"{key}: the ceiling is below one application ({row['irrigation_amount_cm']} cm), "
              f"so it is a real daily limit",
              stored < float(row["irrigation_amount_cm"]), str(stored))
        check(f"{key}: the limits carry their own method and source text",
              bool(row["limits_method"].strip()) and bool(row["limits_source"].strip()))
        if not (row["allocation_cap_cm"] or "").strip():
            # An empty cap is a sourced "none", so the text must say so and name
            # where the rule was looked up; an unexplained blank is a missing value.
            check(f"{key}: an empty allocation cap is declared as a sourced 'none'",
                  "Allocation cap: none" in row["limits_method"]
                  and ("NRD" in row["limits_source"] or "GMD" in row["limits_source"]),
                  row["limits_method"][-160:])


def check_baseline_regime_matches(output):
    print("each baseline was built under the regime and limits its projection uses")
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    per_region = output["metadata"]["baselines"].get("regions", {})
    reported = output["metadata"]["water_regime"]
    for key, row in sorted(regimes.items()):
        recorded = per_region.get(key, {}).get("regime")
        # If these disagree the anomaly compares two different models, which is
        # the silent wrong answer this bundle refuses to produce.
        check(f"{key}: baseline regime '{recorded}' matches the run's '{row['regime']}'",
              recorded == row["regime"], f"{recorded} vs {row['regime']}")
        built = per_region.get(key, {}).get("irrigation")
        check(f"{key}: the baseline's recorded irrigation and limits match the run's",
              key in reported and built == reported[key]["irrigation"],
              f"{built} vs {reported.get(key, {}).get('irrigation')}")


def check_baseline_regime_mismatch_fails():
    print("a baseline built under the wrong regime or limits fails before any projection runs")
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    soils = runner.read_csv_keyed(runner.SOILS_PATH)
    real_meta = runner.load_baselines_meta()
    keys = sorted(regimes)

    # The real pair must pass, or the check below proves nothing.
    try:
        runner.check_baseline_regimes(regimes, soils, real_meta, keys)
        check("the committed baseline and regime table agree", True)
    except runner.RunError as exc:
        check("the committed baseline and regime table agree", False, str(exc)[:200])

    def doctor(change):
        return {"regions": {k: change(dict(v)) for k, v in real_meta.get("regions", {}).items()}}

    def other_ceiling(region):
        if region.get("irrigation"):
            region["irrigation"] = dict(region["irrigation"], capacity_cm_day_gross=
                                        region["irrigation"]["capacity_cm_day_gross"] * 2)
        return region

    for scenario, doctored in (
            ("a region's baseline was built under the other regime",
             doctor(lambda v: dict(v, regime=("rainfed" if v.get("regime") == "irrigated"
                                              else "irrigated")))),
            ("the baseline predates water_regime.csv and records no regime",
             doctor(lambda v: {key: value for key, value in v.items() if key != "regime"})),
            ("an irrigated baseline was built under a different pumping-capacity ceiling",
             doctor(other_ceiling)),
            ("the baseline predates brief 0003 and records no irrigation parameters",
             doctor(lambda v: {key: value for key, value in v.items() if key != "irrigation"}))):
        try:
            runner.check_baseline_regimes(regimes, soils, doctored, keys)
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
        # Brief 0003 AC-8: the gap before the supply limits, from the fixture
        # captured at the base commit, printed beside the one after them.
        before = json.loads(REGIME_REGRESSION_PATH.read_text())["rows"]
        wet_0002 = before[f"{state}_irrigated"]["yield_projection_kg_ha"]
        dry_0002 = before[f"{state}_rainfed"]["yield_projection_kg_ha"]
        gap_0002 = (wet_0002 - dry_0002) / dry_0002 * 100.0
        print(f"        {state}: irrigated {wet:.0f} vs rainfed {dry:.0f} kg/ha, "
              f"gap {gap:+.1f}% (unlimited supply, brief 0002: {gap_0002:+.1f}%; "
              f"NASS operation-level reference {reference[state]:+.0f}%)")
        check(f"{state}: the simulated irrigated-minus-rainfed gap is positive",
              gap > 0, f"{gap:+.1f}%")
        check(f"{state}: the gap is within the documented 25-200% sanity band",
              25.0 <= gap <= 200.0, f"{gap:+.1f}%")


def check_rainfed_regions_unchanged(output):
    print("the ten rainfed regions are unchanged by the supply limits (brief 0003 AC-3)")
    fixture = json.loads(REGIME_REGRESSION_PATH.read_text())
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    now = {row["region_key"]: row for row in output["rows"]}
    rainfed = sorted(key for key, row in regimes.items() if row["regime"] == "rainfed")
    check("ten regions are rainfed", len(rainfed) == 10, str(rainfed))
    for key in rainfed:
        expected = fixture["rows"][key]
        row = now.get(key, {})
        differences = [name for name, value in expected.items() if row.get(name) != value]
        check(f"{key}: every figure is identical to the pre-0003 run",
              not differences,
              "; ".join(f"{n}: {expected[n]} -> {row.get(n)}" for n in differences[:3]))
        check(f"{key}: no irrigation is reported for a rainfed region",
              output["metadata"]["water_regime"][key]["season"] is None)


def process_irrigated(key, regime_row, baselines=None):
    """Run one irrigated region in-process with a given regime row."""
    document = json.loads((HERE / "sample_input.json").read_text())
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    regimes[key] = regime_row
    tables = {
        "soils": runner.read_csv_keyed(runner.SOILS_PATH),
        "planting": runner.read_csv_keyed(runner.PLANTING_PATH),
        "regimes": regimes,
        "normals": runner.load_climatology(),
        "baselines": baselines or runner.load_baselines(),
        "baselines_meta": runner.load_baselines_meta(),
    }
    settings = {"as_of": document["metadata"]["date"], "max_duration": 200,
                "silking_window": runner.DEFAULT_SILKING_WINDOW,
                "frost_windows": runner.DEFAULT_FROST_WINDOWS}
    snapshot, _, season = runner.process_region(
        key, runner.group_rows(document)[key], tables, settings, document)
    return snapshot, season


def check_limits_off_reproduces_0002(output):
    print("with the limits removed, the irrigated strata reproduce brief 0002 exactly "
          "(brief 0003 AC-4)")
    fixture = json.loads(REGIME_REGRESSION_PATH.read_text())
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    for key in IRRIGATED_KEYS:
        # Limits non-binding: the ceiling at the application depth, no cap. The
        # 0002 baseline distribution is restored too, so every field -- baseline
        # median and percentile included -- is comparable.
        limitless = dict(regimes[key], allocation_cap_cm="",
                         capacity_cm_day_gross=regimes[key]["irrigation_amount_cm"])
        old_distribution = runner.load_baselines()
        old_distribution[key] = sorted(
            float(value) for value in fixture["irrigated_baseline_yields_kg_ha"][key].values())
        snapshot, _ = process_irrigated(key, limitless, old_distribution)
        expected = fixture["rows"][key]
        differences = [name for name, value in expected.items() if snapshot.get(name) != value]
        check(f"{key}: the projection with the limits removed is identical to brief 0002",
              not differences,
              "; ".join(f"{n}: {expected[n]} -> {snapshot.get(n)}" for n in differences[:3]))
        # The counterfactual the delivered run reports is that same unlimited run.
        season = output["metadata"]["water_regime"][key]["season"]
        check(f"{key}: the delivered run's unlimited-supply counterfactual is brief 0002's "
              f"yield ({expected['yield_projection_kg_ha']} kg/ha)",
              season["yield_unlimited_kg_ha"] == expected["yield_projection_kg_ha"],
              str(season["yield_unlimited_kg_ha"]))
        # And each baseline year's unlimited counterfactual is 0002's committed value.
        by_year = runner.load_baselines_meta()["regions"][key]["irrigation_binding"]["by_year"]
        old = fixture["irrigated_baseline_yields_kg_ha"][key]
        mismatched = [year for year, value in old.items()
                      if by_year.get(year, {}).get("yield_unlimited_kg_ha") != float(value)]
        check(f"{key}: all 30 baseline years with the limits removed equal brief 0002's",
              len(old) == 30 and not mismatched, str(mismatched[:5]))


def check_limits_respected(output):
    print("no day exceeds the ceiling and no season exceeds the cap (brief 0003 AC-5)")
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    meta = runner.load_baselines_meta()
    for key in IRRIGATED_KEYS:
        ceiling = float(regimes[key]["capacity_cm_day_gross"])
        cap = regimes[key]["allocation_cap_cm"]
        seasons = {"projection": output["metadata"]["water_regime"][key]["season"]}
        seasons.update(meta["regions"][key]["irrigation_binding"]["by_year"])
        over = [label for label, season in seasons.items()
                if season["max_daily_gross_cm"] > ceiling + 1e-9]
        check(f"{key}: no day of the projection or of 30 baseline seasons applies more than "
              f"the {ceiling} cm/day ceiling", not over and len(seasons) == 31, str(over[:5]))
        if cap:
            over_cap = [label for label, season in seasons.items()
                        if season["applied_gross_cm"] > float(cap) + 1e-9]
            check(f"{key}: no season applies more than the {cap} cm cap", not over_cap,
                  str(over_cap[:5]))

    # Under D-1 no delivered region has a cap, so the cap path is proven with a
    # synthetic one small enough to bind this season.
    key = "ks_irrigated"
    synthetic_cap = 10.0
    _, season = process_irrigated(key, dict(regimes[key], allocation_cap_cm=str(synthetic_cap)))
    check(f"a synthetic {synthetic_cap} cm cap is never exceeded "
          f"(applied {season['applied_gross_cm']} cm)",
          season["applied_gross_cm"] <= synthetic_cap + 1e-9, str(season["applied_gross_cm"]))
    check("the date the synthetic cap was reached is reported",
          season["first_cap_reached_date"] is not None, str(season))
    check("the synthetic cap is named among the limits that bound",
          "allocation_cap" in season["binding_limits"], str(season["binding_limits"]))


def check_limit_binding_years():
    print("how often the limits bound over the baseline years is recorded "
          "(brief 0003 AC-7)")
    meta = runner.load_baselines_meta()
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    for key in IRRIGATED_KEYS:
        binding = meta["regions"][key].get("irrigation_binding") or {}
        print(f"        {key}: limits cost > {binding.get('binding_threshold_loss_pct')}% of yield "
              f"in {binding.get('years_limits_bound')}/{binding.get('years')} years "
              f"(capacity {binding.get('years_capacity_bound')}, cap "
              f"{binding.get('years_cap_bound')}); > 5% in {binding.get('years_loss_over_5pct')}; "
              f"median loss {binding.get('yield_loss_to_limits_pct_median')}%, max "
              f"{binding.get('yield_loss_to_limits_pct_max')}%; median applied "
              f"{binding.get('applied_gross_cm_median')} cm")
        check(f"{key}: the binding counts are recorded for all 30 years",
              binding.get("years") == 30 and len(binding.get("by_year", {})) == 30
              and binding.get("years_limits_bound") is not None)
        # A cap that is a sourced "none" can never bind; if it did, the counts
        # would be describing a limit that does not exist.
        if not regimes[key]["allocation_cap_cm"]:
            check(f"{key}: with no allocation cap declared, the cap never binds",
                  binding.get("years_cap_bound") == 0, str(binding.get("years_cap_bound")))
    rainfed = [key for key, region in meta["regions"].items()
               if region.get("regime") == "rainfed" and region.get("irrigation_binding")]
    check("no rainfed region records irrigation binding", not rainfed, str(rainfed))


def check_capacity_stress(output, hot):
    print("a hot, dry flowering fortnight costs more with the limits than without "
          "(brief 0003 AC-9)")
    for key in IRRIGATED_KEYS:
        base_row = next(row for row in output["rows"] if row["region_key"] == key)
        hot_row = next(row for row in hot["rows"] if row["region_key"] == key)
        base = output["metadata"]["water_regime"][key]["season"]
        spell = hot["metadata"]["water_regime"][key]["season"]
        limited_loss = base_row["yield_projection_kg_ha"] - hot_row["yield_projection_kg_ha"]
        unlimited_loss = base["yield_unlimited_kg_ha"] - spell["yield_unlimited_kg_ha"]
        print(f"        {key}: the spell costs {limited_loss:.0f} kg/ha with the limits, "
              f"{unlimited_loss:.0f} kg/ha without; the limits cost "
              f"{base['yield_loss_to_limits_pct']}% -> {spell['yield_loss_to_limits_pct']}%")
        check(f"{key}: the spell costs more yield with the limits than without them",
              limited_loss > unlimited_loss, f"{limited_loss:.0f} vs {unlimited_loss:.0f}")
        check(f"{key}: under the spell the metadata names the pumping capacity as binding",
              spell["limits_bound"] and "pumping_capacity" in spell["binding_limits"],
              str(spell))


def check_malformed_limit_fails():
    print("a malformed supply limit fails loudly, naming the row (brief 0003 AC-11)")
    regimes = runner.read_csv_keyed(runner.WATER_REGIME_PATH)
    key = "ne_irrigated"
    for column, value in (("capacity_cm_day_gross", "-0.5"), ("capacity_cm_day_gross", ""),
                          ("allocation_cap_cm", "none")):
        try:
            process_irrigated(key, dict(regimes[key], **{column: value}))
        except runner.RunError as exc:
            message = str(exc)
            check(f"{column}={value!r} raises, naming water_regime.csv, the region and the column",
                  "water_regime.csv" in message and key in message and column in message,
                  message[:160])
        else:
            check(f"{column}={value!r} raises", False, "the run completed")


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
    check_limits_table()
    check_baseline_regime_matches(output)
    check_baseline_regime_mismatch_fails()
    check_irrigation_gap(output)
    check_unsplit_regions_unchanged()
    check_rainfed_regions_unchanged(output)
    check_limits_off_reproduces_0002(output)
    check_limits_respected(output)
    check_limit_binding_years()
    check_malformed_limit_fails()
    check_units(output)
    check_percentile_rank()
    check_stress_windows()
    check_no_overlay(output)
    check_schema_honesty(output)
    check_crop_parameter_pin(output)
    check_water_limitation(sample_path)
    hot = check_anomaly_responds(sample_path, output)
    if hot is not None:
        check_capacity_stress(output, hot)
    check_unknown_region_fails(sample_path)
    check_every_table_fails_loudly(sample_path)
    check_gap_fails(sample_path)

    print(f"\n{PASS}/{PASS + FAIL} checks pass")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
