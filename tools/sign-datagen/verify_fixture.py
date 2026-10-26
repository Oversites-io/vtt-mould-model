#!/usr/bin/env python3
"""Prove the generated fixture actually exercises the VTT scorer.

Reads the uplinks, replays them through the daily runner one day at a time per
device (carrying last night's state forward, as a nightly job would), then
checks what it found against the manifest's ground truth.

    python3 tools/sign-datagen/verify_fixture.py [--out-dir tools/sign-datagen/out]

A fixture that does not separate the seeded-faulty rooms from the healthy ones
is not a useful test fixture, so this exits non-zero when it fails to.

The scorer is IMPORTED from tools/daily-runner/vtt_daily.py, never copied: one
implementation of the maths.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VTT_PATH = REPO / "tools" / "daily-runner" / "vtt_daily.py"


def load_vtt():
    """Import the scorer from its hyphenated directory (not a package)."""
    spec = importlib.util.spec_from_file_location("vtt_daily", VTT_PATH)
    if spec is None or spec.loader is None:
        sys.exit(f"cannot load the VTT scorer at {VTT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: vtt_daily uses `from __future__ import annotations`,
    # and @dataclass resolves those string annotations by looking the defining
    # module up in sys.modules. Without this it finds None and blows up.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_uplinks(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def separation(results: list[dict], key: str) -> tuple[float, float]:
    healthy = [r[key] for r in results if not r["expected"]]
    faulty = [r[key] for r in results if r["expected"]]
    return max(healthy, default=0.0), min(faulty)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="tools/sign-datagen/out")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    data = out_dir / "uplinks.jsonl"
    if not data.exists():
        data = out_dir / "uplinks.jsonl.gz"
    if not data.exists():
        sys.exit(f"no uplinks found in {out_dir}: run generate_sign_data.py first")

    manifest = json.loads((out_dir / "manifest.json").read_text())
    expected = {d["name"]: d["expect_damp_issue"] for d in manifest["devices"]}
    if not any(expected.values()):
        sys.exit("manifest seeded no faulty devices: nothing to verify")

    vtt = load_vtt()

    # device -> day -> [HourlyReading]
    by_device: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    total = 0
    for up in read_uplinks(data):
        dec = up["decoded"]
        ts = datetime.fromtimestamp(up["ts"] / 1000, tz=timezone.utc)
        by_device[up["device"]["name"]][ts.date().isoformat()].append(
            vtt.HourlyReading(ts=ts, temp_c=dec["temperature"], rh_pct=dec["humidity"])
        )
        total += 1

    results = []
    for name, days in sorted(by_device.items()):
        state = vtt.VttState()
        growth_hours = 0.0
        peak = 0.0
        for day in sorted(days):
            res = vtt.run_daily_vtt(days[day], state)
            state = res.state
            growth_hours += res.growth_hours
            peak = max(peak, res.vtt_sensitive)
        results.append({
            "name": name,
            "vtt": round(state.vtt_sensitive, 4),
            "vtt_peak": round(peak, 4),
            "growth_hours": round(growth_hours, 1),
            "expected": expected.get(name, False),
        })

    # Separate on growth-hours (hours above the critical curve): the earliest
    # signal, and the one that flags a room before the index has had time to
    # climb. The index separation is printed alongside for comparison.
    worst_healthy, best_faulty = separation(results, "growth_hours")
    threshold = (worst_healthy + best_faulty) / 2
    for r in results:
        r["flagged"] = r["growth_hours"] > threshold

    tp = sum(1 for r in results if r["expected"] and r["flagged"])
    fp = sum(1 for r in results if not r["expected"] and r["flagged"])
    fn = sum(1 for r in results if r["expected"] and not r["flagged"])
    tn = sum(1 for r in results if not r["expected"] and not r["flagged"])

    print(f"\nReplayed {total:,} uplinks from {len(by_device)} devices "
          f"through the VTT daily runner.\n")
    print(f"{'device':<16}{'room':<14}{'growth h':>10}{'VTT end':>10}{'VTT peak':>10}"
          f"{'seeded':>9}{'found':>8}")
    print("-" * 77)
    rooms = {d["name"]: d["room_type"] for d in manifest["devices"]}
    for r in sorted(results, key=lambda x: -x["growth_hours"])[:12]:
        print(f"{r['name']:<16}{rooms.get(r['name'], ''):<14}"
              f"{r['growth_hours']:>10.1f}{r['vtt']:>10.4f}{r['vtt_peak']:>10.4f}"
              f"{('DAMP' if r['expected'] else '-'):>9}"
              f"{('DAMP' if r['flagged'] else '-'):>8}")
    if len(results) > 12:
        print(f"{'...':<16}{len(results) - 12} more, all below")

    vh, vf = separation(results, "vtt_peak")
    print(f"\nseparation (growth hours)  healthy worst {worst_healthy:.1f} h  |  "
          f"faulty best {best_faulty:.1f} h")
    print(f"separation (VTT peak)      healthy worst {vh:.4f}  |  faulty best {vf:.4f}")
    print(f"confusion                  {tp} true-positive, {tn} true-negative, "
          f"{fp} false-positive, {fn} false-negative")

    if fp or fn or best_faulty <= worst_healthy:
        print("\nFAIL: the fixture does not cleanly separate damp rooms "
              "from healthy ones.\n")
        sys.exit(1)
    print("\nPASS: every seeded damp room is separable from every healthy "
          "one, with no overlap.\n")


if __name__ == "__main__":
    main()
