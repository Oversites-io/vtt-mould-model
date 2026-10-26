#!/usr/bin/env python3
"""Score the DIY common CSV, room by room, day by day.

    python3 diy/score_csv.py readings.csv [--out daily_scores.csv]
        [--state state.json] [--watch-hours 6] [--act-index 1.0]

Input: the common CSV from diy/01-readings.md (ts,room_id,temp_c,rh_pct).
Output: one row per room per day, with the mould index, growth hours, trend
and a simple flag (ok / watch / act).

Run it every night with the same --state file. Each room's closing state is
saved there and carried into the next run, and days already scored are
skipped, so running it twice on the same data changes nothing.

Days are UTC calendar days. Every reading is stepped hour by hour through
tools/daily-runner/vtt_daily.py, never averaged first.

The flag thresholds are starting points to tune, not published values:
    watch  growth hours that day >= --watch-hours  (conditions are right for mould)
    act    index >= --act-index and still rising   (the model says growth has started)

Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "daily-runner"))

from vtt_daily import HourlyReading, VttState, run_daily_vtt  # noqa: E402

OUT_FIELDS = [
    "room_id", "date", "flag", "vtt_sensitive", "vtt_resistant", "rate_per_day",
    "trend", "growth_hours", "hours_used", "temp_mean_c", "rh_mean_pct", "label",
]


def parse_ts(text: str) -> datetime:
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    ts = datetime.fromisoformat(text)
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def load_readings(path: Path) -> dict[str, dict[str, list[HourlyReading]]]:
    """room_id -> UTC date -> readings."""
    rooms: dict[str, dict[str, list[HourlyReading]]] = defaultdict(lambda: defaultdict(list))
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = {"ts", "room_id", "temp_c", "rh_pct"} - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"{path}: missing column(s) {', '.join(sorted(missing))}")
        for row in reader:
            try:
                ts = parse_ts(row["ts"])
                r = HourlyReading(ts=ts, temp_c=float(row["temp_c"]), rh_pct=float(row["rh_pct"]))
            except (ValueError, TypeError):
                continue
            rooms[row["room_id"]][ts.date().isoformat()].append(r)
    return rooms


def load_state(path: Path | None) -> dict:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def flag_for(result, watch_hours: float, act_index: float) -> str:
    if result.vtt_sensitive >= act_index and result.trend == "rising":
        return "act"
    if result.growth_hours >= watch_hours:
        return "watch"
    return "ok"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, default=Path("daily_scores.csv"))
    ap.add_argument("--state", type=Path, default=None, help="JSON file carrying each room's state between runs")
    ap.add_argument("--watch-hours", type=float, default=6.0)
    ap.add_argument("--act-index", type=float, default=1.0)
    args = ap.parse_args()

    rooms = load_readings(args.input)
    state = load_state(args.state)

    new_rows = []
    for room_id in sorted(rooms):
        saved = state.get(room_id, {})
        prev = VttState(**saved["state"]) if "state" in saved else VttState()
        last_date = saved.get("last_date", "")
        for date in sorted(rooms[room_id]):
            if date <= last_date:
                continue  # already scored on an earlier run
            res = run_daily_vtt(rooms[room_id][date], prev)
            new_rows.append({
                "room_id": room_id,
                "date": date,
                "flag": flag_for(res, args.watch_hours, args.act_index),
                "vtt_sensitive": res.vtt_sensitive,
                "vtt_resistant": res.vtt_resistant,
                "rate_per_day": res.rate_per_day,
                "trend": res.trend,
                "growth_hours": res.growth_hours,
                "hours_used": res.hours_used,
                "temp_mean_c": res.temp_mean_c,
                "rh_mean_pct": res.rh_mean_pct,
                "label": res.label,
            })
            prev, last_date = res.state, date
        state[room_id] = {"last_date": last_date, "state": asdict(prev)}

    # Append when carrying state, so the output builds up night by night.
    append = args.state is not None and args.out.exists()
    with open(args.out, "a" if append else "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        if not append:
            w.writeheader()
        w.writerows(new_rows)

    if args.state:
        args.state.write_text(json.dumps(state, indent=2), encoding="utf-8")

    counts = defaultdict(int)
    latest = {}
    for row in new_rows:
        latest[row["room_id"]] = row["flag"]
    for flag in latest.values():
        counts[flag] += 1
    print(
        f"{len(new_rows)} room-days scored -> {args.out}   "
        f"latest: {counts['act']} act, {counts['watch']} watch, {counts['ok']} ok",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
