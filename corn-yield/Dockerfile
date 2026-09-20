# to build and run from your local machine (build context is this corn-yield/
# folder, which is also what Model Home uses when the model is added from
# github.com/modelhome/wofost-bundles/tree/main/corn-yield):
#   cd corn-yield && docker build -t wofost-corn-yield:local .
#   docker run --rm --network none wofost-corn-yield:local   # uses sample_input.json
#   mkdir -p run && cp sample_input.json run/crop_weather_daily.json
#   docker run --rm --network none -v "$PWD/run:/run" wofost-corn-yield:local \
#       /run/crop_weather_daily.json /run/corn_yield_trajectory.output.json \
#       > run/corn_yield_snapshot.output.json
# The model makes no network calls at run time: --network none is the test.

# --- crop parameters ---------------------------------------------------------
# PCSE's YAMLCropDataProvider does NOT ship its parameters inside the package:
# with no local path it downloads them from GitHub on first use and caches the
# result for seven days. A container relying on that cache would start failing a
# week after it was built, and fails immediately with no network at all. So the
# parameter repository is baked into the image here and read from a local path,
# which needs no network and never goes stale.
#
# Pinned to a commit rather than the wofost72 branch head, so two builds of this
# Dockerfile produce the same crop parameters and therefore the same yields.
FROM python:3.12-slim AS crop-parameters
ARG WOFOST_CROP_PARAMETERS_SHA=f0a6491f23685998fa2172b397ff959a3b5ea738
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --no-checkout https://github.com/ajwdewit/WOFOST_crop_parameters.git /crop_parameters \
    && git -C /crop_parameters checkout "${WOFOST_CROP_PARAMETERS_SHA}" \
    && rm -rf /crop_parameters/.git

# --- the model ---------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# Every version here is the one the verified local environment resolved for
# pcse==6.0.13, pinned so the image cannot drift out from under the numbers in
# README.md.
RUN pip install --no-cache-dir \
    pcse==6.0.13 \
    numpy==2.5.3 \
    pandas==3.0.6 \
    PyYAML==6.0.3 \
    openpyxl==3.1.5 \
    PyDispatcher==2.0.7 \
    requests==2.34.2 \
    dotmap==1.3.30 \
    traitlets-pcse==5.0.0.dev

COPY --from=crop-parameters /crop_parameters /app/crop_parameters

# The committed tables are part of the model, not inputs: the soil and planting
# choices, the 1995-2024 climatology normals, and the 30-year baseline yield
# distribution. build_climatology.py, build_baselines.py and check_yield.py are
# development tools and are deliberately not copied in.
COPY runner.py \
     soils.csv \
     planting_dates.csv \
     climatology.csv \
     climatology.meta.json \
     baseline_yields.csv \
     baselines.meta.json \
     sample_input.json \
     ./

ENTRYPOINT ["python", "runner.py"]

# irl the input/output paths come from the Modelfile; for local testing we
# default to the bundled sample input.
CMD ["sample_input.json"]
