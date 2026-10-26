const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Events {
  constructor() { this.events = {}; }
  addEventListener(name, fn) { this.events[name] = fn; }
  fire(name, value) { this.events[name]?.(value); }
}
class Buffer extends Events {
  constructor() {
    super(); this.updating = false; this.start = 0; this.removes = 0; this.appends = 0;
    this.buffered = { length: 1, start: () => this.start, end: () => 30 };
  }
  appendBuffer() { this.appends++; this.updating = true; }
  remove(start, end) { this.removes++; this.start = end; this.updating = true; }
  finish() { this.updating = false; this.fire('updateend'); }
}
let buffer, socket, frameCallback;
class Media extends Events {
  static isTypeSupported() { return true; }
  constructor() { super(); this.readyState = 'open'; }
  addSourceBuffer() { buffer = new Buffer(); return buffer; }
}
class Socket extends Events {
  static CLOSING = 2;
  constructor() { super(); this.readyState = 1; socket = this; }
  send(value) { this.offer = JSON.parse(value); }
  close() { this.readyState = 3; }
}
const video = new Events();
Object.assign(video, { currentTime: 26, paused: false, play: async () => {}, pause() {}, load() {}, removeAttribute() {}, requestVideoFrameCallback(callback) { frameCallback = callback; return 1; }, cancelVideoFrameCallback() {} });
const window = { MediaSource: Media }, errors = [];
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../src/meowkeeper/app/web/static/js/mse-player.js'), 'utf8'), {
  window, MediaSource: Media, WebSocket: Socket, ArrayBuffer,
  location: { protocol: 'http:', host: 'localhost:8000' },
  URL: { createObjectURL: () => 'blob:fixture', revokeObjectURL() {} },
  setTimeout: () => 1, clearTimeout() {}, clearInterval() {}, performance: { now: () => 1000 }
});
const stats = [], audio = [];
const player = window.MeowKeeperMSE.create({ video, onError: message => errors.push(message), onStats: value => stats.push(value), onAudio: value => audio.push(value) });
player.start();
socket.fire('open');
assert.ok(socket.offer.value.includes('mp4a.40.2'), 'MSE offer must include AAC audio, not just video');
socket.fire('message', { data: JSON.stringify({ type: 'mse', value: 'video/mp4; codecs="hvc1.1.6.L153.B0,mp4a.40.2"' }) });
assert.equal(audio.at(-1).available, true);
assert.equal(audio.at(-1).codec, 'mp4a.40.2');
socket.fire('message', { data: new ArrayBuffer(32) });
assert.equal(buffer.appends, 1);
buffer.finish(); // append completion prunes old media once
assert.equal(buffer.removes, 1);
buffer.finish(); // pruning completion must not recursively prune the same range
assert.equal(buffer.removes, 1, 'no repeated remove/updateend loop');
socket.fire('message', { data: new ArrayBuffer(32) });
assert.equal(buffer.appends, 2, 'new segments continue after pruning');
buffer.finish();
assert.equal(buffer.removes, 1);
assert.deepEqual(errors, []);
frameCallback(1000, { presentedFrames: 10 });
frameCallback(2000, { presentedFrames: 30 });
assert.equal(stats.at(-1).fps, 20, 'FPS counts presented frames even when callbacks are coalesced');
player.close();
const silent = window.MeowKeeperMSE.create({ video, onAudio: value => audio.push(value) });
silent.start();
socket.fire('message', { data: JSON.stringify({ type: 'mse', value: 'video/mp4; codecs="avc1.640029"' }) });
assert.equal(audio.at(-1).available, false, 'video-only negotiation must not claim audio success');
silent.close();
console.log('passed: MSE pruning resumes appends and FPS uses presented-frame deltas');
