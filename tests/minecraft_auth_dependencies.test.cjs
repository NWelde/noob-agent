// Import and construct the actual auth dependency graph without network calls.
const test = require('node:test')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const path = require('node:path')
const sidecarRequire = createRequire(path.resolve(__dirname,
  '../src/noob_agent/connectors/minecraft_sidecar/package.json'))

test('patched UUID remains compatible with Mineflayer authentication imports', () => {
  const mineflayer = sidecarRequire('mineflayer')
  const auth = sidecarRequire('prismarine-auth')
  const yggdrasil = sidecarRequire('yggdrasil')
  const uuid = sidecarRequire('uuid')
  const { PublicClientApplication } = sidecarRequire('@azure/msal-node')
  assert.equal(typeof mineflayer.createBot, 'function')
  assert.equal(typeof auth.Authflow, 'function')
  assert.equal(typeof yggdrasil, 'function')
  const identifier = uuid.v4()
  assert.equal(uuid.validate(identifier), true)
  assert.equal(uuid.version(identifier), 4)
  const client = new PublicClientApplication({ auth: { clientId: identifier } })
  assert.ok(client.getTokenCache())
})
