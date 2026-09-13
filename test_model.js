const test = require('node:test')
const assert = require('node:assert/strict')
const Model = require('./Model.js')

test('parseRecord rejects collector output above the byte limit', () => {
  const raw = JSON.stringify({ok: true, padding: 'x'.repeat(256 * 1024)})
  const parsed = Model.parseRecord(raw)
  assert.equal(parsed.ok, false)
  assert.equal(parsed.error, 'bad collector output')
})

test('parseRecord bounds remote arrays and display strings', () => {
  const accounts = Array.from({length: 17}, () => ({
    label: 'a'.repeat(200),
    subscriptions: []
  }))
  accounts[0].subscriptions = Array.from({length: 17}, (_, subscriptionIndex) => ({
    provider: `provider-${subscriptionIndex}`,
    label: 's'.repeat(200),
    windows: Array.from({length: 9}, () => ({name: 'w'.repeat(200), percent: 50}))
  }))

  const parsed = Model.parseRecord(JSON.stringify({ok: true, accounts}))
  assert.ok(parsed.accounts.length <= 16)
  assert.ok(parsed.accounts[0].subscriptions.length <= 16)
  assert.ok(parsed.accounts[0].subscriptions[0].windows.length <= 8)
  assert.ok(parsed.accounts[0].label.length <= 128)
  assert.ok(parsed.accounts[0].subscriptions[0].label.length <= 128)
  assert.ok(parsed.accounts[0].subscriptions[0].windows[0].name.length <= 128)
})

test('parseRecord rejects non-array collection fields', () => {
  const parsed = Model.parseRecord(JSON.stringify({
    ok: true,
    accounts: {length: 1, 0: {label: 'bad'}},
    keys: {length: 1, 0: {label: 'bad'}}
  }))
  assert.deepEqual(parsed.accounts, [])
  assert.deepEqual(parsed.keys, [])
})
