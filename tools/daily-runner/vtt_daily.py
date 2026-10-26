"""VTT mould index: standalone daily runner (Python).

Wake it once a night, hand it the day's readings, and it integrates them hour
by hour through the published Hukka-Viitanen model
(python/vtt_hukka_viitanen.py, imported, never copied), carrying the previous
night's closing state forward.

Why the loop matters (do not "simplify" it to a daily mean):
    VTT is a threshold integrator. Growth only happens while RH is above the
    temperature-dependent critical curve. A bedroom at 88% RH for 9 cold night
    hours and 54% for 15 day hours has a daily mean of ~67%, below every
    critical threshold, so a mean-based run scores zero growth on exactly the
    overnight-condensation case the model exists to catch.

Stdlib only. Run:  python3 tools/daily-runner/vtt_daily.py
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from vtt_hukka_viitanen import (  # noqa: E402
    SENSITIVITY_CLASSES,
    dewpoint,
    rh_crit,
    step_vtt,
)

# Two material tracks are scored side by side. Defaults: a very sensitive
# surface (the worst case, and the class most published validation uses) and a
# medium resistant one. Pick any key of SENSITIVITY_CLASSES; see source [2]
# Table 1 in python/vtt_hukka_viitanen.py for what each class represents.
DEFAULT_SENSITIVE_CLASS = "very_sensitive"
DEFAULT_RESISTANT_CLASS = "medium_resistant"

# The mould index scale, Viitanen & Ojanen 2007 Table 1.
VTT_SCALE: list[tuple[float, str]] = [
    (6, "Very heavy, tight growth, ~100% coverage"),
    (5, "Over 70% coverage"),
    (4, "30-70% coverage, visual"),
    (3, "10-30% coverage, visible to the naked eye"),
    (2, "Under 10% coverage, microscope"),
    (1, "Small amounts, microscope only"),
    (0, "No growth"),
]


def vtt_label(index: float) -> str:
    for min_v, label in VTT_SCALE:
        if index >= min_v:
            return label
    return "No growth"


def _round(n: float, dp: int) -> float:
    """Round half-UP, like JavaScript's Math.round.

    Python's built-in round() is banker's rounding (half-to-even), so
    round(76.25, 1) == 76.2 where most other tools show 76.3. Half-up keeps the
    printed numbers in line with a JS consumer of the same data.
    """
    f = 10**dp
    return math.floor(n * f + 0.5) / f


# ── Daily runner ────────────────────────────────────────────────────────────


@dataclass
class HourlyReading:
    ts: datetime
    temp_c: float
    rh_pct: float


@dataclass
class VttState:
    """Last night's closing state, carried in, because VTT is cumulative.

    The dry-spell hours matter: the published decline has a 6-24 h delay, so a
    room that turned dry at 23:00 must not restart its dry spell at midnight.
    """

    vtt_sensitive: float = 0.0
    vtt_resistant: float = 0.0
    dry_hours_sensitive: float = 0.0
    dry_hours_resistant: float = 0.0


@dataclass
class HourStep:
    ts: datetime
    temp_c: float
    rh_pct: float
    hours: float
    critical_rh: float
    dewpoint_c: float
    growing: bool  # RH at or above the critical curve: actively growing
    vtt_sensitive: float
    delta: float  # change contributed by this single step


@dataclass
class DailyVttResult:
    # Closing index for the day: this is "the VTT score".
    vtt_sensitive: float
    vtt_resistant: float

    # Rate of change, index points per day (closing minus opening).
    rate_per_day: float
    rate_per_day_resistant: float

    trend: str  # 'rising' | 'falling' | 'stable'

    # Fastest single-step growth in the day, points per hour.
    peak_hourly_rate: float

    opening_sensitive: float

    # Supporting daily features
    hours_used: float
    growth_hours: float
    temp_mean_c: float
    rh_mean_pct: float
    dewpoint_mean_c: float
    rh_hours_over_80: float

    label: str
    state: VttState  # pass this back in tomorrow
    hourly: list[HourStep] = field(default_factory=list)


def run_daily_vtt(
    readings: Sequence[HourlyReading],
    prev: VttState | None = None,
    max_step_hours: float = 3.0,
    sensitive_class: str = DEFAULT_SENSITIVE_CLASS,
    resistant_class: str = DEFAULT_RESISTANT_CLASS,
) -> DailyVttResult:
    """Score one day of readings.

    readings         the day's readings, any cadence. Order doesn't matter;
                     they're sorted here.
    prev             last night's closing state. Omit for a cold start.
    max_step_hours   cap on how far a single gap may integrate, so one missing
                     week of data can't jump the index in a single step.
    """
    prev = prev or VttState()
    sens = SENSITIVITY_CLASSES[sensitive_class]
    resi = SENSITIVITY_CLASSES[resistant_class]

    rows = sorted(
        (
            r
            for r in readings
            if r.temp_c is not None
            and r.rh_pct is not None
            and math.isfinite(r.temp_c)
            and math.isfinite(r.rh_pct)
        ),
        key=lambda r: r.ts,
    )

    m_s, dry_s = prev.vtt_sensitive, prev.dry_hours_sensitive
    m_r, dry_r = prev.vtt_resistant, prev.dry_hours_resistant

    hourly: list[HourStep] = []
    temp_sum = rh_sum = dew_sum = 0.0
    rh_hours_over_80 = growth_hours = peak_hourly_rate = 0.0

    for i, r in enumerate(rows):
        # Hours this reading represents: the gap to the NEXT reading. It
        # degrades gracefully across gaps and is capped so a long outage can't
        # integrate in a single jump. The last reading of the day gets the
        # median cadence.
        if i < len(rows) - 1:
            hours = (rows[i + 1].ts - r.ts).total_seconds() / 3600.0
        else:
            hours = _typical_gap_hours(rows)
        if not math.isfinite(hours) or hours <= 0:
            hours = 1.0
        hours = min(hours, max_step_hours)

        crit = rh_crit(r.temp_c, sens)
        dew = dewpoint(r.temp_c, r.rh_pct)
        growing = r.rh_pct >= crit

        before = m_s
        m_s, dry_s = step_vtt(m_s, r.temp_c, r.rh_pct, dry_s, hours, sens)
        m_r, dry_r = step_vtt(m_r, r.temp_c, r.rh_pct, dry_r, hours, resi)
        delta = m_s - before

        if growing:
            growth_hours += hours
        if r.rh_pct >= 80:
            rh_hours_over_80 += hours
        peak_hourly_rate = max(peak_hourly_rate, delta / hours)

        temp_sum += r.temp_c
        rh_sum += r.rh_pct
        dew_sum += dew

        hourly.append(
            HourStep(
                ts=r.ts,
                temp_c=r.temp_c,
                rh_pct=r.rh_pct,
                hours=_round(hours, 2),
                critical_rh=_round(crit, 1),
                dewpoint_c=_round(dew, 1),
                growing=growing,
                vtt_sensitive=_round(m_s, 4),
                delta=_round(delta, 4),
            )
        )

    n = len(rows) or 1
    # Rounded before the trend test so the reported rate and the reported
    # direction can never disagree.
    rate_per_day = _round(m_s - prev.vtt_sensitive, 4)
    rate_per_day_resistant = _round(m_r - prev.vtt_resistant, 4)

    return DailyVttResult(
        vtt_sensitive=_round(m_s, 4),
        vtt_resistant=_round(m_r, 4),
        rate_per_day=rate_per_day,
        rate_per_day_resistant=rate_per_day_resistant,
        trend="rising" if rate_per_day > 0 else "falling" if rate_per_day < 0 else "stable",
        peak_hourly_rate=_round(peak_hourly_rate, 4),
        opening_sensitive=_round(prev.vtt_sensitive, 4),
        hours_used=_round(sum(h.hours for h in hourly), 2),
        growth_hours=_round(growth_hours, 2),
        temp_mean_c=_round(temp_sum / n, 1),
        rh_mean_pct=_round(rh_sum / n, 1),
        dewpoint_mean_c=_round(dew_sum / n, 1),
        rh_hours_over_80=_round(rh_hours_over_80, 2),
        label=vtt_label(m_s),
        # Unrounded, so carrying state night to night adds no rounding drift.
        state=VttState(m_s, m_r, dry_s, dry_r),
        hourly=hourly,
    )


def _typical_gap_hours(rows: Sequence[HourlyReading]) -> float:
    gaps = sorted(
        (rows[i + 1].ts - rows[i].ts).total_seconds() / 3600.0
        for i in range(len(rows) - 1)
    )
    return gaps[len(gaps) // 2] if gaps else 1.0


# ── Demo ────────────────────────────────────────────────────────────────────
# Four zones, fourteen nightly runs each, hourly readings.

SCENARIOS: list[dict] = [
    # (temp_c, rh_pct)
    {"name": "Severe damp (persistent)", "night": (18, 92), "day": (18, 92)},
    {"name": "Poorly ventilated flat", "night": (14, 90), "day": (18, 68)},
    {"name": "Cold bedroom, dry days", "night": (14.5, 88), "day": (19.5, 54)},
    {"name": "Healthy, well heated", "night": (19, 58), "day": (21, 48)},
]
DEMO_NIGHTS = 14


def demo_day(scenario: dict, day_offset: int) -> list[HourlyReading]:
    base = datetime(2026, 1, 15, tzinfo=timezone.utc) + timedelta(days=day_offset)
    out = []
    for h in range(24):
        temp_c, rh_pct = scenario["night"] if (h < 7 or h >= 22) else scenario["day"]
        out.append(HourlyReading(ts=base + timedelta(hours=h), temp_c=temp_c, rh_pct=rh_pct))
    return out


def _main() -> None:
    print(f"\nVTT mould index: {DEMO_NIGHTS} nightly runs per zone, 24 hourly readings each.")
    print("Night = 22:00-07:00. Each run carries in the previous run's closing state.")
    print(f"Tracks: {DEFAULT_SENSITIVE_CLASS} (shown) and {DEFAULT_RESISTANT_CLASS}.\n")

    for sc in SCENARIOS:
        nt, nr = sc["night"]
        dt_, dr = sc["day"]
        print(f"{sc['name']}: night {nt} C/{nr}%, day {dt_} C/{dr}%")
        header = ["day", "mean RH", "growth h", "VTT", "rate/day", "trend"]
        print("".join(h.ljust(5 if i == 0 else 11) for i, h in enumerate(header)))
        print("-" * 60)

        state = VttState()
        res = None
        for d in range(DEMO_NIGHTS):
            res = run_daily_vtt(demo_day(sc, d), state)
            print(
                "".join(
                    [
                        str(d + 1).ljust(5),
                        f"{res.rh_mean_pct}%".ljust(11),
                        f"{res.growth_hours}".ljust(11),
                        f"{res.vtt_sensitive:.4f}".ljust(11),
                        f"{'+' if res.rate_per_day >= 0 else ''}{res.rate_per_day:.4f}".ljust(11),
                        res.trend,
                    ]
                )
            )
            state = res.state
        print(f"-> {res.label}   (resistant track: {res.vtt_resistant:.4f})\n")

    # The reason the runner steps hours instead of scoring a daily mean.
    sc = SCENARIOS[2]
    day = demo_day(sc, 0)
    sens = SENSITIVITY_CLASSES[DEFAULT_SENSITIVE_CLASS]
    mean_t = sum(r.temp_c for r in day) / len(day)
    mean_rh = sum(r.rh_pct for r in day) / len(day)
    mean_only, _ = step_vtt(0.0, mean_t, mean_rh, 0.0, 24.0, sens)
    mean_growth_h = 24 if mean_rh >= rh_crit(mean_t, sens) else 0
    stepped = run_daily_vtt(day)

    print("Why the runner loops the hours instead of scoring a daily mean")
    print("-" * 60)
    print(f"Zone: {sc['name']}")
    print(f"  daily mean            {mean_t:.1f} C / {mean_rh:.1f}% RH")
    print(
        f"  critical RH @ {sc['night'][0]} C  {rh_crit(sc['night'][0], sens):.1f}%"
        f"  <- night RH of {sc['night'][1]}% is ABOVE this"
    )
    print(f"  scored on the mean    VTT {mean_only:.4f}, growth hours {mean_growth_h}")
    print(f"  stepped hourly        VTT {stepped.vtt_sensitive:.4f}, growth hours {stepped.growth_hours}")
    print(f"\nThe mean hides the condensation entirely: {mean_rh:.1f}% is below the critical")
    print("curve, so a mean-based run reports a clean zone. Stepping the hours finds")
    print("the 9 hours a night that are genuinely above it.")
    print("\nNote the index itself stays near zero for this room. That is the published")
    print("model, not a bug: its fast early decline (the first 6 dry hours) undoes each")
    print("night's growth, so a room that dries out every day does not accumulate.")
    print("Growth hours flag the condition; the index says whether the surface is")
    print("actually losing ground. Report both.\n")


if __name__ == "__main__":
    _main()
