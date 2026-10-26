# 3. Score it

Now the model does its job. Every room, every day, hour by hour.

```bash
python3 diy/score_csv.py readings.csv --out daily_scores.csv
```

Under the hood this calls the daily runner in `tools/daily-runner/vtt_daily.py`, which steps every reading through the published model. Nothing is averaged first.

## Never score the daily average

This is the one mistake that breaks everything.

```
  Cold bedroom, one day
  ─────────────────────
  night  14.5 °C   88% RH   ← above the mould line for 9 hours
  day    19.5 °C   54% RH   ← dry

  daily average   17.6 °C   66.8% RH   ← looks perfectly healthy
```

Score the average and the room is clean. Step the hours and you find 9 hours a night when mould can grow. Run `python3 tools/daily-runner/vtt_daily.py` to see it for yourself.

## What comes out

One row per room per day:

| Column | What it means |
|---|---|
| `vtt_sensitive` | The mould index, 0 to 6, for a very sensitive surface (the worst case). 1 = growth has started. 3 = visible |
| `vtt_resistant` | The same, for a medium resistant surface |
| `growth_hours` | Hours that day when conditions were right for mould. **The early warning** |
| `rate_per_day` and `trend` | Is the index going up, down, or holding? |
| `hours_used` | How many hours of data the day actually had. Low means gaps |
| `temp_mean_c`, `rh_mean_pct` | For context only. Don't make decisions on these |
| `flag` | ok / watch / act (chapter 4) |

## Two numbers, two jobs

**Growth hours** go up first. They tell you the conditions are wrong, even before any mould has had time to grow.

**The index** moves slowly. It tells you whether the surface is actually losing ground. A room that's damp at night but dries out every day may show plenty of growth hours and an index that stays near zero. That's the published model working properly: the first few dry hours undo most of each night's growth.

Report both. One without the other tells half the story.

## Run it every night

Use a state file, and the scores build up night by night:

```bash
python3 diy/score_csv.py today.csv --state state.json --out daily_scores.csv
```

- Each room's closing state is saved in `state.json` and carried into tomorrow. The model is cumulative, so this matters.
- Days already scored are skipped, so running it twice does no harm.
- Schedule it with cron, a GitHub Action, or whatever you already use.

In testing, running 30 days one night at a time gave exactly the same scores as running them all at once.

## Check it before you trust it

Run it on the test data first (see the [DIY README](README.md)). It should find all 8 damp rooms and nothing else. If your setup gets a different answer on the test data, the problem is in your pipeline, not the model.

**Next:** [4. Alert and act](04-alert-act.md)
