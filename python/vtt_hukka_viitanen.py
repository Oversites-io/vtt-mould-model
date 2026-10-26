#!/usr/bin/env python3
"""
The VTT / Hukka-Viitanen mould growth model: a faithful reference implementation.

Pure stdlib, no dependencies. The JavaScript twin is js/vtt-model.mjs; the two
are checked against each other by js/parity.test.mjs. Change one, change both.

SOURCES (every constant below is traceable to one of these; nothing invented):

  [1] Viitanen, H. & Ojanen, T. (2007) "Improved Model to Predict Mold Growth in
      Building Materials", Buildings X / ASHRAE.
      https://web.ornl.gov/sci/buildings/conf-archive/2007%20B10%20papers/162_Viitanen.pdf
      -> Eq 1 (RHcrit polynomial), Eq 2 (growth), Eq 3 (k1), Eq 4 (k2), Eq 5 (decline)

  [2] Menneer, T. et al. (2022) "Modelling mould growth in domestic environments
      using relative humidity and temperature", Building and Environment 208:108583.
      https://www.ecehh.org/wp/wp-content/uploads/2023/06/Modelling_mould_growth.pdf
      -> Table 1 (sensitivity-class constants), Eq 6 (Mmax), and the explicit
         statement that dM/dt is expressed per 24 h

  [3] Hukka, A. & Viitanen, H. (1999) "A mathematical model of mould growth on
      wooden material", Wood Science and Technology 33:475-485. The origin of the
      regression work both papers above restate.

MOULD INDEX SCALE (Viitanen 2007, Table 1):
  0  no growth, spores not activated
  1  small amounts on surface, microscope only  <- growth initiated
  2  <10% coverage, microscope
  3  10-30% coverage, VISIBLE to naked eye      <- "visible mould"
  4  30-70% coverage, visual
  5  >70% coverage
  6  ~100% coverage, very heavy and tight growth

UNITS NOTE (this trips people up):
  Source [1] states t is in weeks; source [2] states dM/dt is per 24 h. Both are
  consistent: the regression exp(...) yields time-to-M=1 in WEEKS, and the
  literal 7 in the denominator converts weeks to days, so the equation as written
  gives a PER-DAY rate. Sanity check in self_test(): pine sapwood at 20 C / 97% RH
  reaches M=1 in ~10 days, matching the published ~1.5 weeks to initiation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

# ── Sensitivity classes: source [2] Table 1 ─────────────────────────────────
#   k11 : k1 multiplier while M < 1
#   k12 : k1 multiplier while M >= 1
#   A,B,C : coefficients of the Mmax equation (Eq 6)
#   rh_crit_above_20 : RHcrit constant used when T > 20 C


@dataclass(frozen=True)
class Sensitivity:
    name: str
    k11: float
    k12: float
    A: float
    B: float
    C: float
    rh_crit_above_20: float


SENSITIVITY_CLASSES = {
    "very_sensitive": Sensitivity("very sensitive", 1.0, 2.0, 1.0, 7.0, 2.0, 80.0),
    "sensitive": Sensitivity("sensitive", 0.578, 0.386, 0.3, 6.0, 1.0, 80.0),
    "medium_resistant": Sensitivity("medium resistant", 0.072, 0.097, 0.0, 5.0, 1.5, 85.0),
    "resistant": Sensitivity("resistant", 0.033, 0.014, 0.0, 3.0, 1.0, 85.0),
}

# Growth-equation constants: source [2], from the original VTT model [1].
P_T = 0.68
P_RH = 13.9
P_C = 66.02

M_INDEX_MIN = 0.0
M_INDEX_MAX = 6.0


# ── Eq 1: critical relative humidity ───────────────────────────────────────
def rh_crit(temp_c: float, sens: Sensitivity) -> float:
    """
    RHcrit(T) = -0.00267*T^3 + 0.160*T^2 - 3.13*T + 100.0   for T <= 20 C
              = 80 or 85 (per sensitivity class)            for T >  20 C

    The polynomial evaluates to 80.04 at T=20, so it joins the constant branch
    smoothly for the 80% classes. Source [1] Eq 1.
    """
    if temp_c > 20.0:
        return sens.rh_crit_above_20
    return -0.00267 * temp_c**3 + 0.160 * temp_c**2 - 3.13 * temp_c + 100.0


# ── Eq 6: maximum attainable mould index ───────────────────────────────────
def m_max(temp_c: float, rh_pct: float, sens: Sensitivity) -> float:
    """
    Mmax = A + B*x - C*x^2,  where x = (RHcrit - RH) / (RHcrit - 100)

    x is 0 at RH == RHcrit and 1 at RH == 100%, so Mmax rises from A (barely any
    growth possible at the threshold) to A+B-C at saturation. For 'very sensitive'
    that is 1 -> 6, i.e. the full index. For 'resistant' it is 0 -> 2, so a
    resistant material can never reach visible mould (M3) no matter how wet.
    Source [2] Eq 6 + Table 1.
    """
    crit = rh_crit(temp_c, sens)
    denom = crit - 100.0
    if denom == 0:
        return M_INDEX_MAX
    x = (crit - rh_pct) / denom
    value = sens.A + sens.B * x - sens.C * x * x
    return max(0.0, min(M_INDEX_MAX, value))


# ── Eq 2/3/4: growth rate when RH >= RHcrit ────────────────────────────────
def growth_rate_per_day(
    temp_c: float, rh_pct: float, m: float, sens: Sensitivity,
    wood_species: int = 0, surface_quality: int = 0,
) -> float:
    """
    dM/dt = k1*k2 / (7 * exp(-pT*ln T - pRH*ln RH + 0.14*W - 0.33*SQ + pC))

    Returned per 24 h. W: 0=pine, 1=spruce. SQ: 0=sawn, 1=kiln dried.
    Sources [1] Eq 2-4, [2] Eq 4-5.
    """
    if temp_c <= 0.0 or rh_pct <= 0.0:
        return 0.0

    exponent = (
        -P_T * math.log(temp_c)
        - P_RH * math.log(rh_pct)
        + 0.14 * wood_species
        - 0.33 * surface_quality
        + P_C
    )
    # exp(...) is the regression's time-to-M=1 in weeks; the 7 converts to days.
    denominator = 7.0 * math.exp(exponent)
    if denominator <= 0:
        return 0.0

    k1 = sens.k11 if m < 1.0 else sens.k12
    # Retardation as M approaches Mmax.
    k2 = max(1.0 - math.exp(2.3 * (m - m_max(temp_c, rh_pct, sens))), 0.0)

    return (k1 * k2) / denominator


# ── Eq 5 (2007) / Eq 3 (2022): decline when RH < RHcrit ────────────────────
def decline_rate_per_day(
    hours_since_dry_start: float, c_decline: float = 1.0, wood_surface: bool = True,
) -> float:
    """
    dM/dt = Cdecline * { -0.032   for t - t1 <= 6h  (or any non-wood surface)
                       {  0       for 6h < t - t1 <= 24h
                       { -0.016   for t - t1 > 24h

    The flat middle band is the published delay: mould does not begin dying back
    immediately once conditions turn. Cdecline (0.1..1) scales decline intensity
    by material. Sources [1] Eq 5, [2] Eq 3.
    """
    if not wood_surface:
        return c_decline * -0.032
    if hours_since_dry_start <= 6.0:
        return c_decline * -0.032
    if hours_since_dry_start <= 24.0:
        return 0.0
    return c_decline * -0.016


# ── One time step (pure) ───────────────────────────────────────────────────
def step_vtt(
    m: float, temp_c: float, rh_pct: float, dry_hours_before: float,
    dt_hours: float, sens: Sensitivity, wood_species: int = 0,
    surface_quality: int = 0, c_decline: float = 1.0, wood_surface: bool = True,
) -> tuple[float, float]:
    """
    Advance the mould index by one time step. Pure: the caller owns the state.
    Mirrors stepVTT() in js/vtt-model.mjs.

    dry_hours_before is the length of the current dry spell BEFORE this step
    (0 if the previous step was wet). Returns (new_m, new_dry_hours).
    """
    crit = rh_crit(temp_c, sens)
    if rh_pct >= crit:
        dry_hours = 0.0
        rate = growth_rate_per_day(
            temp_c, rh_pct, m, sens, wood_species, surface_quality
        )
    else:
        dry_hours = dry_hours_before + dt_hours
        rate = decline_rate_per_day(dry_hours, c_decline, wood_surface)

    nxt = max(M_INDEX_MIN, min(m + rate * (dt_hours / 24.0), M_INDEX_MAX))
    return nxt, dry_hours


# ── Stateful integrator ────────────────────────────────────────────────────
class MouldModel:
    """
    Step the mould index through a time series of (temperature, RH) readings.

    Tracks time since the start of the current dry spell, which the decline
    equation needs. Typical use:

        model = MouldModel("very_sensitive")
        for temp, rh in readings:
            m = model.step(temp, rh, dt_hours=1.0)

    To carry state between runs (e.g. a nightly job), save model.m and
    model.dry_hours and pass them back as m_initial and dry_hours_initial.
    """

    def __init__(
        self, sensitivity: str = "very_sensitive", m_initial: float = 0.0,
        dry_hours_initial: float = 0.0, wood_species: int = 0,
        surface_quality: int = 0, c_decline: float = 1.0,
        wood_surface: bool = True,
    ) -> None:
        if sensitivity not in SENSITIVITY_CLASSES:
            raise ValueError(
                f"unknown sensitivity {sensitivity!r}; "
                f"expected one of {sorted(SENSITIVITY_CLASSES)}"
            )
        self.sens = SENSITIVITY_CLASSES[sensitivity]
        self.m = float(m_initial)
        self.dry_hours = float(dry_hours_initial)
        self.wood_species = wood_species
        self.surface_quality = surface_quality
        self.c_decline = c_decline
        self.wood_surface = wood_surface

    def step(self, temp_c: float, rh_pct: float, dt_hours: float = 1.0) -> float:
        """Advance by dt_hours and return the new mould index."""
        self.m, self.dry_hours = step_vtt(
            self.m, temp_c, rh_pct, self.dry_hours, dt_hours, self.sens,
            self.wood_species, self.surface_quality, self.c_decline,
            self.wood_surface,
        )
        return self.m

    def run(
        self, series: Iterable[tuple[float, float]], dt_hours: float = 1.0
    ) -> Iterator[float]:
        """Yield the mould index after each (temp_c, rh_pct) reading."""
        for temp_c, rh_pct in series:
            yield self.step(temp_c, rh_pct, dt_hours)


def dewpoint(temp_c: float, rh_pct: float) -> float:
    """Dew point from temperature and RH (Magnus formula, a=17.27, b=237.7)."""
    a, b = 17.27, 237.7
    alpha = (a * temp_c) / (b + temp_c) + math.log(rh_pct / 100)
    return (b * alpha) / (a - alpha)


def days_to_index(
    target_m: float, temp_c: float, rh_pct: float,
    sensitivity: str = "very_sensitive", max_days: int = 3650,
) -> Optional[float]:
    """
    Days of CONTINUOUS exposure at (temp_c, rh_pct) to reach target_m.
    Returns None if unreachable: Mmax may sit below the target.
    """
    model = MouldModel(sensitivity)
    for hour in range(max_days * 24):
        if model.step(temp_c, rh_pct, 1.0) >= target_m:
            return (hour + 1) / 24.0
    return None


def self_test() -> None:
    """Sanity checks against values stated in the published papers."""
    print("VTT / Hukka-Viitanen reference implementation: self test\n")

    # Eq 1 continuity: polynomial should meet the 80% constant branch at T=20.
    vs = SENSITIVITY_CLASSES["very_sensitive"]
    at20 = rh_crit(20.0, vs)
    print(f"  RHcrit(20 C)            = {at20:.2f}%   (expect ~80, joins constant branch)")
    for t in (0, 5, 10, 15, 20, 25):
        print(f"    RHcrit({t:>2} C) = {rh_crit(float(t), vs):6.2f}%")

    # Mmax bounds: A at threshold, A+B-C at saturation.
    print(f"\n  Mmax(20 C, 100% RH)     = {m_max(20.0, 100.0, vs):.2f}   (expect 6.00 = A+B-C)")
    print(f"  Mmax(20 C, RHcrit)      = {m_max(20.0, rh_crit(20.0, vs), vs):.2f}   (expect 1.00 = A)")
    res = SENSITIVITY_CLASSES["resistant"]
    print(f"  Mmax(20 C, 100%) resist = {m_max(20.0, 100.0, res):.2f}   (expect 2.00, never visible)")

    # Published expectation: pine sapwood ~1.5 weeks to initiation at 20C/97%RH.
    d1 = days_to_index(1.0, 20.0, 97.0)
    print(f"\n  20 C / 97% RH -> M1     = {d1:.1f} days  (published ~1.5 weeks to initiation)")
    d3 = days_to_index(3.0, 20.0, 97.0)
    print(f"  20 C / 97% RH -> M3     = {d3:.1f} days  (visible mould)")

    print("\n  Time to VISIBLE mould (M3), continuous exposure, very sensitive:")
    for t, rh in ((15.0, 90.0), (15.0, 95.0), (20.0, 85.0), (20.0, 90.0), (20.0, 95.0)):
        d = days_to_index(3.0, t, rh)
        print(f"    {t:>4.0f} C / {rh:>4.0f}% RH -> {'never' if d is None else f'{d:6.1f} days'}")


if __name__ == "__main__":
    self_test()
