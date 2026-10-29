// Execute the real preview controller, including asynchronous JPEG callbacks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attrs = {};
    this.listeners = {}; this.hidden = false; this.naturalWidth = 1920; this.naturalHeight = 1080;
    this.classes = new Set();
    this.classList = {
      contains: name => this.classes.has(name),
      add: name => this.classes.add(name),
      remove: name => this.classes.delete(name),
      toggle: (name, force) => {
        const enabled = force === undefined ? !this.classes.has(name) : Boolean(force);
        if (enabled) this.classes.add(name); else this.classes.delete(name);
        return enabled;
      },
    };
  }
  contains(node) { return this.children.includes(node); }
  appendChild(node) { this.children.push(node); return node; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; if (key === 'src') delete this.src; }
  addEventListener(key, fn) { this.listeners[key] = fn; }
  removeEventListener(key) { delete this.listeners[key]; }
  closest() { return this.parentElement; }
  pause() {}
  load() {}
  remove() { this.removed = true; }
  async decode() { if (this.decodePromise) await this.decodePromise; }
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function main() {
  const stage = new Element('div'), image = new Element('img');
  image.parentElement = stage;
  image.hidden = true;
  const document = new Element('document'), decodedImages = [], revokedUrls = [];
  document.hidden = false;
  document.body = new Element('body');
  let nextDecode = null;
  document.createElement = tag => {
    const node = new Element(tag);
    if (tag === 'img') {
      if (nextDecode) { node.decodePromise = nextDecode.promise; nextDecode = null; }
      decodedImages.push(node);
    }
    return node;
  };
  const window = new Element('window'), timers = new Map(), frames = [], requests = [];
  let nextTimer = 1, nextObjectUrl = 1;
  let sourceCodec = 'h264';
  const context = {
    window, document, performance: { now: () => 1000 }, AbortController,
    localStorage: { getItem: () => null, setItem() {} },
    URL: { createObjectURL: () => `blob:fixture-${nextObjectUrl++}`, revokeObjectURL: value => revokedUrls.push(value) },
    setTimeout(fn) { const id = nextTimer++; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: async url => {
      requests.push(url);
      return url.endsWith('/status')
        ? { ok: true, json: async () => ({ frame_age_seconds: 0, source_connections: 1, source_status: 'running', source_is_xiaomi: true, stream_name: 'fixture', original_source: { video_codec: sourceCodec } }) }
        : { ok: true, blob: async () => ({ type: 'image/jpeg', size: 1000 }) };
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../src/meowkeeper/app/web/static/js/shared-preview.js'), 'utf8'), context);
  const note = new Element('p');
  const preview = window.MeowKeeperPreview.mount({ image, empty: new Element('div'), note, onFrame: frame => frames.push(frame) });
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  assert.equal(frames.length, 1, 'mount must reach the decoded-frame callback');
  assert.equal(frames[0].width, 1920);
  assert.equal(frames[0].height, 1080);
  assert.equal(image.hidden, false);
  const firstFrame = image.src;
  const pendingDecode = deferred();
  nextDecode = pendingDecode;
  const [timerId, poll] = timers.entries().next().value;
  timers.delete(timerId);
  const nextFrame = poll();
  await settle();
  assert.equal(image.hidden, false, 'the current JPEG remains visible while the next frame decodes');
  assert.equal(image.src, firstFrame, 'the visible source is not replaced before decode completes');
  pendingDecode.resolve();
  await nextFrame;
  assert.equal(image.hidden, false, 'a decoded replacement does not blank the preview');
  assert.notEqual(image.src, firstFrame, 'the new JPEG replaces the old frame after decode');
  assert.ok(revokedUrls.includes(firstFrame), 'the previous object URL is released after replacement');
  assert.equal(decodedImages.length, 2, 'each fetched JPEG is decoded off-screen before display');

  const hud = stage.children.find(node => node.className === 'preview-mode-hud');
  const button = mode => hud.children.find(node => node.dataset.previewMode === mode);
  for (const mode of ['sleep', 'energy', 'smooth']) {
    const control = button(mode);
    assert.equal(control.children.length, 2, `${mode} renders only its icon and tooltip`);
    assert.equal(control.children[0].className, 'preview-mode-icon');
    assert.equal(control.children[1].className, 'preview-mode-tooltip');
    assert.equal(control.attrs['aria-describedby'], control.children[1].id, `${mode} tooltip is accessible`);
  }

  const audioControl = hud.children.find(node => node.dataset.previewControl === 'audio');
  assert.ok(audioControl, 'audio toggle button is rendered on the HUD');
  assert.equal(audioControl.attrs['aria-pressed'], 'false', 'audio toggle defaults to off / muted');
  assert.equal(audioControl.children.length, 2, 'audio toggle renders icon and tooltip');
  assert.equal(audioControl.children[0].className, 'preview-mode-icon');
  assert.equal(audioControl.children[1].className, 'preview-mode-tooltip');
  assert.equal(audioControl.attrs['aria-describedby'], audioControl.children[1].id, 'audio tooltip is accessible');
  assert.equal(preview.isAudioEnabled(), false, 'preview service reports audio is initially disabled');

  await audioControl.listeners.click();
  assert.equal(audioControl.attrs['aria-pressed'], 'true', 'audio button state updates to pressed');
  assert.equal(preview.isAudioEnabled(), true, 'audio state becomes enabled');

  await audioControl.listeners.click();
  assert.equal(audioControl.attrs['aria-pressed'], 'false', 'audio button state updates to not pressed');
  assert.equal(preview.isAudioEnabled(), false, 'audio state returns to disabled');

  const fsControl = hud.children.find(node => node.dataset.previewControl === 'fullscreen');
  assert.ok(fsControl, 'fullscreen toggle button is rendered on the HUD');
  assert.equal(fsControl.attrs['aria-pressed'], 'false', 'fullscreen toggle defaults to not pressed');
  assert.equal(fsControl.children.length, 2, 'fullscreen toggle renders icon and tooltip');
  assert.equal(fsControl.children[0].className, 'preview-mode-icon');
  assert.equal(fsControl.children[1].className, 'preview-mode-tooltip');
  assert.equal(fsControl.attrs['aria-describedby'], fsControl.children[1].id, 'fullscreen tooltip is accessible');
  assert.equal(preview.isFullscreen(), false, 'preview reports not fullscreen');

  let requestedFs = false;
  stage.requestFullscreen = async () => { requestedFs = true; document.fullscreenElement = stage; };
  await fsControl.listeners.click();
  assert.ok(requestedFs, 'clicking fullscreen calls stage.requestFullscreen');
  await button('sleep').listeners.click();
  assert.equal(preview.mode(), 'sleep');
  assert.equal(timers.size, 0, 'sleep cancels polling');
  const before = requests.length;
  await preview.refresh();
  assert.equal(requests.length, before, 'sleep performs no network work');
  await button('smooth').listeners.click();
  await settle();
  assert.equal(preview.mode(), 'energy', 'missing MSE falls back to working JPEG');
  assert.equal(image.hidden, false);
  assert.match(note.textContent, /未載入 MSE/, 'JPEG success preserves the fallback explanation');
  const players=[];
  let supportedCodecs=['hvc1.1.6.L153.B0'];
  window.MeowKeeperMSE={supportedCodecs:()=>supportedCodecs,create(options){const player={options,start(){},close(){this.closed=true;}};players.push(player);return player;}};
  await button('smooth').listeners.click();await settle();
  assert.equal(players.length,1);assert.equal(players[0].options.compatibility,false,'native MSE is tried before transcode');
  players[0].options.onError('native codec unsupported');
  assert.equal(players.length,2);assert.equal(players[1].options.compatibility,true,'one compatibility attempt follows native failure');
  players[1].options.onError('compatibility failed');await settle();
  assert.equal(preview.mode(),'energy');assert.equal(players.length,2,'compatibility failure does not loop');
  sourceCodec='hevc';
  await button('smooth').listeners.click();await settle();
  assert.equal(players.length,3);assert.equal(players[2].options.compatibility,false,'supported HEVC goes directly to original MSE');
  players[2].options.onError('HEVC stream rejected');
  assert.equal(players.length,4);assert.equal(players[3].options.compatibility,true,'HEVC failure gets one shared H.264 fallback');
  players[3].options.onError('compatibility failed');await settle();
  supportedCodecs=[];
  await button('smooth').listeners.click();await settle();
  assert.equal(players.length,5);assert.equal(players[4].options.compatibility,true,'unsupported HEVC bypasses slow original attempts');
  players[4].options.onError('compatibility failed');await settle();
  document.hidden = true;
  document.listeners.visibilitychange();
  assert.equal(timers.size, 0, 'hidden tab cancels polling');
  document.hidden = false;
  document.listeners.visibilitychange();
  await settle();
  assert.equal(image.hidden, false, 'visible tab resumes');
  preview.close();
  assert.equal(timers.size, 0);
  assert.equal(document.listeners.visibilitychange, undefined);
  assert.equal(preview.isDisplaying(), false);
  console.log('passed: mount, decoded JPEG dimensions, sleep, HEVC native/fallback paths, visibility, cleanup');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
