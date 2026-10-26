// Run: npm test   (or: node --test tools/compare/)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { replayZone } from './replay.mjs'
import { simplifiedCriticalRH, simplifiedStep } from './simplified-model.mjs'

const near = (actual, expected, tol) =>
  assert.ok(Math.abs(actual - expected) <= tol, `expected ${expected} +/- ${tol}, got ${actual}`)

function hours(n, tempC, rhPct, start = '2026-10-01T00:00:00Z') {
  const t0 = Date.parse(start)
  return Array.from({ length: n }, (_, i) => ({
    bucketStart: new Date(t0 + i * 3600000).toISOString(),
    tempC,
    rhPct,
  }))
}

// ── the simplified model itself ─────────────────────────────────────────

test('simplified: critical RH interpolates the lookup curve', () => {
  near(simplifiedCriticalRH(16.8), 81.28, 1e-9)
  near(simplifiedCriticalRH(19.6), 80.16, 1e-9)
  near(simplifiedCriticalRH(12), 83.8, 1e-9)
})

test('simplified: critical RH clamps outside the curve', () => {
  assert.equal(simplifiedCriticalRH(-5), 95)
  assert.equal(simplifiedCriticalRH(60), 78)
})

test('simplified: grows, decays and freezes as written', () => {
  // 20 C, 90% RH: excess 0.10 x tempFactor 1.0 x k1 1.0 x 0.01 = +0.001
  near(simplifiedStep(0, 20, 90, 1, 'sensitive'), 0.001, 1e-12)
  // 20 C, 70% RH: deficit 0.10 x k2 0.2 x 0.005 = -0.0001
  near(simplifiedStep(1, 20, 70, 1, 'sensitive'), 0.9999, 1e-12)
  // below 0 C and above 50 C: slow decay k2 x 0.01
  near(simplifiedStep(1, -5, 99, 1, 'sensitive'), 0.998, 1e-12)
  near(simplifiedStep(1, 55, 99, 1, 'sensitive'), 0.998, 1e-12)
})

test('simplified: caps at 6, floors at 0, resistant grows slower', () => {
  assert.equal(simplifiedStep(5.9999, 30, 100, 1000, 'sensitive'), 6)
  assert.equal(simplifiedStep(0, 20, 10, 1, 'sensitive'), 0)
  near(simplifiedStep(0, 20, 90, 1, 'resistant'), 0.0003, 1e-12)
})

// ── the side-by-side replay ─────────────────────────────────────────────

test('replay: empty input gives zeros', () => {
  const r = replayZone([], 168)
  assert.equal(r.hours, 0)
  assert.equal(r.simplifiedIndexEnd, 0)
  assert.equal(r.publishedIndexEnd, 0)
  assert.equal(r.lastPublishedRhAbove, false)
  assert.deepEqual(r.series, [])
})

test('replay: a cold damp room (12 C, 82% RH) is above the published curve but not the simplified one', () => {
  // simplified curve at 12 C = 83.8%; published (very sensitive) = 80.87%
  const r = replayZone(hours(24, 12, 82), 168)
  assert.equal(r.simplifiedRhAboveHours, 0)
  assert.equal(r.publishedRhAboveHours, 24)
  assert.equal(r.lastSimplifiedRhAbove, false)
  assert.equal(r.lastPublishedRhAbove, true)
  assert.equal(r.simplifiedIndexEnd, 0)
  assert.ok(r.publishedIndexEnd > 0)
})

test('replay: simplified growth matches its formula (20 C, 90% RH, 48 h)', () => {
  const r = replayZone(hours(48, 20, 90), 168)
  near(r.simplifiedIndexEnd, 0.048, 1e-9)
  assert.equal(r.simplifiedRhAboveHours, 48)
  assert.equal(r.publishedRhAboveHours, 48)
  assert.ok(r.publishedIndexEnd > r.simplifiedIndexEnd)
})

test('replay: RH-above hours are counted only inside the window', () => {
  const r = replayZone(hours(240, 12, 82), 168)
  assert.equal(r.hours, 240)
  assert.equal(r.recentHours, 168)
  assert.equal(r.publishedRhAboveHours, 168)
})

test('replay: input order does not matter', () => {
  const rows = hours(24, 12, 82)
  assert.deepEqual(replayZone(rows.slice().reverse(), 168), replayZone(rows, 168))
})

test('replay: non-finite rows are dropped', () => {
  const rows = [...hours(3, 20, 90), { bucketStart: '2026-10-01T05:00:00Z', tempC: NaN, rhPct: 50 }]
  assert.equal(replayZone(rows, 168).hours, 3)
})

test('replay: series has one point per row, in time order, matching the totals', () => {
  const r = replayZone(hours(30, 12, 82).reverse(), 168)
  assert.equal(r.series.length, 30)
  assert.ok(r.series[0].bucketStart < r.series[29].bucketStart)
  assert.equal(r.series.filter((p) => p.publishedRhAbove).length, r.publishedRhAboveHours)
  assert.equal(r.series[29].publishedIndex, r.publishedIndexEnd)
  assert.equal(r.series[29].simplifiedIndex, r.simplifiedIndexEnd)
})

test('replay: dry spell is wall-clock time since the last wet hour, not a row count', () => {
  // 24 wet hours, then ONE dry hour 49 h after the last wet hour (a data gap).
  // 49 h is past the 24 h band, so decline is the slow -0.016 per day, not the
  // fast -0.032 per day it would be if the replay just counted rows.
  const wet = hours(24, 20, 95)
  const lastWet = Date.parse(wet[23].bucketStart)
  const dry = { bucketStart: new Date(lastWet + 49 * 3600000).toISOString(), tempC: 20, rhPct: 50 }
  const before = replayZone(wet, 168).publishedIndexEnd
  const after = replayZone([...wet, dry], 168).publishedIndexEnd
  assert.ok(before > 0.016)
  near(before - after, 0.016 / 24, 1e-12)
})

test('replay: a dry hour right after a wet one uses the fast early decline', () => {
  const wet = hours(24, 20, 95)
  const lastWet = Date.parse(wet[23].bucketStart)
  const dry = { bucketStart: new Date(lastWet + 3600000).toISOString(), tempC: 20, rhPct: 50 }
  const before = replayZone(wet, 168).publishedIndexEnd
  const after = replayZone([...wet, dry], 168).publishedIndexEnd
  near(before - after, 0.032 / 24, 1e-12)
})
