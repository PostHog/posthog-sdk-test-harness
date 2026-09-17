// Exercise both public Node export conditions in the installed consumer.
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

const consumer = resolve(process.argv[2])
const captureMode = process.argv[3] || 'v0'
if (!['v0', 'v1'].includes(captureMode)) throw new Error('Capture mode must be v0 or v1')
const capturePath = captureMode === 'v0' ? '/batch' : '/i/v1/analytics/events'
const smoke = `
const assert = require('node:assert/strict')
const { createServer } = require('node:http')
const { gunzipSync } = require('node:zlib')
const requests = []
const server = createServer(async (req, res) => {
  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  const raw = Buffer.concat(chunks)
  const body = JSON.parse(req.headers['content-encoding'] === 'gzip' ? gunzipSync(raw) : raw)
  requests.push({ path: req.url, body })
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(req.url.startsWith('/flags') ? { featureFlags: { calibration: 'variant-a' } } : { status: 1 }))
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const sdk = new PostHog('phc_calibration_fixture', { host: 'http://127.0.0.1:' + server.address().port })
assert.equal(sdk.capture({ distinctId: 'calibration-user', event: 'calibration-event', properties: { no: false, zero: 0, nil: null } }), undefined)
const aiUuid = '0198c0de-0000-7000-8000-000000000abc'
assert.equal(sdk.captureAi({ distinctId: 'calibration-user', event: '$ai_generation', uuid: aiUuid }), aiUuid)
assert.equal(await sdk.getFeatureFlag('calibration', 'calibration-user'), 'variant-a')
assert.equal(await sdk.getFeatureFlag('calibration', 'calibration-user'), 'variant-a')
assert.equal(await sdk.getFeatureFlag('missing', 'calibration-user'), undefined)
assert.equal(await sdk.flush(), undefined)
const flags = requests.filter(r => r.path.startsWith('/flags'))
assert.equal(flags.length, 3)
assert.deepEqual(flags.slice(0, 2).map(r => r.body.flag_keys_to_evaluate), [['calibration'], ['calibration']])
assert.equal(flags[0].body.geoip_disable, true)
const event = requests.filter(r => r.path.startsWith(CAPTURE_PATH)).flatMap(r => r.body.batch).find(e => e.event === 'calibration-event')
assert.ok(event)
assert.equal(event.properties.no, false)
assert.equal(event.properties.zero, 0)
assert.equal(event.properties.nil, null)
const aiEvents = requests.filter(r => r.path === '/i/v0/ai/batch/').flatMap(r => r.body.batch)
assert.equal(aiEvents.length, 1)
assert.equal(aiEvents[0].uuid, aiUuid)
console.log(JSON.stringify({ format: FORMAT, captureMode: CAPTURE_MODE, aiCapture: 'native UUID matches AI endpoint', flags: flags.length, capture: 'native void', flush: 'native async void', missingFlag: 'undefined', defaultGeoipDisable: true }))
// End the isolated smoke process, rather than testing an unrelated shutdown API.
process.exit(0)
`
for (const format of ['commonjs', 'esm']) {
  const code = format === 'commonjs'
    ? `const { PostHog } = require('posthog-node'); const FORMAT = 'commonjs'; const CAPTURE_PATH = ${JSON.stringify(capturePath)}; const CAPTURE_MODE = ${JSON.stringify(captureMode)}; (async () => { ${smoke} })().catch(e => { console.error(e); process.exit(1) })`
    : `import { PostHog } from 'posthog-node'; import { createRequire } from 'node:module'; const require = createRequire(import.meta.url); const FORMAT = 'esm'; const CAPTURE_PATH = ${JSON.stringify(capturePath)}; const CAPTURE_MODE = ${JSON.stringify(captureMode)}; ${smoke}`
  const result = spawnSync(process.execPath, [format === 'esm' ? '--input-type=module' : '--input-type=commonjs', '--eval', code], {
    cwd: consumer, env: { ...process.env, POSTHOG_CAPTURE_MODE: captureMode }, encoding: 'utf8', timeout: 10000,
  })
  process.stdout.write(result.stdout || '')
  process.stderr.write(result.stderr || '')
  if (result.error || result.status !== 0) throw result.error || new Error(`${format} smoke failed`)
}
