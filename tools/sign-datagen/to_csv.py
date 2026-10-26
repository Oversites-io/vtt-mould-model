#!/usr/bin/env python3
"""Flatten generated uplinks into CSV for handover.

    python3 tools/sign-datagen/to_csv.py [--out-dir tools/sign-datagen/out] [--gzip]

Writes two files beside the source data:

  readings.csv   one row per uplink, in time order
  devices.csv    one row per device: the manifest's ground truth, including
                 which rooms were seeded damp and why

Measure columns keep the decoder's exact payload keys (`temperature`,
`averageNoise`, `iziairGlobal_code`, …) rather than being renamed to
something friendlier. The point of this fixture is to look like what comes
out of the pipeline, and renaming the columns here would mean anyone building
against the CSV builds against names the real decoder never emits. Units are
in the README and in units.csv.

Streams the input, so memory stays flat regardless of file size.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

# Decoder payload keys.
MEASURES = [
    "temperature", "humidity", "co2", "tvoc", "luminosity",
    "averageNoise", "peakNoise", "occupancyRate",
    "iziairGlobal_code", "iziairCo2_code",
]

UNITS = {
    "temperature": "°C", "humidity": "%RH", "co2": "ppm", "tvoc": "µg/m³",
    "luminosity": "lux", "averageNoise": "dB", "peakNoise": "dB",
    "occupancyRate": "%", "iziairGlobal_code": "code (1 best, 5 worst)",
    "iziairCo2_code": "code (1 best, 5 worst)",
}

CONTEXT = ["device_name", "dev_eui", "site", "room_type", "ts"]
RADIO = ["fcnt", "port", "freq_hz", "dr", "rssi_dbm", "snr_db", "toa_ms"]


def read_uplinks(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="tools/sign-datagen/out")
    ap.add_argument("--gzip", action="store_true", help="write .csv.gz")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    src = out_dir / "uplinks.jsonl"
    if not src.exists():
        src = out_dir / "uplinks.jsonl.gz"
    if not src.exists():
        raise SystemExit(f"no uplinks in {out_dir}: run generate_sign_data.py first")

    manifest = json.loads((out_dir / "manifest.json").read_text())

    suffix = ".csv.gz" if args.gzip else ".csv"
    opener = (lambda p: gzip.open(p, "wt", newline="", encoding="utf-8")) if args.gzip \
        else (lambda p: open(p, "w", newline="", encoding="utf-8"))

    # ── readings ──
    readings_path = out_dir / f"readings{suffix}"
    rows = 0
    with opener(readings_path) as fh:
        w = csv.writer(fh)
        w.writerow(CONTEXT + MEASURES + RADIO)
        for u in read_uplinks(src):
            d, dec = u["device"], u["decoded"]
            ts = datetime.fromtimestamp(u["ts"] / 1000, tz=timezone.utc)
            w.writerow(
                [d["name"], u["EUI"], d["site"], d["room_type"],
                 ts.isoformat().replace("+00:00", "Z")]
                + [dec.get(k) for k in MEASURES]
                + [u["fcnt"], u["port"], u["freq"], u["dr"],
                   u["rssi"], u["snr"], u["toa"]]
            )
            rows += 1

    # ── devices (ground truth) ──
    devices_path = out_dir / f"devices{suffix}"
    with opener(devices_path) as fh:
        w = csv.writer(fh)
        w.writerow(["device_name", "dev_eui", "site", "room_type",
                    "expect_damp_issue", "fault_kind", "fault_label",
                    "fault_onset_day", "fault_ramp_days"])
        for d in manifest["devices"]:
            f = d.get("fault") or {}
            w.writerow([d["name"], d["dev_eui"], d["site"], d["room_type"],
                        "yes" if d["expect_damp_issue"] else "no",
                        f.get("kind", ""), f.get("label", ""),
                        f.get("onset_day", ""), f.get("ramp_days", "")])

    # ── units key ──
    units_path = out_dir / "units.csv"
    with open(units_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["column", "unit"])
        for k in MEASURES:
            w.writerow([k, UNITS[k]])

    for p, label in ((readings_path, f"{rows:,} rows"),
                     (devices_path, f"{len(manifest['devices'])} devices"),
                     (units_path, "measure units")):
        print(f"{p}  ({p.stat().st_size/1e6:.1f} MB)  {label}")


if __name__ == "__main__":
    main()
