// Replay one space's hourly history under the simplified and the published
// mould models, side by side. Pure: no I/O. compare.mjs feeds it simulator
// output; replay.test.mjs pins its behaviour.
import { rhCrit, SENSITIVITY_CLASSES, stepVTT } from '../../js/vtt-model.mjs'
import { simplifiedCriticalRH, simplifiedStep } from './simplified-model.mjs'

const PUBLISHED_CLASS = SENSITIVITY_CLASSES.very_sensitive
// Dry-spell length assumed when no wet hour has been seen yet: comfortably past
// every decline band, so a dry start declines at the slow long-term rate.
const NO_WET_HOUR_YET = 14 * 24

/**
 * Replay hourly rows ({ bucketStart: ISO string, tempC, rhPct }, any order)
 * from mould index 0 under both models. RH-above-critical hours are counted
 * only for rows within `windowHours` of the latest row, so a 14-day replay can
 * report a 7-day count.
 *
 * The published model's dry spell is wall-clock hours since the last wet hour
 * (not a count of rows), so a gap in the data ages the dry spell.
 */
export function replayZone(rows, windowHours) {
  const sorted = rows
    .filter((r) => Number.isFinite(r.tempC) && Number.isFinite(r.rhPct))
    .slice()
    .sort((a, b) => Date.parse(a.bucketStart) - Date.parse(b.bucketStart))

  const out = {
    hours: sorted.length,
    recentHours: 0,
    simplifiedRhAboveHours: 0,
    publishedRhAboveHours: 0,
    simplifiedIndexEnd: 0,
    publishedIndexEnd: 0,
    simplifiedIndexMax: 0,
    publishedIndexMax: 0,
    lastSimplifiedRhAbove: false,
    lastPublishedRhAbove: false,
    series: [],
  }
  if (sorted.length === 0) return out

  const lastMs = Date.parse(sorted[sorted.length - 1].bucketStart)
  const windowStartMs = lastMs - (windowHours - 1) * 3600000
  let mS = 0
  let mP = 0
  let lastWetMs = null

  for (const r of sorted) {
    const t = Date.parse(r.bucketStart)
    const sAbove = r.rhPct >= simplifiedCriticalRH(r.tempC)
    const pAbove = r.rhPct >= rhCrit(r.tempC, PUBLISHED_CLASS)

    mS = simplifiedStep(mS, r.tempC, r.rhPct, 1, 'sensitive')
    // Hours of dry spell BEFORE this step; stepVTT adds the step itself.
    const dryBefore = lastWetMs === null
      ? NO_WET_HOUR_YET
      : Math.max(0, (t - lastWetMs) / 3600000 - 1)
    mP = stepVTT(mP, r.tempC, r.rhPct, dryBefore, 1, PUBLISHED_CLASS).m
    if (pAbove) lastWetMs = t

    out.simplifiedIndexMax = Math.max(out.simplifiedIndexMax, mS)
    out.publishedIndexMax = Math.max(out.publishedIndexMax, mP)
    if (t >= windowStartMs) {
      out.recentHours++
      if (sAbove) out.simplifiedRhAboveHours++
      if (pAbove) out.publishedRhAboveHours++
    }
    out.series.push({
      bucketStart: r.bucketStart,
      simplifiedRhAbove: sAbove, publishedRhAbove: pAbove,
      simplifiedIndex: mS, publishedIndex: mP,
    })
    out.lastSimplifiedRhAbove = sAbove
    out.lastPublishedRhAbove = pAbove
  }
  out.simplifiedIndexEnd = mS
  out.publishedIndexEnd = mP
  return out
}
