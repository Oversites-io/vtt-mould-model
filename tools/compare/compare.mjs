#!/usr/bin/env node
/**
 * Simplified vs published mould model, over the simulator's output.
 *
 * Replays every space in tools/simulator/out/readings_hourly.csv from M=0
 * under both models and reports the gap.
 *
 *   node tools/simulator/generate.mjs     # first, if out/ is empty
 *   node tools/compare/compare.mjs [--readings path/to/readings_hourly.csv]
 */
import { readFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { replayZone } from './replay.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const i = argv.indexOf('--readings')
const path = i >= 0 ? argv[i + 1] : join(HERE, '..', 'simulator', 'out', 'readings_hourly.csv')
if (!existsSync(path)) {
  console.error(`no readings at ${path}: run node tools/simulator/generate.mjs first`)
  process.exit(1)
}

// The simulator's CSV has no quoted fields, so a plain split is safe here.
const [header, ...lines] = readFileSync(path, 'utf8').trimEnd().split('\n')
const col = Object.fromEntries(header.split(',').map((c, n) => [c, n]))
const bySpace = new Map()
for (const line of lines) {
  const f = line.split(',')
  const id = f[col.space_id]
  if (!bySpace.has(id)) {
    bySpace.set(id, { name: `${f[col.dwelling]} . ${f[col.space_name]}`, scenario: f[col.scenario], rows: [] })
  }
  bySpace.get(id).rows.push({ bucketStart: f[col.ts], tempC: Number(f[col.temp_c]), rhPct: Number(f[col.rh_pct]) })
}

const results = []
for (const [id, s] of bySpace) {
  const r = replayZone(s.rows, s.rows.length)
  results.push({
    id, name: s.name, scenario: s.scenario, hours: r.hours,
    sAbove: r.simplifiedRhAboveHours, pAbove: r.publishedRhAboveHours,
    sMax: r.simplifiedIndexMax, pMax: r.publishedIndexMax,
  })
}

const fmt = (n, w, dp = 3) => n.toFixed(dp).padStart(w)
console.log(`\nSimplified lookup-curve model  vs  published Hukka-Viitanen (very sensitive)`)
console.log(`${results.length} spaces, both models from M=0.\n`)
console.log(`${'space'.padEnd(24)}${'scenario'.padEnd(13)}${'h>crit simp'.padStart(12)}${'h>crit pub'.padStart(12)}` +
  `${'peak M simp'.padStart(13)}${'peak M pub'.padStart(12)}`)
console.log('-'.repeat(86))
for (const r of results.slice().sort((a, b) => b.pMax - a.pMax).slice(0, 15)) {
  console.log(`${r.name.padEnd(24)}${r.scenario.padEnd(13)}${String(r.sAbove).padStart(12)}${String(r.pAbove).padStart(12)}` +
    `${fmt(r.sMax, 13)}${fmt(r.pMax, 12)}`)
}

const reached = (k, m) => results.filter((r) => r[k] >= m).length
console.log(`\nSpaces reaching growth initiated (M1)   simplified ${reached('sMax', 1)}   published ${reached('pMax', 1)}`)
console.log(`Spaces reaching visible mould (M3)      simplified ${reached('sMax', 3)}   published ${reached('pMax', 3)}`)

const hiddenCold = results.filter((r) => r.sAbove === 0 && r.pAbove > 0)
console.log(`Spaces above the published curve that the simplified curve never flags: ${hiddenCold.length}`)

// Published / simplified peak index, by scenario. Above 1: the simplified model
// under-predicts; below 1: it holds on to growth the published decline erases.
console.log('\nPublished / simplified peak index, by scenario:')
for (const scenario of [...new Set(results.map((r) => r.scenario))]) {
  const ratios = results
    .filter((r) => r.scenario === scenario && r.sMax > 1e-6 && r.pMax > 1e-6)
    .map((r) => r.pMax / r.sMax)
    .sort((a, b) => a - b)
  if (!ratios.length) continue
  console.log(`  ${scenario.padEnd(12)} median ${ratios[Math.floor(ratios.length / 2)].toFixed(2)}x  ` +
    `range ${ratios[0].toFixed(2)}x to ${ratios[ratios.length - 1].toFixed(2)}x  (${ratios.length} spaces)`)
}
console.log('')
