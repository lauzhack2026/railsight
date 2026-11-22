#!/bin/sh
# Reproduce the whole scan. Tiles and model answers are cached under cache/; re-running only does what is missing.
set -e
cd "$(dirname "$0")"
PY=${PY:-../.venv/bin/python}
$PY segments.py          # BAV network -> ~1 km segments, tunnel parts from OSM
$PY fetch.py 12          # swisstopo / BAFU WMTS tiles around open track (cached, <= 12 requests/s)
$PY features.py          # step 1: numbers per segment, no model
$PY incidents.py         # SBB liveticker incidents -> segments; trains per edge
$PY assess.py            # steps 2 and 3: text model on the numbers, vision model on a few crops
$PY assess.py terrain    # evaluation variant without incident history
$PY naive.py 12          # measured baseline: every km as one image to a frontier vision model
$PY evaluate.py          # out-of-time check
$PY export.py            # app/public/data
