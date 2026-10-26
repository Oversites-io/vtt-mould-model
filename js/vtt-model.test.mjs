// Published-value checkpoints for the VTT / Hukka-Viitanen model.
// Every expectation traces to the cited papers (see vtt-model.mjs header) or
// to a closed-form consequence of their equations.
// Run: npm test   (or: node --test js/)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  SENSITIVITY_CLASSES,
  rhCrit,
  mMax,
  declineRatePerDay,
  stepVTT,
  dewpoint,
} from './vtt-model.mjs'

const VS = SENSITIVITY_CLASSES.very_sensitive
const near = (actual, expected, tol, msg) =>
  assert.ok(Math.abs(actual - expected) <= tol, `${msg ?? ''} expected ${expected} +/- ${tol}, got ${actual}`)

test('RHcrit: Eq 1 polynomial joins the 80% branch at 20 C', () => {
  near(rhCrit(20.0, VS), 80.04, 0.01)
  assert.equal(rhCrit(25.0, VS), 80.0)
  assert.equal(rhCrit(25.0, SENSITIVITY_CLASSES.resistant), 85.0)
  near(rhCrit(0.0, VS), 100.0, 0.001) // no growth below saturation at 0 C
  near(rhCrit(10.0, VS), 82.03, 0.01)
})

test('Mmax: Eq 6 bounds, A at threshold, A+B-C at saturation', () => {
  near(mMax(20.0, 100.0, VS), 6.0, 0.001) // 1+7-2
  near(mMax(20.0, rhCrit(20.0, VS), VS), 1.0, 0.001) // = A
  // Resistant material caps at M2 even fully saturated: never visible mould.
  near(mMax(20.0, 100.0, SENSITIVITY_CLASSES.resistant), 2.0, 0.001)
})

test('growth: 20 C / 97% RH reaches M1 in ~10.4 days (published ~1.5 weeks)', () => {
  let m = 0, dry = 0, hours = 0
  while (m < 1.0 && hours < 24 * 60) {
    ;({ m, dryHours: dry } = stepVTT(m, 20.0, 97.0, dry, 1, VS))
    hours++
  }
  const days = hours / 24
  assert.ok(days > 9.5 && days < 11.5, `expected ~10.4 days to M1, got ${days.toFixed(1)}`)
})

test('growth: resistant material never reaches visible mould', () => {
  let m = 0, dry = 0
  const RES = SENSITIVITY_CLASSES.resistant
  for (let h = 0; h < 24 * 365 * 3; h++) {
    ;({ m, dryHours: dry } = stepVTT(m, 20.0, 100.0, dry, 1, RES))
  }
  assert.ok(m < 2.01, `resistant M after 3 years saturated = ${m.toFixed(3)}, must cap ~2`)
  assert.ok(m > 1.8, `should approach its Mmax of 2, got ${m.toFixed(3)}`)
})

test('decline: Eq 5 bands, -0.032, 0, -0.016 (per 24h)', () => {
  assert.equal(declineRatePerDay(3), -0.032)
  assert.equal(declineRatePerDay(12), 0) // the published 6-24h no-decline delay
  assert.equal(declineRatePerDay(48), -0.016)
  near(declineRatePerDay(3, 0.5), -0.016, 1e-12) // cDecline scaling
  assert.equal(declineRatePerDay(48, 1.0, false), -0.032) // non-wood: constant
})

test('step: dry-spell hours accumulate and reset', () => {
  let s = stepVTT(2.0, 20.0, 60.0, 0, 1, VS) // dry hour: counter up
  assert.equal(s.dryHours, 1)
  s = stepVTT(s.m, 20.0, 95.0, s.dryHours, 1, VS) // wet hour: counter reset
  assert.equal(s.dryHours, 0)
})

test('step: index clamps to [0, 6]', () => {
  assert.equal(stepVTT(0.0, 20.0, 50.0, 100, 1, VS).m, 0.0) // cannot go negative
  assert.ok(stepVTT(6.0, 30.0, 100.0, 0, 1, VS).m <= 6.0)
})

test('step: no growth at/below 0 C even when saturated', () => {
  const s = stepVTT(1.0, 0.0, 100.0, 0, 1, VS)
  assert.equal(s.m, 1.0)
  assert.equal(s.dryHours, 0) // RH >= crit, so not a dry spell either
})

test('dewpoint: Magnus formula, known values', () => {
  near(dewpoint(20, 100), 20.0, 1e-9) // saturated air: dew point = air temp
  near(dewpoint(20, 50), 9.26, 0.01)
})
