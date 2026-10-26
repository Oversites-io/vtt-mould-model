# DIY mould detection

Build your own early warning for damp and mould, using the VTT model in this repo.

Four chapters. Each one adds one block. By the end you have a system that reads any temperature and humidity sensor, scores every room every night, and tells you which rooms to look at first.

```
  1. GET THE READINGS        2. GET IT IN ONE PLACE      3. SCORE IT              4. ALERT AND ACT
  ───────────────────        ──────────────────────      ───────────            ────────────────
  LoRaWAN sensor             network server webhook     hour by hour,           ok / watch / act
  BMS trend log         ──▶  or BMS export          ──▶ never the daily   ──▶   on the right clock
  any temp + RH source       one common CSV             average                 then do something
```

| Chapter | What you build | Script |
|---|---|---|
| [1. Get the readings](01-readings.md) | Sensors in the right place, reporting often enough | |
| [2. Get it in one place](02-one-place.md) | Every reading in one simple format | `from_uplinks.py` |
| [3. Score it](03-score.md) | A nightly mould score for every room | `score_csv.py` |
| [4. Alert and act](04-alert-act.md) | Who gets told, when, and what they do | |

## Try it in two minutes, with no hardware

```bash
# 1. Make 30 days of test data: 100 rooms, 8 of them with a real damp fault
python3 tools/sign-datagen/generate_sign_data.py --gzip --out-dir out

# 2. Turn the sensor messages into the common format
python3 diy/from_uplinks.py out/uplinks.jsonl.gz --out out/readings.csv

# 3. Score every room, every day
python3 diy/score_csv.py out/readings.csv --out out/daily_scores.csv
```

What you should see:

```
424245 readings from 100 rooms -> out/readings.csv
3000 room-days scored -> out/daily_scores.csv   latest: 8 act, 0 watch, 92 ok
```

All 8 damp rooms flagged, no false alarms. Each one went to **watch** about 9 days before it went to **act**. That gap is the point of the whole thing: time to fix it before it's on the wall.

## What you need

- Python 3.9 or later. Nothing to install.
- For real data: a temperature and humidity reading per room, with a time on it. Any source works.

## Before you rely on it

It's a model, not an inspection. A room sensor measures the air, not the wall, and cold spots run wetter than the room. Use it to decide where to look first, never to declare a home free of damp or mould. See "What is and isn't proven" in the [main README](../README.md).
