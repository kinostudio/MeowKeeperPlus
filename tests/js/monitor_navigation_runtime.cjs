const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attrs = {}; this.events = new Map();
    this.hidden = false; this.isConnected = true; this.naturalWidth = 1920; this.naturalHeight = 1080;
    this.videoWidth = 1920; this.videoHeight = 1080;
    const classes = new Set();
    this.classList = { contains: key => classes.has(key), add: key => classes.add(key), remove: key => classes.delete(key),
      toggle(key, force) { const next = force ?? !classes.has(key); if (next) classes.add(key); else classes.delete(key); return next; } };
  }
  append(...nodes) { for (const node of nodes) { node.remove(); this.children.push(node); node.parentElement = this; } }
  moveBefore(node) { this.append(node); }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this); this.parentElement = null; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  hasAttribute(key) { return key in this.attrs; }
  removeAttribute(key) { delete this.attrs[key]; if (key === 'src') delete this.src; }
  addEventListener(key, fn) { if (!this.events.has(key)) this.events.set(key, new Set()); this.events.get(key).add(fn); }
  removeEventListener(key, fn) { this.events.get(key)?.delete(fn); }
  dispatchEvent(event) { for (const fn of [...(this.events.get(event.type) || [])]) fn(event); }
  closest(selector) {
    if (selector === 'a[href]') return this.tagName === 'a' ? this : null;
    let node = this;
    while (node) {
      if (selector === '[data-monitor-view]' ? node.dataset.monitorView : ['preview-stage', 'overview-preview'].includes(node.className)) return node;
      node = node.parentElement;
    }
    return null;
  }
  pause() { this.pauses = (this.pauses || 0) + 1; }
  load() { this.loads = (this.loads || 0) + 1; }
  async play() { this.plays = (this.plays || 0) + 1; }
  async decode() {}
  focus() {}
  scrollIntoView() {}
}
async function main() {
  const overview = new Element(), live = new Element();
  overview.dataset.monitorView = 'overview'; live.dataset.monitorView = 'live'; live.hidden = true;
  const homeStage = new Element(), liveStage = new Element(), homeImage = new Element('img'), liveImage = new Element('img');
  homeStage.className = 'overview-preview'; liveStage.className = 'preview-stage';
  overview.append(homeStage); homeStage.append(homeImage); live.append(liveStage); liveStage.append(liveImage);
  const link = href => { const node = new Element('a'); node.href = href; node.attrs.href = href; return node; };
  const homeLink = link('http://test/dashboard'), liveLink = link('http://test/live');
  const document = new Element(), window = new Element(), mainContent = new Element();
  document.hidden = false; document.createElement = tag => new Element(tag);
  document.querySelectorAll = selector => selector === '[data-monitor-view]' ? [overview, live] : [homeLink, liveLink];
  document.getElementById = id => id === 'main-content' ? mainContent : null;
  const location = { origin: 'http://test', pathname: '/dashboard', href: 'http://test/dashboard', search: '', hash: '' };
  const history = { pushState(_state, _title, url) { Object.assign(location, new URL(url, location.href)); location.href = new URL(url, 'http://test').href; location.pathname = new URL(url, 'http://test').pathname; } };
  window.scrollTo = () => {};
  let timerId = 0, created = 0, closed = 0, requests = 0;
  const timers = new Map(), players = [];
  window.MeowKeeperMSE = { supportedCodecs: () => ['avc1.640029', 'mp4a.40.2'], create(options) {
    created++;
    const player = { options, start() { options.video.src = 'blob:continuous-session'; }, close() { closed++; } };
    players.push(player); return player;
  } };
  const context = { window, document, location, history, URL: Object.assign(URL, { createObjectURL: () => 'blob:jpeg', revokeObjectURL() {} }),
    AbortController, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    localStorage: { getItem: () => null, setItem() {} }, performance: { now: () => 1000 },
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); },
    fetch: async url => { requests++; return url.endsWith('/status') ? { ok: true, json: async () => ({ source_status: 'running', frame_age_seconds: 0,
      source_connections: 1, source_is_xiaomi: true, stream_name: 'test_source', original_source: { video_codec: 'h264' } }) }
      : { ok: true, blob: async () => ({ type: 'image/jpeg', size: 100 }) }; }
  };
  for (const name of ['monitor-navigation.js', 'shared-preview.js']) vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../src/meowkeeper/app/web/static/js', name), 'utf8'), context);
  const home = window.MeowKeeperPreview.mount({ image: homeImage, scope: 'overview', defaultMode: 'energy' });
  const detail = window.MeowKeeperPreview.mount({ image: liveImage, scope: 'live', defaultMode: 'smooth' });
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  const hud = () => (overview.hidden ? liveStage : homeStage).children.find(node => node.className === 'preview-mode-hud');
  const mode = key => hud().children.find(node => node.dataset.previewMode === key);
  await [...mode('smooth').events.get('click')][0](); await settle();
  assert.equal(created, 1);
  const video = homeStage.children.find(node => node.tagName === 'video');
  const pauseCount = video.pauses;
  players[0].options.onAudio({ available: true, codec: 'mp4a.40.2' });
  const audio = hud().children.find(node => node.dataset.previewControl === 'audio');
  await [...audio.events.get('click')][0]();
  assert.equal(home.isAudioEnabled(), true);
  const click = node => { const event = { type: 'click', button: 0, target: node, preventDefault() { this.defaultPrevented = true; } }; document.dispatchEvent(event); return event; };
  const before = requests;
  for (let index = 0; index < 12; index++) {
    const target = index % 2 ? homeLink : liveLink;
    assert.equal(click(target).defaultPrevented, true);
    assert.equal((index % 2 ? homeStage : liveStage).children.includes(video), true);
    assert.equal(video.src, 'blob:continuous-session');
    assert.equal(video.pauses, pauseCount, 'navigation must not pause/close the controller');
    assert.equal(created, 1); assert.equal(closed, 0);
    assert.equal(detail.isAudioEnabled(), true);
  }
  assert.equal(requests, before, 'navigation itself does not fetch/reconnect media');
  location.pathname = '/live'; location.href = 'http://test/live'; window.dispatchEvent({ type: 'popstate' });
  assert.equal(live.hidden, false); assert.equal(overview.hidden, true); assert.equal(created, 1);
  assert.equal(click(link('http://test/settings/ai')).defaultPrevented, undefined, 'other pages use normal navigation and pagehide cleanup');
  await [...mode('sleep').events.get('click')][0]();
  assert.equal(closed, 1); const sleepingRequests = requests;
  click(homeLink); click(liveLink);
  assert.equal(detail.mode(), 'sleep'); assert.equal(created, 1); assert.equal(requests, sleepingRequests);
  window.dispatchEvent({ type: 'pagehide' }); window.dispatchEvent({ type: 'pagehide' });
  assert.equal(closed, 1, 'page exit is idempotent and does not reopen sleeping media');
  console.log('passed: persistent monitor navigation, one session, same video, audio retention, history, sleep and cleanup');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
