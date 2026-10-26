#!/usr/bin/env python3
"""Synthetic Nexelec SIGN (7-in-1) uplinks, LNS-shaped and decoder-shaped.

Produces a test fixture for the VTT mould scorer: N devices reporting every
10 minutes, of which a small percentage develop a genuine damp problem that
the scorer should catch. Ships a ground-truth manifest so a test can assert
"the scorer flagged exactly the rooms that were actually bad".

    python3 tools/sign-datagen/generate_sign_data.py --devices 100 --days 30
    python3 tools/sign-datagen/verify_fixture.py        # proves it works

Stdlib only.

════════════════════════════════════════════════════════════════════════════
AUTHENTICITY: what is real and what is modelled
════════════════════════════════════════════════════════════════════════════
Real: the payload keys, units and decimal places as configured for this
device (manufacturer 'Nexelec', model 'SIGN', short_name '7 in 1',
decoder X565):

    payload key        alias           unit      dp
    temperature        Temperature     °C        1
    humidity           Humidity        %RH       1
    co2                CO₂             ppm       1
    tvoc               TVOC            µg/m³     1
    luminosity         Luminosity      lux       1
    averageNoise       Sound (avg)     dB        1
    peakNoise          Sound (peak)    dB        1
    occupancyRate      Occupancy rate  %         1
    iziairGlobal_code  IZIair overall  code      -
    iziairCo2_code     IZIair CO₂      code      -

The device is battery / LoRaWAN Class A, configured default uplink 1800 s.
This generator defaults to 600 s because that is what was asked for; 10 min
is a legitimate configured period for the X5 family but is aggressive for a
battery part, so battery life in the real world would be well short of what
the default interval gives. Nothing here models battery drain.

Modelled, not measured (flagged honestly):
  * `data` is random hex of a plausible length. Nexelec's X565 byte layout is
    NOT implemented: emitting bytes that look real but decode to nonsense
    would be worse than obviously-synthetic filler. Do not run a real decoder
    over it. The `decoded` object is the payload that matters.
  * IZIair codes are derived here from CO₂/TVOC with the thresholds below.
    Nexelec's real index is proprietary; treat the codes as plausible, not
    authoritative.
  * RSSI/SNR/fcnt/gateway are plausible LoRaWAN telemetry, not a replay.

════════════════════════════════════════════════════════════════════════════
THE PHYSICS: why the damp rooms actually go damp
════════════════════════════════════════════════════════════════════════════
Humidity is NOT dialled in as a number. Each room carries a moisture mass
(mixing ratio, g/kg dry air) that is driven by:

    moisture in   ← occupants breathing, cooking, showering, drying clothes
    moisture out  ← ventilation exchange with outdoor air (air changes/hour)

and RH is then computed from that moisture and the room's air temperature
via the Magnus saturation curve. This matters: it means a cold, poorly
ventilated room reaches high RH *for the right reason* (the same moisture in
colder air is a higher relative humidity), which is exactly the condensation
mechanism the VTT model exists to detect. A fault room here is under-heated
and under-ventilated; its RH climbs as a consequence, not by decree.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Catalogue facts (see module docstring) ──────────────────────────────────

MANUFACTURER = "Nexelec"
MODEL = "SIGN"
SHORT_NAME = "7 in 1"
DECODER_REF = "X565"

# IZIair bands: MODELLED, see docstring. 1 = excellent … 5 = poor.
IZIAIR_CO2_BANDS = [(800, 1), (1000, 2), (1400, 3), (2000, 4), (10**9, 5)]
IZIAIR_TVOC_BANDS = [(150, 1), (300, 2), (600, 3), (1000, 4), (10**9, 5)]


def _band(value: float, bands: list[tuple[float, int]]) -> int:
    for limit, code in bands:
        if value < limit:
            return code
    return bands[-1][1]


# ── Identifiers ─────────────────────────────────────────────────────────────
# DevEUIs and the filler payload hex are derived by hash, not drawn from the
# PRNG. Two reasons, and neither is cosmetic:
#   * a device's EUI is then a pure function of (seed, index), stable no
#     matter what order devices are built in or how many draws happened first,
#     which is what you want from a reproducible fixture;
#   * generating identifier-shaped hex from a non-cryptographic PRNG is the
#     shape of a real security defect elsewhere, and static analysis is right
#     to flag it. Not worth arguing with for values that should be derived
#     anyway.
# `random` is still used freely below for the SIMULATION: sensor noise,
# occupancy draws, radio jitter. That is modelling, not secret-generation, and
# a seeded PRNG is exactly the right tool for it.


def stable_hex(*parts: object, nbytes: int) -> str:
    """Deterministic hex derived from the given parts."""
    material = ":".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    while len(digest) < nbytes * 2:
        digest += hashlib.sha256(digest.encode("utf-8")).hexdigest()
    return digest[: nbytes * 2]


# ── Psychrometrics ──────────────────────────────────────────────────────────

P_ATM_HPA = 1013.25


def sat_vapour_pressure_hpa(temp_c: float) -> float:
    """Saturation vapour pressure over water (Magnus/Tetens), hPa."""
    return 6.112 * math.exp(17.67 * temp_c / (temp_c + 243.5))


def rh_to_mixing_ratio(temp_c: float, rh_pct: float) -> float:
    """RH (%) → mixing ratio (g water / kg dry air)."""
    e = (rh_pct / 100.0) * sat_vapour_pressure_hpa(temp_c)
    e = min(e, P_ATM_HPA - 0.01)
    return 622.0 * e / (P_ATM_HPA - e)


def mixing_ratio_to_rh(temp_c: float, w_g_per_kg: float) -> float:
    """Mixing ratio (g/kg) → RH (%), clamped to a physical 1 to 100."""
    w = max(w_g_per_kg, 0.0) / 622.0
    e = w * P_ATM_HPA / (1.0 + w)
    rh = 100.0 * e / sat_vapour_pressure_hpa(temp_c)
    return max(1.0, min(100.0, rh))


# ── Room archetypes ─────────────────────────────────────────────────────────


@dataclass
class RoomType:
    name: str
    # occupancy probability by hour-of-day (24 values, 0..1)
    occupancy: list[float]
    # peak moisture generation when occupied, grams/hour
    moisture_g_per_hour: float
    # healthy ventilation, air changes per hour
    ach_healthy: float
    # heating setpoint when "on"
    setpoint_c: float
    volume_m3: float


_NIGHT = [0.95] * 7 + [0.4] + [0.05] * 14 + [0.5, 0.9]
_EVENING = [0.05] * 7 + [0.5, 0.3] + [0.1] * 8 + [0.4, 0.8, 0.9, 0.9, 0.7, 0.3, 0.1]
_KITCHEN = [0.02] * 6 + [0.3, 0.5, 0.2] + [0.08] * 8 + [0.35, 0.6, 0.45, 0.2, 0.1, 0.05, 0.03]
_BATH = [0.02] * 6 + [0.35, 0.4, 0.15] + [0.05] * 12 + [0.2, 0.3, 0.15, 0.05]
_HALL = [0.02] * 6 + [0.15, 0.2, 0.12] + [0.05] * 9 + [0.15, 0.2, 0.18, 0.12, 0.06, 0.03]

# Moisture rates are the SUSTAINED average while the room is in use, not the
# instantaneous peak of a running shower or a boiling pan. Tuned so healthy
# rooms sit under the mould curve nearly always, as healthy rooms in real
# homes do, because a fixture whose healthy bathrooms
# read damp would drown the signal it exists to test.
ROOM_TYPES = [
    RoomType("bedroom", _NIGHT, 48.0, 0.8, 18.0, 32.0),
    RoomType("living_room", _EVENING, 60.0, 1.0, 21.0, 45.0),
    RoomType("kitchen", _KITCHEN, 150.0, 1.7, 20.0, 30.0),
    RoomType("bathroom", _BATH, 170.0, 2.4, 21.0, 14.0),
    RoomType("hall", _HALL, 25.0, 1.1, 18.0, 20.0),
]

# Rooms where a damp fault is plausible. Halls and kitchens are excluded:
# a fault there reads as a ventilation defect rather than the cold-surface
# condensation story the VTT model is scoring.
FAULT_ELIGIBLE = {"bedroom", "living_room", "bathroom"}


@dataclass
class FaultProfile:
    """How a room goes bad. None of these touch RH directly: they starve the
    room of heat and ventilation and let the moisture balance do the rest."""

    onset_day: float
    ramp_days: float
    # heating deficit at full severity, °C below setpoint
    heat_deficit_c: float
    # ventilation remaining at full severity, as a fraction of healthy ACH
    ach_fraction: float
    # extra moisture at full severity, g/h (e.g. drying clothes indoors)
    extra_moisture_g_per_hour: float
    label: str

    def severity(self, day: float) -> float:
        """0 before onset, ramping to 1 over ramp_days."""
        if day < self.onset_day:
            return 0.0
        return min(1.0, (day - self.onset_day) / max(self.ramp_days, 0.1))


# Named, never positional. An earlier version used bare tuples and the
# ach_fraction slot silently picked up the wrong element, which INVERTED the
# fault: the "damp" room got 3.5x the ventilation and dried out. The verifier
# caught it, but a dict is why it cannot happen again.
FAULT_KINDS = [
    {
        "kind": "cold_bridge_bedroom",
        "rooms": {"bedroom"},
        "heat_deficit_c": 4.5,
        "ach_fraction": 0.30,
        "extra_moisture_g_per_hour": 70.0,
        "label": "Under-heated bedroom, external wall, trickle vent shut",
    },
    {
        "kind": "unventilated_drying",
        "rooms": {"bedroom", "living_room"},
        "heat_deficit_c": 3.5,
        "ach_fraction": 0.25,
        "extra_moisture_g_per_hour": 260.0,
        "label": "Clothes dried indoors, window kept shut",
    },
    {
        "kind": "failed_extractor",
        "rooms": {"bathroom"},
        "heat_deficit_c": 3.0,
        "ach_fraction": 0.20,
        "extra_moisture_g_per_hour": 150.0,
        "label": "Extractor fan failed, moisture not cleared after use",
    },
]


# ── Devices ─────────────────────────────────────────────────────────────────


@dataclass
class Device:
    index: int
    name: str
    dev_eui: str
    room: RoomType
    site: str
    fault: FaultProfile | None = None
    # running state
    w: float = 8.0            # mixing ratio, g/kg
    temp: float = 19.0
    co2: float = 450.0
    tvoc: float = 90.0
    fcnt: int = field(default_factory=lambda: random.randint(1, 4000))

    @property
    def is_faulty(self) -> bool:
        return self.fault is not None


SITES = [
    "Rowan House", "Elmfield Court", "Beech Rise", "Alder Walk", "Hazel Gardens",
]


def make_devices(n: int, fault_rate: float, rng: random.Random,
                 seed_tag: int) -> list[Device]:
    devices: list[Device] = []
    for i in range(1, n + 1):
        room = ROOM_TYPES[(i - 1) % len(ROOM_TYPES)]
        # 70B3D5 is the real LoRa Alliance OUI prefix; the rest is derived.
        eui = "70B3D5" + stable_hex(seed_tag, "eui", i, nbytes=5).upper()
        devices.append(
            Device(
                index=i,
                name=f"Nexelec-{i:05d}",
                dev_eui=eui,
                room=room,
                site=SITES[(i - 1) % len(SITES)],
                w=rng.uniform(7.0, 9.0),
                temp=rng.uniform(17.5, 20.5),
            )
        )

    eligible = [d for d in devices if d.room.name in FAULT_ELIGIBLE]
    n_faulty = max(1, round(len(devices) * fault_rate))
    n_faulty = min(n_faulty, len(eligible))
    for d in rng.sample(eligible, n_faulty):
        # Only fault kinds that make sense in this room. An "under-heated
        # bedroom" label on a living room reads as a generator bug to anyone
        # reading the handover, and rightly so.
        candidates = [k for k in FAULT_KINDS if d.room.name in k["rooms"]]
        spec = candidates[rng.randrange(len(candidates))]
        d.fault = FaultProfile(
            onset_day=rng.uniform(1.0, 4.0),
            ramp_days=rng.uniform(3.0, 7.0),
            heat_deficit_c=spec["heat_deficit_c"] * rng.uniform(0.85, 1.15),
            ach_fraction=spec["ach_fraction"],
            extra_moisture_g_per_hour=spec["extra_moisture_g_per_hour"],
            label=spec["label"],
        )
        d.fault.kind = spec["kind"]  # type: ignore[attr-defined]
    return devices


# ── Outdoor conditions: a cold, damp UK winter ──────────────────────────────


def outdoor(ts: datetime, day_index: float, rng: random.Random) -> tuple[float, float]:
    """(temp_c, rh_pct) outdoors: seasonal drift + diurnal swing + weather."""
    hour = ts.hour + ts.minute / 60.0
    seasonal = 7.0 - 2.5 * math.cos(2 * math.pi * day_index / 60.0)
    diurnal = 3.0 * math.sin(2 * math.pi * (hour - 9.0) / 24.0)
    weather = 2.2 * math.sin(2 * math.pi * day_index / 5.3)
    temp = seasonal + diurnal + weather + rng.gauss(0, 0.35)
    rh = 84.0 - 1.6 * diurnal + 5.0 * math.sin(2 * math.pi * day_index / 4.1)
    return temp, max(55.0, min(99.0, rh + rng.gauss(0, 2.0)))


def daylight_lux(ts: datetime) -> float:
    hour = ts.hour + ts.minute / 60.0
    if hour < 7.8 or hour > 16.6:
        return 0.0
    frac = (hour - 7.8) / (16.6 - 7.8)
    return 480.0 * math.sin(math.pi * frac)


# ── One step of the room model ──────────────────────────────────────────────


def step_room(d: Device, ts: datetime, day_index: float, dt_hours: float,
              rng: random.Random) -> dict:
    hour = ts.hour
    room = d.room
    sev = d.fault.severity(day_index) if d.fault else 0.0

    out_t, out_rh = outdoor(ts, day_index, rng)
    w_out = rh_to_mixing_ratio(out_t, out_rh)

    # occupancy
    p_occ = room.occupancy[hour]
    if ts.weekday() >= 5 and room.name in ("living_room", "kitchen"):
        p_occ = min(1.0, p_occ * 1.35)
    occupied = rng.random() < p_occ
    occ_rate = max(0.0, min(100.0, rng.gauss(p_occ * 100.0, 9.0)))

    # temperature: heating schedule, minus any fault deficit
    heating_on = (6 <= hour < 9) or (16 <= hour < 22)
    setpoint = room.setpoint_c if heating_on else room.setpoint_c - 3.0
    setpoint -= sev * d.fault.heat_deficit_c if d.fault else 0.0
    # pulled toward setpoint, dragged toward outdoor by the fabric
    d.temp += (setpoint - d.temp) * 0.28 * dt_hours * (1.0 if heating_on else 0.5)
    d.temp += (out_t - d.temp) * 0.05 * dt_hours
    d.temp += rng.gauss(0, 0.06)

    # moisture balance
    ach = room.ach_healthy
    if d.fault:
        ach *= 1.0 - sev * (1.0 - d.fault.ach_fraction)
    gen_g = (room.moisture_g_per_hour if occupied else room.moisture_g_per_hour * 0.06)
    if d.fault:
        gen_g += sev * d.fault.extra_moisture_g_per_hour
    air_mass_kg = room.volume_m3 * 1.2
    d.w += (gen_g * dt_hours) / air_mass_kg
    d.w += (w_out - d.w) * min(1.0, ach * dt_hours)
    d.w = max(1.0, min(28.0, d.w))

    rh = mixing_ratio_to_rh(d.temp, d.w)
    rh = max(1.0, min(100.0, rh + rng.gauss(0, 0.45)))

    # CO2: occupants in, ventilation out
    co2_gen = 19000.0 if occupied else 0.0
    d.co2 += (co2_gen * dt_hours) / air_mass_kg
    d.co2 += (variable_outdoor_co2(rng) - d.co2) * min(1.0, ach * dt_hours)
    d.co2 = max(400.0, min(5000.0, d.co2 + rng.gauss(0, 6.0)))

    # TVOC: tracks occupancy and poor ventilation
    tvoc_target = 70.0 + (180.0 if occupied else 0.0) + sev * 90.0
    d.tvoc += (tvoc_target - d.tvoc) * 0.3 * dt_hours * 6
    d.tvoc = max(20.0, min(3000.0, d.tvoc + rng.gauss(0, 8.0)))

    # light and sound
    lux = daylight_lux(ts)
    if occupied and lux < 90:
        lux += rng.uniform(110, 260)
    lux = max(0.0, lux + rng.gauss(0, 4.0))

    base_noise = 31.0 + (14.0 if occupied else 0.0)
    avg_noise = max(25.0, rng.gauss(base_noise, 2.4))
    peak_noise = avg_noise + abs(rng.gauss(9.0, 4.5))

    return {
        "temperature": round(d.temp, 1),
        "humidity": round(rh, 1),
        "co2": round(d.co2, 1),
        "tvoc": round(d.tvoc, 1),
        "luminosity": round(lux, 1),
        "averageNoise": round(avg_noise, 1),
        "peakNoise": round(min(peak_noise, 120.0), 1),
        "occupancyRate": round(occ_rate, 1),
        "iziairCo2_code": _band(d.co2, IZIAIR_CO2_BANDS),
        "iziairGlobal_code": max(
            _band(d.co2, IZIAIR_CO2_BANDS), _band(d.tvoc, IZIAIR_TVOC_BANDS)
        ),
    }


def variable_outdoor_co2(rng: random.Random) -> float:
    return rng.gauss(430.0, 8.0)


# ── LNS envelope ────────────────────────────────────────────────────────────

_DR = ["SF7 BW125 4/5", "SF8 BW125 4/5", "SF9 BW125 4/5", "SF10 BW125 4/5"]


def uplink(d: Device, ts: datetime, decoded: dict, seqno: int,
           rng: random.Random) -> dict:
    """A decoded LoRaWAN uplink, shaped the way the LNS hands it to the webhook.

    `data` is filler (see the module docstring). `decoded` is the real payload.
    """
    d.fcnt = (d.fcnt + 1) % 65536
    n_bytes = rng.choice([11, 11, 11, 12])
    return {
        "cmd": "rx",
        "seqno": seqno,
        "EUI": d.dev_eui,
        "ts": int(ts.timestamp() * 1000),
        "ack": False,
        "fcnt": d.fcnt,
        "port": 1,
        "freq": rng.choice([868100000, 868300000, 868500000, 867100000]),
        "dr": rng.choice(_DR),
        "rssi": int(rng.gauss(-104, 9)),
        "snr": round(rng.gauss(6.5, 3.2), 1),
        "toa": rng.choice([46, 61, 82, 113]),
        "data": stable_hex(d.dev_eui, d.fcnt, int(ts.timestamp()), nbytes=n_bytes),
        "decoded": decoded,
        "device": {
            "name": d.name,
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "short_name": SHORT_NAME,
            "decoder": DECODER_REF,
            "site": d.site,
            "room_type": d.room.name,
        },
    }


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--devices", type=int, default=100)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--interval-mins", type=int, default=10)
    ap.add_argument("--fault-rate", type=float, default=0.08,
                    help="fraction of devices that develop a damp problem")
    ap.add_argument("--packet-loss", type=float, default=0.018,
                    help="fraction of uplinks dropped, as LoRaWAN does")
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--start", type=str, default="2026-01-06T00:00:00Z")
    ap.add_argument("--out-dir", type=str, default="tools/sign-datagen/out")
    ap.add_argument("--gzip", action="store_true", help="write .jsonl.gz")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
    dt_hours = args.interval_mins / 60.0
    steps = int(args.days * 24 * 60 / args.interval_mins)

    devices = make_devices(args.devices, args.fault_rate, rng, args.seed)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / ("uplinks.jsonl.gz" if args.gzip else "uplinks.jsonl")
    opener = (lambda p: gzip.open(p, "wt", encoding="utf-8")) if args.gzip else (
        lambda p: open(p, "w", encoding="utf-8")
    )

    seqno = rng.randint(100000, 999999)
    written = dropped = 0

    with opener(data_path) as fh:
        for step in range(steps):
            ts = start + timedelta(minutes=args.interval_mins * step)
            day_index = step * dt_hours / 24.0
            for d in devices:
                decoded = step_room(d, ts, day_index, dt_hours, rng)
                if rng.random() < args.packet_loss:
                    dropped += 1
                    continue
                seqno += 1
                # jitter: devices do not all transmit on the same second
                jitter = timedelta(seconds=rng.randint(0, args.interval_mins * 60 - 1))
                fh.write(json.dumps(uplink(d, ts + jitter, decoded, seqno, rng)) + "\n")
                written += 1

    end = start + timedelta(minutes=args.interval_mins * steps)
    manifest = {
        "generated_by": "tools/sign-datagen/generate_sign_data.py",
        "synthetic": True,
        "note": (
            "Synthetic test fixture. Not real telemetry. The 'data' hex is filler "
            "and is NOT a valid Nexelec X565 encoding. Do not run a real decoder "
            "over it; use the 'decoded' object."
        ),
        "seed": args.seed,
        "device_model": {
            "manufacturer": MANUFACTURER, "model": MODEL,
            "short_name": SHORT_NAME, "decoder": DECODER_REF,
        },
        "window": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "days": args.days,
            "interval_mins": args.interval_mins,
        },
        "counts": {
            "devices": len(devices),
            "uplinks_written": written,
            "uplinks_dropped": dropped,
            "faulty_devices": sum(1 for d in devices if d.is_faulty),
        },
        "devices": [
            {
                "name": d.name,
                "dev_eui": d.dev_eui,
                "site": d.site,
                "room_type": d.room.name,
                "expect_damp_issue": d.is_faulty,
                "fault": (
                    {
                        "kind": getattr(d.fault, "kind", None),
                        "label": d.fault.label,
                        "onset_day": round(d.fault.onset_day, 2),
                        "ramp_days": round(d.fault.ramp_days, 2),
                    }
                    if d.fault else None
                ),
            }
            for d in devices
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"devices          {len(devices)}  ({manifest['counts']['faulty_devices']} seeded faulty)")
    print(f"window           {args.days}d @ {args.interval_mins}min")
    print(f"uplinks written  {written:,}  (dropped {dropped:,} to packet loss)")
    print(f"data             {data_path}  ({data_path.stat().st_size/1e6:.1f} MB)")
    print(f"manifest         {out_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
