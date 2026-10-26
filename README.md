# VTT mould model

The **VTT / Hukka-Viitanen mould growth model** in JavaScript and Python, with
tools for running it on temperature and humidity sensor data.

It answers one question: **given a room's temperature and relative humidity
over time, how much mould would grow on a surface, and is it getting worse?**

- Faithful to the published papers. Every constant is cited, nothing invented.
- JavaScript and Python versions, checked against each other hour by hour.
- No dependencies. Node 18+ or Python 3 standard library only.
- MIT licensed.

## How the model works

```
   temperature + RH, every hour
               │
               ▼
   is RH above the critical curve for this temperature?
               │
       yes ────┴──── no
        │             │
      GROW         DECLINE
   (faster when    (after a 6 to 24 hour
    warmer and      delay: mould does not
    wetter)         die back at once)
        │             │
        └──────┬──────┘
               ▼
       mould index M, 0 to 6
```

| M | What you would see (Viitanen & Ojanen 2007, Table 1) |
|---|---|
| 0 | No growth |
| 1 | Small amounts, microscope only: **growth has started** |
| 2 | Under 10% coverage, microscope |
| 3 | 10 to 30% coverage, **visible to the naked eye** |
| 4 | 30 to 70% coverage |
| 5 | Over 70% coverage |
| 6 | Very heavy, tight growth, ~100% coverage |

The critical curve depends on temperature. Cold air needs to be wetter before
mould grows:

| Temperature | 0 °C | 5 °C | 10 °C | 15 °C | 20 °C | above 20 °C |
|---|---|---|---|---|---|---|
| Critical RH | 100% | 88.0% | 82.0% | 80.0% | 80.0% | 80% (85% for resistant materials) |

Materials come in four **sensitivity classes** (very sensitive, sensitive,
medium resistant, resistant). See Menneer et al. 2022, Table 1, for which
materials fall in each. A resistant surface can never reach visible mould,
however wet it gets.

## Quick start

**JavaScript**

```js
import { SENSITIVITY_CLASSES, stepVTT } from './js/vtt-model.mjs'

const cls = SENSITIVITY_CLASSES.very_sensitive
let m = 0, dry = 0
for (const [tempC, rhPct] of hourlyReadings) {
  ;({ m, dryHours: dry } = stepVTT(m, tempC, rhPct, dry, 1, cls))
}
console.log(m) // mould index, 0 to 6
```

**Python**

```python
from vtt_hukka_viitanen import MouldModel

model = MouldModel("very_sensitive")
for temp_c, rh_pct in hourly_readings:
    m = model.step(temp_c, rh_pct, dt_hours=1.0)
print(m)  # mould index, 0 to 6
```

To carry state between runs (a nightly job, say), save **both** the index and
the dry-spell hours. The decline delay depends on how long the surface has
been dry, so dropping it resets the clock.

## How fast does it grow?

Continuous exposure, very sensitive material, from a clean start
(`python3 python/vtt_hukka_viitanen.py`):

| Conditions | Growth starts (M1) | Visible mould (M3) |
|---|---|---|
| 20 °C, 97% RH | 10.4 days (published: ~1.5 weeks) | 20.8 days |
| 20 °C, 95% RH | | 27.8 days |
| 15 °C, 95% RH | | 33.8 days |
| 20 °C, 90% RH | | 59.5 days |
| 15 °C, 90% RH | | 72.4 days |
| 20 °C, 85% RH | | never (M3 is out of reach at this humidity) |

## Three things worth knowing

**1. Step hour by hour. Never score a daily mean.**
VTT only grows while RH is above the curve. A cold bedroom at 14.5 °C / 88% for
9 hours a night and 19.5 °C / 54% by day averages 17.6 °C / 66.8%, which is
below the curve. Score the mean and you see nothing. Step the hours and you
find 9 hours a night above it.

**2. A room that dries out every day does not accumulate mould.**
In that same bedroom the index stays near zero. The published decline is fast
for the first 6 dry hours, and that undoes each night's growth. So report
**growth hours** (hours above the curve) alongside the index. Growth hours
flag the condition early; the index tells you whether the surface is actually
losing ground.

**3. Shortcut models drift a long way from the published one.**
A common shortcut (a hand-built lookup curve plus linear growth) is in
`tools/compare/` for comparison. Over the simulator's 100 rooms:

| | Simplified shortcut | Published model |
|---|---|---|
| Critical RH at 12 °C | 83.8% | 80.9% |
| Rooms reaching visible mould (M3) | 0 | 5 |
| Severe damp rooms: peak index | about 4x lower (median) | |
| Borderline rooms that dry out daily: peak index | about 7x higher (median) | |

## What's in the repo

| Path | What |
|---|---|
| `js/vtt-model.mjs` | The model, JavaScript (ES module, no dependencies) |
| `python/vtt_hukka_viitanen.py` | The model, Python (stdlib only). Run it for a self test |
| `js/*.test.mjs` | Published-value checks, plus JS vs Python parity |
| `tools/daily-runner/` | Score a day of readings at a time, carrying state night to night |
| `tools/sign-datagen/` | Synthetic Nexelec SIGN sensor data with a known answer, plus a verifier |
| `tools/simulator/` | 100 rooms x 61 days of hourly data, to CSV and a Postgres load script |
| `tools/compare/` | Simplified shortcut vs published model, side by side |

## The tools

### Daily runner

```bash
python3 tools/daily-runner/vtt_daily.py
```

Wakes once a night, integrates the day's readings at whatever cadence they
arrive (gaps are capped at 3 hours), and returns the closing index, the rate
of change per day, growth hours, dew point and a `state` to pass back in
tomorrow. Two material tracks run side by side (very sensitive and medium
resistant by default).

### Sensor test data (Nexelec SIGN)

```bash
python3 tools/sign-datagen/generate_sign_data.py --gzip   # 100 devices, 30 days, ~20 s
python3 tools/sign-datagen/verify_fixture.py              # replays it through the daily runner
python3 tools/sign-datagen/to_csv.py --gzip               # CSV for handover
```

100 synthetic 7-in-1 sensors reporting every 10 minutes, 8 of which develop a
real damp fault (cold bedroom, clothes drying indoors, failed bathroom
extractor). Humidity is never dialled in as a number: each room carries a
moisture balance (people, cooking, showers in; ventilation out), and RH follows
from that and the air temperature. A faulty room is under-heated and
under-ventilated, and its RH climbs as a consequence.

The verifier replays all 424,245 uplinks and checks the result against the
generator's ground truth. It exits non-zero if the damp rooms don't separate:

```
separation (growth hours)  healthy worst 0.1 h     |  faulty best 588.1 h
separation (VTT peak)      healthy worst 0.0000    |  faulty best 4.0856
confusion                  8 true-positive, 92 true-negative, 0 false-positive, 0 false-negative
```

**Modelled, not measured:** the `data` hex is filler (don't run a real decoder
over it; use `decoded`), the IZIair codes are derived from CO2/TVOC thresholds,
and the radio metadata is plausible rather than captured. The committed
`sample-uplinks.jsonl` uses sequential dummy EUIs and repeating filler hex so
nothing in the repo looks like a real identifier.

### Simulator

```bash
node tools/simulator/generate.mjs        # ~1.5 s
```

100 rooms across a procedurally built estate, 61 winter days, hourly, written
to `tools/simulator/out/`:

| File | Rows | What |
|---|---|---|
| `spaces.csv` | 100 | Room definitions and scenario |
| `readings_hourly.csv` | 146,400 | Every reading, fully scored |
| `daily_summary.csv` | 6,100 | Per room per day |
| `weather_hourly.csv` | 1,464 | Outdoor temperature, RH, dew point |
| `vtt_events.csv` | 20 | Every M1 to M4 crossing, with timestamp |
| `load.sql` | | Tables and inserts for a fresh Postgres |

Exactly 5% of rooms are built to cross **visible mould (M3)** during the
window, from a clean start. They cross between day 39 and day 49. No other room
gets past M1 (the worst non-trigger room peaks at 0.81), so a consumer that
finds more or fewer than 5 is wrong, and a test can assert on that. The run is
deterministic: the same `--seed` gives byte-identical files. `load.sql` loads
into PostgreSQL 16 in about 2 seconds:

```bash
createdb vttsim && psql -d vttsim -v ON_ERROR_STOP=1 -f tools/simulator/out/load.sql
```

Options: `--spaces`, `--days`, `--start`, `--seed`, `--trigger-pct`,
`--trigger-m`, `--sensitive-class`, `--resistant-class`, `--out`.

### Compare

```bash
node tools/simulator/generate.mjs && node tools/compare/compare.mjs
```

Replays every simulated room under the simplified shortcut and the published
model, and prints the gap (the table in "Three things worth knowing" above).

## Tests

```bash
npm test
```

26 JavaScript tests (published-value checkpoints, the comparison replay, and a
JS vs Python parity check across all four sensitivity classes, agreeing to
1e-12 every hour), then the Python self test. Tested on Node 26 and
Python 3.14.

## How this was built

Every tool follows the same order, and the order matters:

```
 1. READ THE REAL THING        the papers, or the vendor's decoder.
          │                    never memory, never a datasheet summary
          ▼
 2. PORT IT FAITHFULLY         verbatim maths, checked against the source
          │
          ▼
 3. GENERATE DATA WITH         synthetic readings whose right answer
    A KNOWN ANSWER             is written down before scoring
          │
          ▼
 4. REPLAY AND CHECK           score it, compare to the answer,
          │                    fail loudly if it doesn't separate
          ▼
 5. SAY WHAT ISN'T PROVEN      below
```

Step 4 caught a bug in the generator, not the scorer: the "damp" rooms were
getting 3.5x the ventilation and drying out, because a positional tuple put
the wrong value in the ventilation slot. Fault settings are named fields now.

## What is and isn't proven

**Proven here.** The JS and Python models agree to 1e-12. Both reproduce the
published checkpoints (M1 in 10.4 days at 20 °C / 97%, against ~1.5 weeks
published; the Mmax bounds; the decline bands). Both synthetic fixtures
separate their seeded cases with no overlap.

**Not proven here.**
- **Room air is not the wall.** The model describes conditions at a surface.
  A room sensor measures the air, and cold spots (external walls, corners,
  behind furniture) run wetter than the room. Room readings will understate
  risk at those spots. Menneer et al. 2022 apply the model to domestic room
  data; read it before relying on room-level results.
- **The model was fitted on wood.** Other materials come in through the
  sensitivity classes, which is the published approach, not a measurement of
  your wall.
- **Synthetic data is synthetic.** The fixtures prove the tools behave; they
  are not a record of any real building.
- **A model, not an inspection.** Use it to prioritise where to look, not to
  declare a home free of damp or mould.

## Sources

1. Viitanen, H. & Ojanen, T. (2007). *Improved Model to Predict Mold Growth in
   Building Materials.* Buildings X Conference, ASHRAE.
   [PDF](https://web.ornl.gov/sci/buildings/conf-archive/2007%20B10%20papers/162_Viitanen.pdf)
2. Menneer, T. et al. (2022). *Modelling mould growth in domestic environments
   using relative humidity and temperature.* Building and Environment 208:108583.
   [PDF](https://www.ecehh.org/wp/wp-content/uploads/2023/06/Modelling_mould_growth.pdf)
3. Hukka, A. & Viitanen, H. (1999). *A mathematical model of mould growth on
   wooden material.* Wood Science and Technology 33:475-485.

## Licence

[MIT](LICENSE)
