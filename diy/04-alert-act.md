# 4. Alert and act

A score nobody acts on is just a number. This chapter is about turning it into a message, and the message into a fix.

## Which clock?

Not every reading is an emergency. Three clocks:

| Clock | Question | Example | Mould? |
|---|---|---|---|
| **Real time** | Would you want a call at home, now? | Fire, a flood, someone stuck in a lift | No |
| **Near time** | Does someone need to act this week? | A bedroom going damp every night | **Yes** |
| **Transaction time** | What does the month tell us? | How many homes flagged, how fast were they fixed | **Yes** |

Mould is a near-time problem. Nobody needs a 2am alert for it. Somebody does need to know this week, while it's still cheap to fix.

## The flag

`score_csv.py` gives every room a flag each day:

```
  ok      nothing to do
  watch   growth hours that day >= 6         conditions are right for mould
  act     index >= 1 and still rising        the model says growth has started
```

These thresholds are starting points, not published values. Tune them with `--watch-hours` and `--act-index` once you've seen your own buildings.

In the test data, every damp room hit **watch** about 9 days before it hit **act**. That's your window.

## Who gets told

| Flag | Who | How |
|---|---|---|
| watch for 3 days running | Whoever looks after the home | A weekly list, not an alarm |
| act | The person who can send someone round | A job raised, with the room ID and the last 7 days attached |
| Monthly | Whoever reports on the estate | Counts, time to fix, repeat rooms |

Send the room ID, the flag, and a small chart of the last week. Nothing else. The chart does more than any paragraph.

## What to do about it

The model tells you where. People on the ground work out why. The usual causes:

- **Too cold.** The heating is off, broken, or too expensive to run.
- **Too wet.** Clothes drying indoors, cooking or showers with no extraction.
- **No air.** Trickle vents shut, extractor fan broken or switched off.
- **The building itself.** A leak, a cold bridge, failed insulation.

Most of these are quick to fix once you know. Some point to a tenant who can't afford to heat their home, which needs a different conversation, not a leaflet.

## Awaab's Law

Social landlords in England now have legal deadlines to investigate and fix damp and mould hazards. Phase 2, from 30 November 2026, extends the deadlines to more hazards, including excess cold and excess heat. The same temperature readings you've just collected cover those too.

A sensor doesn't replace a report from a tenant. It means you might hear about it sooner.

## Be honest about the limits

- It measures the air, not the wall. Cold corners run wetter than the room.
- The model was fitted on wood. Other materials come in through the sensitivity classes.
- It's a model, not an inspection. Use it to choose where to look first, never to say a home is clear.

## That's the system

```
  sensor ──▶ one CSV ──▶ nightly score ──▶ watch / act ──▶ someone fixes it
```

If you build it, I'd like to hear what you found. Open an issue in this repo.
