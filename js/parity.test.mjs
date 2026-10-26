// JS vs Python parity: the two implementations must give the same index,
// hour by hour, over the same readings. Needs python3 on the PATH.
// Run: npm test   (or: node --test js/)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { SENSITIVITY_CLASSES, stepVTT } from './vtt-model.mjs'

const PY_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'python')

// A fortnight of hourly readings that crosses the critical curve both ways:
// cold damp nights, dry heated days, a dry spell long enough to reach every
// decline band, and a few readings below 0 C and above 20 C.
function series() {
  const out = []
  for (let h = 0; h < 24 * 14; h++) {
    const hour = h % 24
    const night = hour < 7 || hour >= 22
    const dry = h >= 24 * 9 && h < 24 * 11
    let t = night ? 14.5 : 19.5
    let rh = dry ? 50 : night ? 92 : 62
    if (h % 97 === 0) t = -1
    if (h % 89 === 0) { t = 23; rh = 88 }
    out.push([t, rh + Math.sin(h / 5) * 2])
  }
  return out
}

function runJs(cls, readings) {
  let m = 0, dry = 0
  return readings.map(([t, rh]) => {
    ;({ m, dryHours: dry } = stepVTT(m, t, rh, dry, 1, SENSITIVITY_CLASSES[cls]))
    return m
  })
}

function runPython(cls, readings) {
  const script = [
    'import json, sys',
    'from vtt_hukka_viitanen import MouldModel',
    'cls, readings = json.load(sys.stdin)',
    'model = MouldModel(cls)',
    'print(json.dumps([model.step(t, rh, 1.0) for t, rh in readings]))',
  ].join('\n')
  const out = execFileSync('python3', ['-I', '-c', `import sys; sys.path.insert(0, ${JSON.stringify(PY_DIR)})\n${script}`], {
    input: JSON.stringify([cls, readings]),
    encoding: 'utf8',
  })
  return JSON.parse(out)
}

for (const cls of Object.keys(SENSITIVITY_CLASSES)) {
  test(`parity: JS and Python agree hour by hour (${cls})`, () => {
    const readings = series()
    const js = runJs(cls, readings)
    const py = runPython(cls, readings)
    assert.equal(py.length, js.length)
    for (let i = 0; i < js.length; i++) {
      assert.ok(Math.abs(js[i] - py[i]) < 1e-12, `hour ${i}: JS ${js[i]} vs Python ${py[i]}`)
    }
    assert.ok(Math.max(...js) > 0, 'the series should grow some mould, or the test proves nothing')
  })
}
