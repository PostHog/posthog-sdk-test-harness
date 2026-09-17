// Native property-matching reproduction, independent of the v2 host and observer.
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

const consumer = resolve(process.argv[2])
const captureMode = process.argv[3] || 'v0'
if (!['v0', 'v1'].includes(captureMode)) throw new Error('Capture mode must be v0 or v1')
const probe = `
const assert = require('node:assert/strict')
const { createServer } = require('node:http')
// The first flag and first getter from the YAML-origin versioned boolean cases.
let definitions = { flags: [{id: 1, name: 'false_banana_exact', key: 'false_banana_exact', active: true, version: 2,
  filters: {groups: [{properties: [{key: 'value', value: false, operator: 'exact', type: 'person'}], rollout_percentage: 100}]}}],
  group_type_mapping: {}, cohorts: {} }
const requests = []
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost')
  const body = JSON.stringify(definitions)
  requests.push({path: url.pathname, authorization: req.headers.authorization, token: url.searchParams.get('token'), definitions: JSON.parse(body)})
  res.setHeader('Content-Type', 'application/json'); res.end(body)
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const sdk = new PostHog('phc_test_key', {host: 'http://127.0.0.1:' + server.address().port, secretKey: 'phx_test_key'})
const results = []
for (const [version, expected] of [[undefined, true], [1, true], [2, false], [1, true], [2, false], [undefined, true]]) {
  delete definitions.property_matching_version
  if (version !== undefined) definitions.property_matching_version = version
  const before = requests.length
  assert.equal(await sdk.reloadFeatureFlags(), undefined)
  assert.equal(await sdk.waitForLocalEvaluationReady(5000), true)
  assert.equal(requests.length, before + 1)
  assert.equal(requests.at(-1).authorization, 'Bearer phx_test_key')
  assert.equal(requests.at(-1).token, 'phc_test_key')
  assert.equal(requests.at(-1).definitions.property_matching_version, version)
  const actual = await sdk.getFeatureFlag('false_banana_exact', 'local-user', {onlyEvaluateLocally: true, personProperties: {value: 'banana'}})
  results.push({property_matching_version: version ?? 'missing', expected, outcome: actual === undefined ? {kind: 'undefined'} : {kind: 'value', value: actual}, conforms: actual === expected})
}
assert.ok(requests.every(r => ['/flags/definitions', '/api/feature_flag/local_evaluation'].includes(r.path.replace(/\\/$/, ''))))
console.log(JSON.stringify({format: FORMAT, captureMode: CAPTURE_MODE, results}))
process.exit(results.every(r => r.conforms) ? 0 : 1)
`
for (const format of ['commonjs', 'esm']) {
  const prelude = `const FORMAT = ${JSON.stringify(format)}; const CAPTURE_MODE = ${JSON.stringify(captureMode)};`
  const code = format === 'commonjs'
    ? `const { PostHog } = require('posthog-node'); ${prelude} (async () => { ${probe} })().catch(e => { console.error(e); process.exit(2) })`
    : `import { PostHog } from 'posthog-node'; import { createRequire } from 'node:module'; const require = createRequire(import.meta.url); ${prelude} ${probe}`
  const result = spawnSync(process.execPath, [`--input-type=${format === 'esm' ? 'module' : 'commonjs'}`, '--eval', code], {
    cwd: consumer, env: { ...process.env, POSTHOG_CAPTURE_MODE: captureMode }, encoding: 'utf8', timeout: 15000,
  })
  process.stdout.write(result.stdout || '')
  process.stderr.write(result.stderr || '')
  if (result.error || ![0, 1].includes(result.status) || !result.stdout.trim()) throw result.error || new Error(`${format} probe failed to complete`)
  JSON.parse(result.stdout.trim())
  if (result.status !== 0) process.exitCode = result.status
}
