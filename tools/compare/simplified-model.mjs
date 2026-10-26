// A simplified mould index, kept ONLY to compare against the published model.
// Never use this for real scoring: use js/vtt-model.mjs.
//
// It is a common shortcut: interpolate a hand-built critical-RH lookup curve,
// then grow the index linearly with how far RH sits above it. Easy to write,
// looks plausible, and wrong in ways the comparison (compare.mjs) measures:
//   1. its curve sits 1 to 3 RH points ABOVE the published RHcrit below 20 C,
//      so a cold damp room (12 C, 82% RH) reads as safe when the published
//      model says it is growing;
//   2. its growth rate has no basis in the regression: under sustained damp
//      it grows about 4x slower than the published model and never reaches
//      visible mould in the simulator's 61 days, while in rooms that dry out
//      each day it keeps growth the published model's early decline erases.

const CRITICAL_RH_CURVE = [
  [0, 95], [5, 90], [10, 85], [15, 82], [20, 80],
  [25, 79], [30, 78], [35, 78], [40, 78], [50, 78],
]

const GROWTH_RATES = {
  sensitive: { k1: 1.0, k2: 0.2 },
  resistant: { k1: 0.3, k2: 0.1 },
}

/** Critical RH, linearly interpolated from the lookup curve. */
export function simplifiedCriticalRH(tempC) {
  if (tempC <= CRITICAL_RH_CURVE[0][0]) return CRITICAL_RH_CURVE[0][1]
  const last = CRITICAL_RH_CURVE[CRITICAL_RH_CURVE.length - 1]
  if (tempC >= last[0]) return last[1]
  for (let i = 0; i < CRITICAL_RH_CURVE.length - 1; i++) {
    const [t1, rh1] = CRITICAL_RH_CURVE[i]
    const [t2, rh2] = CRITICAL_RH_CURVE[i + 1]
    if (tempC >= t1 && tempC <= t2) {
      const ratio = (tempC - t1) / (t2 - t1)
      return rh1 + ratio * (rh2 - rh1)
    }
  }
  return 80
}

/** One step of the simplified index. sensitivity: 'sensitive' | 'resistant'. */
export function simplifiedStep(currentIndex, tempC, rhPct, hoursExposed, sensitivity) {
  const criticalRH = simplifiedCriticalRH(tempC)
  const rate = GROWTH_RATES[sensitivity]

  if (tempC < 0 || tempC > 50) {
    const decay = rate.k2 * hoursExposed * 0.01
    return Math.max(0, currentIndex - decay)
  }
  if (rhPct >= criticalRH) {
    const rhExcess = (rhPct - criticalRH) / 100
    const tempFactor = tempC > 0 ? Math.min(tempC / 20, 1.5) : 0
    const growth = rate.k1 * rhExcess * tempFactor * hoursExposed * 0.01
    return Math.min(6, currentIndex + growth)
  }
  const rhDeficit = (criticalRH - rhPct) / 100
  const decay = rate.k2 * rhDeficit * hoursExposed * 0.005
  return Math.max(0, currentIndex - decay)
}
