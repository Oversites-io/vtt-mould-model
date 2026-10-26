#!/usr/bin/env node
/**
 * VTT data simulator: N spaces x N days of hourly damp data, to files.
 *
 * Standalone: no DB, no network, no npm deps. Writes CSVs + a self-contained
 * load.sql you can run against a fresh Postgres.
 *
 * Every hour is scored through the published Hukka-Viitanen model
 * (js/vtt-model.mjs), on two material tracks.
 *
 * Deterministic: same --seed gives byte-identical output.
 *
 * Usage:
 *   node tools/simulator/generate.mjs
 *   node tools/simulator/generate.mjs --spaces 100 --trigger-pct 5
 *   node tools/simulator/generate.mjs --start 2026-01-01 --days 61 --out ./out
 */
import { writeFileSync, mkdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { SENSITIVITY_CLASSES, rhCrit, stepVTT, dewpoint } from '../../js/vtt-model.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))

// ── args ────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2)
const arg = (k, d) => { const i = argv.indexOf(`--${k}`); return i >= 0 ? argv[i + 1] : d }
const START = new Date(`${arg('start', '2026-01-01')}T00:00:00Z`)
const DAYS = Number(arg('days', '61'))
const SEED = Number(arg('seed', '20260831'))
const N_SPACES = Number(arg('spaces', '100'))
const TRIGGER_PCT = Number(arg('trigger-pct', '5'))
const OUT = arg('out', join(HERE, 'out'))
// Mould index the trigger feeds must cross. M3 = mould visible to the naked eye.
const TRIGGER_M = Number(arg('trigger-m', '3'))
// The two material tracks scored for every space (keys of SENSITIVITY_CLASSES).
const SENS = SENSITIVITY_CLASSES[arg('sensitive-class', 'very_sensitive')]
const RESI = SENSITIVITY_CLASSES[arg('resistant-class', 'medium_resistant')]
const HOURS = DAYS * 24
const N_TRIGGER = Math.max(1, Math.round(N_SPACES * TRIGGER_PCT / 100))
if (!SENS || !RESI) throw new Error(`unknown sensitivity class; use one of ${Object.keys(SENSITIVITY_CLASSES).join(', ')}`)

// ── deterministic PRNG (mulberry32) ─────────────────────────────────────
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const rnd = mulberry32(SEED)
const jitter = (amp) => (rnd() - 0.5) * 2 * amp
const pick = (arr) => arr[Math.floor(rnd() * arr.length)]

// ── build the estate procedurally ───────────────────────────────────────
// EPC weighted toward the middle, with a poor-stock tail that gives the damp
// cases somewhere plausible to live.
const EPC_POOL = ['B','B','B','C','C','C','C','D','D','D','D','E','E','F','G']
const ROOM_PLAN = [
  ['bedroom', 'Bedroom'],
  ['bathroom', 'Bathroom'],
  ['kitchen', 'Kitchen'],
  ['living_room', 'Living Room'],
  ['hall', 'Hall'],
  ['study', 'Study'],
  ['store', 'Store'],
]

const slots = []
let dwellingNo = 0
while (slots.length < N_SPACES) {
  dwellingNo++
  const epc = pick(EPC_POOL)
  const size = 3 + Math.floor(rnd() * 3) // 3-5 rooms
  const dwelling = `Flat ${String(dwellingNo).padStart(3, '0')}`
  // Every dwelling has a bedroom + bathroom; the rest vary.
  const rooms = [ROOM_PLAN[0], ROOM_PLAN[1]]
  for (let i = 2; i < size; i++) rooms.push(ROOM_PLAN[2 + Math.floor(rnd() * 5)])
  let bedIdx = 0
  for (const [room_type, label] of rooms) {
    if (slots.length >= N_SPACES) break
    const name = room_type === 'bedroom' ? (bedIdx++ ? `Bedroom ${bedIdx}` : label) : label
    slots.push({ dwelling, epc, room_type, name })
  }
}

// ── assign scenarios ────────────────────────────────────────────────────
// vtt_trigger: sustained severe damp in a cold room, so the index crosses
//   TRIGGER_M *during* the window, from a clean start.
// damp / borderline: real moisture problems that grow some mould but stay
//   below TRIGGER_M.
// healthy: everything else.
const isDampProne = (s) => ['bedroom', 'bathroom'].includes(s.room_type)
const isPoorStock = (s) => ['E', 'F', 'G'].includes(s.epc)

const order = slots.map((s, i) => i)
const triggerIdx = order
  .filter((i) => isDampProne(slots[i]) && isPoorStock(slots[i]))
  .slice(0, N_TRIGGER)
if (triggerIdx.length < N_TRIGGER) {
  for (const i of order.filter((i) => isDampProne(slots[i]))) {
    if (triggerIdx.length >= N_TRIGGER) break
    if (!triggerIdx.includes(i)) triggerIdx.push(i)
  }
}
const remaining = order.filter((i) => !triggerIdx.includes(i))
const nDamp = Math.round(N_SPACES * 0.08)
const nBorderline = Math.round(N_SPACES * 0.17)
const dampIdx = remaining.filter((i) => isDampProne(slots[i])).slice(0, nDamp)
const borderlineIdx = remaining
  .filter((i) => !dampIdx.includes(i) && isDampProne(slots[i]))
  .slice(0, nBorderline)

const SPACES = slots.map((s, i) => {
  const scenario = triggerIdx.includes(i) ? 'vtt_trigger'
    : dampIdx.includes(i) ? 'damp'
    : borderlineIdx.includes(i) ? 'borderline'
    : 'healthy'

  // Profiles: baseTemp, ampTemp, baseRH, ampRH, rhRamp (RH points added
  // linearly across the window: the damp build-up story).
  let p
  if (scenario === 'vtt_trigger') {
    p = { baseTemp: 15.0 + jitter(0.4), ampTemp: 1.1, baseRH: 91 + jitter(1.5), ampRH: 3.2,
          rhRamp: 6 + jitter(1.5) }
  } else if (scenario === 'damp') {
    p = { baseTemp: 15.6 + jitter(0.6), ampTemp: 1.2, baseRH: 71 + jitter(2), ampRH: 4.2,
          rhRamp: 19 + jitter(2) }
  } else if (scenario === 'borderline') {
    p = { baseTemp: 17.5 + jitter(0.8), ampTemp: 1.5, baseRH: 74 + jitter(2), ampRH: 6.5,
          rhRamp: 7 + jitter(2) }
  } else {
    const warm = { A: 20.4, B: 19.8, C: 19.6, D: 19.2, E: 18.6, F: 18.2, G: 17.9 }[s.epc] ?? 19.2
    const dampish = ['bathroom', 'kitchen'].includes(s.room_type)
    p = { baseTemp: warm + jitter(0.9), ampTemp: 1.1,
          baseRH: (dampish ? 55 : 47) + jitter(3), ampRH: dampish ? 7 : 4,
          rhRamp: 1 + jitter(1) }
  }
  return {
    id: `SP-${String(i + 1).padStart(3, '0')}`,
    dwelling: s.dwelling, name: s.name, room_type: s.room_type, epc: s.epc,
    scenario, ...p,
  }
})

// ── outdoor weather: UK winter, diurnal + slow synoptic drift + cold snap ──
function outdoorAt(h) {
  const day = h / 24
  const diurnal = -3.0 * Math.cos(2 * Math.PI * ((h % 24) - 3) / 24)
  const synoptic = 3.2 * Math.sin(2 * Math.PI * day / 11.3) + 1.8 * Math.sin(2 * Math.PI * day / 4.1)
  const snap = day > 22 && day < 31 ? -4.5 : 0
  const temp = 6.4 + diurnal + synoptic + snap + jitter(0.6)
  const rh = Math.min(99, Math.max(58, 84 + 8 * Math.sin(2 * Math.PI * (day + 2) / 6.7) + jitter(3)))
  return { temp, rh, dewpoint: dewpoint(temp, rh) }
}

// ── generate ────────────────────────────────────────────────────────────
mkdirSync(OUT, { recursive: true })
const weather = Array.from({ length: HOURS }, (_, h) => outdoorAt(h))
const tsAt = (h) => new Date(START.getTime() + h * 3600_000)

const rows = []
const vttEvents = []
const state = new Map()
for (const s of SPACES) {
  state.set(s.id, { sens: 0, sensDry: 0, res: 0, resDry: 0, ge80: [], crossed: {} })
}

for (let h = 0; h < HOURS; h++) {
  const ts = tsAt(h)
  const w = weather[h]
  const hourOfDay = ts.getUTCHours()
  const progress = h / (HOURS - 1)

  for (const s of SPACES) {
    const leak = { A: 0.02, B: 0.03, C: 0.05, D: 0.08, E: 0.11, F: 0.14, G: 0.16 }[s.epc] ?? 0.08
    const temp = s.baseTemp
      - s.ampTemp * Math.sin(2 * Math.PI * hourOfDay / 24)
      + leak * (w.temp - 6.4) + jitter(0.3)
    const rh = Math.min(98, Math.max(22,
      s.baseRH + s.ampRH * Math.sin(2 * Math.PI * (hourOfDay - 5) / 24)
      + s.rhRamp * progress + jitter(1.5)))

    const st = state.get(s.id)
    const criticalRH = rhCrit(temp, SENS)
    const dew = dewpoint(temp, rh)
    const dewpointMargin = temp - dew

    const sens = stepVTT(st.sens, temp, rh, st.sensDry, 1, SENS)
    const res = stepVTT(st.res, temp, rh, st.resDry, 1, RESI)
    const trend = sens.m - st.sens

    // Record threshold crossings: the events a downstream consumer cares
    // about. M1 = growth initiated, M3 = visible mould.
    for (const level of [1, 2, 3, 4]) {
      if (!st.crossed[level] && st.sens < level && sens.m >= level) {
        st.crossed[level] = true
        vttEvents.push({
          ts: ts.toISOString(), space_id: s.id, dwelling: s.dwelling,
          space_name: s.name, room_type: s.room_type, epc_rating: s.epc,
          scenario: s.scenario, m_level_crossed: level,
          vtt_sensitive: +sens.m.toFixed(5),
          hours_since_start: h,
          day_of_window: Math.floor(h / 24) + 1,
          temp_c: +temp.toFixed(2), rh_pct: +rh.toFixed(2),
        })
      }
    }

    st.ge80.push(rh >= 80 ? 1 : 0)
    if (st.ge80.length > 168) st.ge80.shift()
    const rhHoursGe80Last7 = st.ge80.reduce((a, b) => a + b, 0)

    st.sens = sens.m; st.sensDry = sens.dryHours
    st.res = res.m; st.resDry = res.dryHours

    rows.push({
      ts: ts.toISOString(), space_id: s.id, dwelling: s.dwelling, space_name: s.name,
      room_type: s.room_type, epc_rating: s.epc, scenario: s.scenario,
      temp_c: +temp.toFixed(2), rh_pct: +rh.toFixed(2),
      dewpoint_c: +dew.toFixed(2), dewpoint_margin_c: +dewpointMargin.toFixed(2),
      critical_rh_pct: +criticalRH.toFixed(2), rh_above_critical: rh >= criticalRH,
      outdoor_temp_c: +w.temp.toFixed(2), outdoor_dewpoint_c: +w.dewpoint.toFixed(2),
      rh_hours_ge80_7d: rhHoursGe80Last7,
      vtt_sensitive: +sens.m.toFixed(5), vtt_resistant: +res.m.toFixed(5),
      vtt_trend: +trend.toFixed(6),
    })
  }
}

// ── daily rollup ────────────────────────────────────────────────────────
const daily = new Map()
for (const r of rows) {
  const k = `${r.space_id}|${r.ts.slice(0, 10)}`
  if (!daily.has(k)) daily.set(k, [])
  daily.get(k).push(r)
}
const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length
const dailyRows = [...daily.entries()].map(([k, rs]) => {
  const [space_id, day] = k.split('|')
  const last = rs[rs.length - 1]
  return {
    day, space_id, dwelling: last.dwelling, space_name: last.space_name,
    room_type: last.room_type, epc_rating: last.epc_rating, scenario: last.scenario,
    temp_mean_c: +mean(rs.map((r) => r.temp_c)).toFixed(2),
    rh_mean_pct: +mean(rs.map((r) => r.rh_pct)).toFixed(2),
    rh_max_pct: +Math.max(...rs.map((r) => r.rh_pct)).toFixed(2),
    dewpoint_mean_c: +mean(rs.map((r) => r.dewpoint_c)).toFixed(2),
    dewpoint_margin_min_c: +Math.min(...rs.map((r) => r.dewpoint_margin_c)).toFixed(2),
    hours_rh_ge_80: rs.filter((r) => r.rh_pct >= 80).length,
    hours_above_critical_rh: rs.filter((r) => r.rh_above_critical).length,
    vtt_sensitive_eod: last.vtt_sensitive, vtt_resistant_eod: last.vtt_resistant,
  }
})

// ── writers ─────────────────────────────────────────────────────────────
const csv = (objs) => {
  const cols = Object.keys(objs[0])
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [cols.join(','), ...objs.map((o) => cols.map((c) => esc(o[c])).join(','))].join('\n') + '\n'
}
const sqlLit = (v) =>
  v === null || v === undefined ? 'NULL'
  : typeof v === 'number' ? String(v)
  : typeof v === 'boolean' ? (v ? 'true' : 'false')
  : `'${String(v).replace(/'/g, "''")}'`
function insertBlock(table, objs, batch = 500) {
  if (!objs.length) return ''
  const cols = Object.keys(objs[0])
  const out = []
  for (let i = 0; i < objs.length; i += batch) {
    out.push(`INSERT INTO ${table} (${cols.join(', ')}) VALUES\n` +
      objs.slice(i, i + batch).map((o) => '  (' + cols.map((c) => sqlLit(o[c])).join(', ') + ')').join(',\n') + ';\n')
  }
  return out.join('\n')
}

const spaceRows = SPACES.map((s) => ({
  space_id: s.id, dwelling: s.dwelling, space_name: s.name, room_type: s.room_type,
  epc_rating: s.epc, scenario: s.scenario,
  base_temp_c: +s.baseTemp.toFixed(2), base_rh_pct: +s.baseRH.toFixed(2),
  rh_ramp_pts: +s.rhRamp.toFixed(2),
}))
const weatherRows = weather.map((w, h) => ({
  ts: tsAt(h).toISOString(), outdoor_temp_c: +w.temp.toFixed(2),
  outdoor_rh_pct: +w.rh.toFixed(2), outdoor_dewpoint_c: +w.dewpoint.toFixed(2),
}))

writeFileSync(join(OUT, 'spaces.csv'), csv(spaceRows))
writeFileSync(join(OUT, 'readings_hourly.csv'), csv(rows))
writeFileSync(join(OUT, 'daily_summary.csv'), csv(dailyRows))
writeFileSync(join(OUT, 'weather_hourly.csv'), csv(weatherRows))
writeFileSync(join(OUT, 'vtt_events.csv'), vttEvents.length ? csv(vttEvents) : 'ts,space_id,m_level_crossed\n')

const ddl = `-- VTT simulator output: self-contained load for a FRESH database.
-- Generated by tools/simulator/generate.mjs
--   window  : ${START.toISOString()} .. ${tsAt(HOURS - 1).toISOString()} (${DAYS} days, hourly)
--   spaces  : ${SPACES.length}   vtt-trigger feeds: ${N_TRIGGER} (${TRIGGER_PCT}%, threshold M>=${TRIGGER_M})
--   seed    : ${SEED}   tracks: ${SENS.name} / ${RESI.name}
-- Scored through the published Hukka-Viitanen model (js/vtt-model.mjs).
-- Synthetic data. Not a record of any real building.

BEGIN;
DROP TABLE IF EXISTS vtt_sim_events, vtt_sim_readings, vtt_sim_daily, vtt_sim_weather, vtt_sim_spaces CASCADE;

CREATE TABLE vtt_sim_spaces (
  space_id text PRIMARY KEY, dwelling text NOT NULL, space_name text NOT NULL,
  room_type text NOT NULL, epc_rating text NOT NULL, scenario text NOT NULL,
  base_temp_c numeric, base_rh_pct numeric, rh_ramp_pts numeric
);
CREATE TABLE vtt_sim_weather (
  ts timestamptz PRIMARY KEY, outdoor_temp_c numeric,
  outdoor_rh_pct numeric, outdoor_dewpoint_c numeric
);
CREATE TABLE vtt_sim_readings (
  ts timestamptz NOT NULL, space_id text NOT NULL REFERENCES vtt_sim_spaces(space_id),
  dwelling text, space_name text, room_type text, epc_rating text, scenario text,
  temp_c numeric, rh_pct numeric, dewpoint_c numeric, dewpoint_margin_c numeric,
  critical_rh_pct numeric, rh_above_critical boolean,
  outdoor_temp_c numeric, outdoor_dewpoint_c numeric,
  rh_hours_ge80_7d integer, vtt_sensitive numeric, vtt_resistant numeric,
  vtt_trend numeric,
  PRIMARY KEY (space_id, ts)
);
CREATE TABLE vtt_sim_daily (
  day date NOT NULL, space_id text NOT NULL REFERENCES vtt_sim_spaces(space_id),
  dwelling text, space_name text, room_type text, epc_rating text, scenario text,
  temp_mean_c numeric, rh_mean_pct numeric, rh_max_pct numeric,
  dewpoint_mean_c numeric, dewpoint_margin_min_c numeric,
  hours_rh_ge_80 integer, hours_above_critical_rh integer,
  vtt_sensitive_eod numeric, vtt_resistant_eod numeric,
  PRIMARY KEY (space_id, day)
);
CREATE TABLE vtt_sim_events (
  ts timestamptz NOT NULL, space_id text NOT NULL REFERENCES vtt_sim_spaces(space_id),
  dwelling text, space_name text, room_type text, epc_rating text, scenario text,
  m_level_crossed integer NOT NULL, vtt_sensitive numeric,
  hours_since_start integer, day_of_window integer,
  temp_c numeric, rh_pct numeric,
  PRIMARY KEY (space_id, m_level_crossed)
);

`
writeFileSync(join(OUT, 'load.sql'), ddl
  + insertBlock('vtt_sim_spaces', spaceRows) + '\n'
  + insertBlock('vtt_sim_weather', weatherRows) + '\n'
  + insertBlock('vtt_sim_readings', rows) + '\n'
  + insertBlock('vtt_sim_daily', dailyRows) + '\n'
  + insertBlock('vtt_sim_events', vttEvents) + '\n'
  + `CREATE INDEX ON vtt_sim_readings (ts);\nCREATE INDEX ON vtt_sim_readings (scenario);\nCOMMIT;\n`)

// ── console summary ─────────────────────────────────────────────────────
console.log(`\nVTT simulator: ${SPACES.length} spaces x ${DAYS} days hourly = ${rows.length.toLocaleString()} readings`)
console.log(`window: ${START.toISOString().slice(0, 10)} .. ${tsAt(HOURS - 1).toISOString().slice(0, 10)}   seed: ${SEED}`)
console.log(`tracks: ${SENS.name} / ${RESI.name}   trigger target: ${N_TRIGGER} feeds (${TRIGGER_PCT}%) must cross M>=${TRIGGER_M}\n`)

const scenDist = SPACES.reduce((a, s) => { a[s.scenario] = (a[s.scenario] ?? 0) + 1; return a }, {})
console.log('scenario mix:', scenDist)

const crossed = vttEvents.filter((e) => e.m_level_crossed >= TRIGGER_M)
const crossedSpaces = [...new Set(crossed.map((e) => e.space_id))]
const onTarget = crossedSpaces.length === N_TRIGGER &&
  crossedSpaces.every((id) => SPACES.find((s) => s.id === id).scenario === 'vtt_trigger')
console.log(`\nfeeds crossing M>=${TRIGGER_M}: ${crossedSpaces.length} / ${SPACES.length}` +
  ` = ${((crossedSpaces.length / SPACES.length) * 100).toFixed(1)}%` +
  (onTarget ? '  on target' : '  OFF TARGET'))
if (crossed.length) {
  console.table(crossed.filter((e) => e.m_level_crossed === TRIGGER_M).map((e) => ({
    space: `${e.dwelling} . ${e.space_name}`, epc: e.epc_rating, scenario: e.scenario,
    crossed_on_day: e.day_of_window, vtt: e.vtt_sensitive, rh: e.rh_pct, temp: e.temp_c,
  })))
}
const evLevels = vttEvents.reduce((a, e) => { a[`M${e.m_level_crossed}`] = (a[`M${e.m_level_crossed}`] ?? 0) + 1; return a }, {})
console.log('all threshold crossings:', evLevels)

const peakBy = {}
for (const r of rows) peakBy[r.scenario] = Math.max(peakBy[r.scenario] ?? 0, r.vtt_sensitive)
console.log('peak index by scenario:', Object.fromEntries(Object.entries(peakBy).map(([k, v]) => [k, +v.toFixed(3)])))
console.log(`\nwrote -> ${OUT}`)
if (!onTarget) process.exitCode = 1
