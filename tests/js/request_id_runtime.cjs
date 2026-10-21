const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../../src/meowkeeper/app/web/static/js/request-id.js'), 'utf8');
const window = { crypto: { getRandomValues: bytes => crypto.randomFillSync(bytes) } };
vm.runInNewContext(source, { window, Uint8Array });
const ids = new Set();
for (let i = 0; i < 1000; i++) {
  const id = window.MeowKeeperRequestId();
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  ids.add(id);
}
assert.equal(ids.size, 1000);
window.crypto.randomUUID = () => 'native-uuid';
assert.equal(window.MeowKeeperRequestId(), 'native-uuid');
delete window.crypto.randomUUID;
delete window.crypto.getRandomValues;
assert.throws(() => window.MeowKeeperRequestId(), /瀏覽器無法建立回報識別碼/);
console.log('passed: LAN HTTP CSPRNG UUID fallback, native API, unavailable API');
