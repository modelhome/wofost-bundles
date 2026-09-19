# wofost-bundles

Standalone [Model Home](https://modelhome.run) model bundles built on
[PCSE](https://github.com/ajwdewit/pcse)/WOFOST, the crop growth model behind
the EU MARS operational yield-forecasting system. Each subfolder is a
self-contained model: a `Modelfile.toml`, a `Dockerfile`, a `runner.py`, and
sample inputs.

Nothing upstream is vendored here. PCSE is installed as a pinned pip package
inside each bundle's image.

## Bundles

| Bundle | Model | Inputs -> Outputs |
|---|---|---|
| [`corn-yield/`](./corn-yield) | US corn yield (WOFOST): development stage, projected yield and the weather-driven yield anomaly, per corn state | a `crop-weather` daily weather table (nothing else) -> per region: development stage and days to flowering and maturity, projected and normal-weather yields, the yield anomaly as a percentage, and heat and frost stress counted inside the stages where it matters |

`corn-yield/` is node 2 of a climate -> agriculture -> finance flow:
[`agromet-bundles/crop-weather/`](https://github.com/modelhome/agromet-bundles)
supplies the weather, this bundle turns it into a yield signal, and
`ag-commodity-bundles/corn-price/` turns that into a price impact. The three are
composed in a Model Home Flow.

## Quick start

Each bundle builds and runs from its own folder, which is also the build context
Model Home uses:

```bash
cd corn-yield
docker build -t wofost-corn-yield:local .
docker run --rm wofost-corn-yield:local   # needs no network
```

Or, when creating a new model on Model Home, paste the bundle folder's GitHub
URL (for example
`https://github.com/modelhome/wofost-bundles/tree/main/corn-yield`) into the
"classic import" option.

Each bundle's README covers its inputs, outputs, parameter sources, unit
conversions and assumptions. See [`CLAUDE.md`](./CLAUDE.md) for the design
notes, the conventions every bundle follows, the verified PCSE run
configuration, and how features are briefed, planned and built.

## Licence

MIT. See [`LICENSE`](./LICENSE). The crop model is
[PCSE](https://github.com/ajwdewit/pcse) (MIT) and its crop parameters come from
[`ajwdewit/WOFOST_crop_parameters`](https://github.com/ajwdewit/WOFOST_crop_parameters);
each bundle's README carries the full attribution for its own data tables.
