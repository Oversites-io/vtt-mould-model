// The VTT / Hukka-Viitanen mould growth model.
//
// Plain .mjs with no dependencies, so Node, Deno, Bun and browsers can all
// import it without a build step. The Python twin is
// python/vtt_hukka_viitanen.py; js/parity.test.mjs checks the two agree.
// Change one, change both.
//
// SOURCES (every constant is traceable; nothing invented):
//  [1] Viitanen & Ojanen 2007 "Improved Model to Predict Mold Growth in
//      Building Materials" (Buildings X/ASHRAE): Eq 1 RHcrit, Eq 2 growth,
//      Eq 3 k1, Eq 4 k2, Eq 5 decline.
//  [2] Menneer et al. 2022, Building and Environment 208:108583: Table 1
//      sensitivity-class constants, Eq 6 Mmax, per-24h rate statement.
//  [3] Hukka & Viitanen 1999, Wood Sci Tech 33:475-485: the original model.
//
// UNITS: the regression exp(...) is time-to-M1 in WEEKS; the literal 7
// converts to days, so growth/decline rates are PER 24 HOURS. Verified:
// 20 C / 97% RH reaches M1 in ~10.4 days (published ~1.5 weeks).

/**
 * Sensitivity classes: source [2] Table 1.
 * k11/k12: k1 below/at-or-above M=1. A,B,C: Mmax coefficients (Eq 6).
 * rhCritAbove20: the RHcrit constant used when T > 20 C.
 */
export const SENSITIVITY_CLASSES = {
  very_sensitive:   { name: 'very sensitive',   k11: 1.0,   k12: 2.0,   A: 1.0, B: 7.0, C: 2.0, rhCritAbove20: 80.0 },
  sensitive:        { name: 'sensitive',        k11: 0.578, k12: 0.386, A: 0.3, B: 6.0, C: 1.0, rhCritAbove20: 80.0 },
  medium_resistant: { name: 'medium resistant', k11: 0.072, k12: 0.097, A: 0.0, B: 5.0, C: 1.5, rhCritAbove20: 85.0 },
  resistant:        { name: 'resistant',        k11: 0.033, k12: 0.014, A: 0.0, B: 3.0, C: 1.0, rhCritAbove20: 85.0 },
}

// Growth-equation constants: source [2], from the original VTT model.
const P_T = 0.68
const P_RH = 13.9
const P_C = 66.02

export const M_INDEX_MIN = 0.0
export const M_INDEX_MAX = 6.0

/**
 * Eq 1: critical relative humidity for mould growth.
 * RHcrit(T) = -0.00267 T^3 + 0.160 T^2 - 3.13 T + 100   for T <= 20 C
 *           = 80 or 85 per sensitivity class             for T >  20 C
 * The polynomial evaluates to 80.04 at T=20, joining the 80% branch smoothly.
 */
export function rhCrit(tempC, cls) {
  if (tempC > 20.0) return cls.rhCritAbove20
  return -0.00267 * tempC ** 3 + 0.160 * tempC ** 2 - 3.13 * tempC + 100.0
}

/**
 * Eq 6: maximum attainable mould index for these conditions + material.
 * Mmax = A + B*x - C*x^2 where x = (RHcrit - RH)/(RHcrit - 100): 0 at the
 * threshold, 1 at saturation. 'resistant' caps at 2.0 even when saturated:
 * a resistant surface can never reach visible mould (M3).
 */
export function mMax(tempC, rhPct, cls) {
  const crit = rhCrit(tempC, cls)
  const denom = crit - 100.0
  if (denom === 0) return M_INDEX_MAX
  const x = (crit - rhPct) / denom
  const value = cls.A + cls.B * x - cls.C * x * x
  return Math.max(0.0, Math.min(M_INDEX_MAX, value))
}

/**
 * Eq 2/3/4: growth rate (per 24 h) when RH >= RHcrit.
 * W: 0=pine, 1=spruce. SQ: 0=sawn, 1=kiln dried.
 */
export function growthRatePerDay(tempC, rhPct, m, cls, woodSpecies = 0, surfaceQuality = 0) {
  if (tempC <= 0.0 || rhPct <= 0.0) return 0.0

  const exponent =
    -P_T * Math.log(tempC)
    - P_RH * Math.log(rhPct)
    + 0.14 * woodSpecies
    - 0.33 * surfaceQuality
    + P_C
  // exp(...) is the regression's time-to-M=1 in weeks; the 7 converts to days.
  const denominator = 7.0 * Math.exp(exponent)
  if (denominator <= 0) return 0.0

  const k1 = m < 1.0 ? cls.k11 : cls.k12
  // Retardation as M approaches Mmax.
  const k2 = Math.max(1.0 - Math.exp(2.3 * (m - mMax(tempC, rhPct, cls))), 0.0)

  return (k1 * k2) / denominator
}

/**
 * Eq 5: decline rate (per 24 h) when RH < RHcrit.
 * The flat 6-24h band is the published delay: mould does not begin dying back
 * immediately once conditions turn. cDecline (0.1..1) scales by material.
 */
export function declineRatePerDay(hoursSinceDryStart, cDecline = 1.0, woodSurface = true) {
  if (!woodSurface) return cDecline * -0.032
  if (hoursSinceDryStart <= 6.0) return cDecline * -0.032
  if (hoursSinceDryStart <= 24.0) return 0.0
  return cDecline * -0.016
}

/**
 * Advance the mould index by one time step. Pure: the caller owns the state.
 * Mirrors step_vtt() in python/vtt_hukka_viitanen.py.
 *
 * @param m              current mould index (0..6)
 * @param tempC          mean temperature over the step
 * @param rhPct          mean RH over the step
 * @param dryHoursBefore hours since the current dry spell began, BEFORE this
 *                       step (0 if the previous step was wet)
 * @param dtHours        step length in hours
 * @param cls            a SENSITIVITY_CLASSES entry
 * @returns {{ m: number, dryHours: number }} new index + updated dry-spell hours
 */
export function stepVTT(m, tempC, rhPct, dryHoursBefore, dtHours, cls, opts = {}) {
  const { woodSpecies = 0, surfaceQuality = 0, cDecline = 1.0, woodSurface = true } = opts
  const crit = rhCrit(tempC, cls)

  let rate
  let dryHours
  if (rhPct >= crit) {
    dryHours = 0.0
    rate = growthRatePerDay(tempC, rhPct, m, cls, woodSpecies, surfaceQuality)
  } else {
    dryHours = dryHoursBefore + dtHours
    rate = declineRatePerDay(dryHours, cDecline, woodSurface)
  }

  const next = Math.max(M_INDEX_MIN, Math.min(m + rate * (dtHours / 24.0), M_INDEX_MAX))
  return { m: next, dryHours }
}

/** Dew point from temperature and RH (Magnus formula, a=17.27, b=237.7). */
export function dewpoint(tempC, rhPct) {
  const a = 17.27, b = 237.7
  const alpha = (a * tempC) / (b + tempC) + Math.log(rhPct / 100)
  return (b * alpha) / (a - alpha)
}
