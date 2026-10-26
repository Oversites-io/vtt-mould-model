#!/usr/bin/env python3
"""Turn sensor messages (JSON lines) into the DIY common CSV.

    python3 diy/from_uplinks.py IN.jsonl[.gz] [--out readings.csv]
        [--ts-field ts] [--room-field device.name]
        [--temp-field decoded.temperature] [--rh-field decoded.humidity]

One JSON object per line in, one row per reading out:

    ts,room_id,temp_c,rh_pct
    2026-01-06T00:01:38Z,Nexelec-00003,18.2,62.9

Fields are dot paths into each message, so the same script works for most
network servers. The defaults match tools/sign-datagen/sample-uplinks.jsonl.
Examples for other sources are in diy/02-one-place.md.

Timestamps can be epoch milliseconds, epoch seconds or ISO 8601. They are
written out as UTC. Messages missing any of the four fields are skipped and
counted, never guessed.

Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


def get_path(obj, path: str):
    """Follow a dot path like 'uplink_message.decoded_payload.temperature'."""
    for key in path.split("."):
        if isinstance(obj, dict) and key in obj:
            obj = obj[key]
        else:
            return None
    return obj


def parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Above ~1e11 it can only be milliseconds (year 5138 in seconds).
        seconds = value / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return parse_ts(int(text))
        # Python before 3.11 doesn't accept a trailing Z, and some servers
        # send more than 6 fractional digits. Normalise both.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if "." in text:
            head, _, rest = text.partition(".")
            digits = "".join(c for c in rest if c.isdigit())
            tz = rest[len(digits):]
            text = f"{head}.{digits[:6]}{tz}"
        try:
            ts = datetime.fromisoformat(text)
        except ValueError:
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


def to_number(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def read_lines(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield line


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, default=Path("readings.csv"))
    ap.add_argument("--ts-field", default="ts")
    ap.add_argument("--room-field", default="device.name")
    ap.add_argument("--temp-field", default="decoded.temperature")
    ap.add_argument("--rh-field", default="decoded.humidity")
    args = ap.parse_args()

    rows, skipped, bad_json = [], 0, 0
    for line in read_lines(args.input):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            bad_json += 1
            continue
        ts = parse_ts(get_path(msg, args.ts_field))
        room = get_path(msg, args.room_field)
        temp = to_number(get_path(msg, args.temp_field))
        rh = to_number(get_path(msg, args.rh_field))
        if ts is None or room in (None, "") or temp is None or rh is None:
            skipped += 1
            continue
        rows.append((ts, str(room), temp, rh))

    rows.sort(key=lambda r: (r[1], r[0]))
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "room_id", "temp_c", "rh_pct"])
        for ts, room, temp, rh in rows:
            w.writerow([ts.strftime("%Y-%m-%dT%H:%M:%SZ"), room, temp, rh])

    rooms = len({r[1] for r in rows})
    print(f"{len(rows)} readings from {rooms} rooms -> {args.out}", file=sys.stderr)
    if skipped or bad_json:
        print(f"skipped {skipped} messages missing a field, {bad_json} unreadable lines", file=sys.stderr)


if __name__ == "__main__":
    main()
