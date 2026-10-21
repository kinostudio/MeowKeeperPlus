const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require(process.env.MEOWKEEPER_PLAYWRIGHT || "playwright");

const SCRIPT = path.resolve(__dirname, "../../src/meowkeeper/app/web/static/js/dashboard-live-metrics.js");
const LAYOUT_SCRIPT = path.resolve(__dirname, "../../src/meowkeeper/app/web/static/js/dashboard-home-layout.js");
const STYLE_FILES = [
  "../../src/meowkeeper/app/web/static/style.css",
  "../../src/meowkeeper/app/web/static/css/experience.css",
  "../../src/meowkeeper/app/web/static/css/redesign.css",
  "../../src/meowkeeper/app/web/static/css/vision.css",
  "../../src/meowkeeper/app/web/static/css/term-help.css",
  "../../src/meowkeeper/app/web/static/css/monitor.css"
].map(file => path.resolve(__dirname, file));
const EDGE = process.env.MEOWKEEPER_EDGE || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const markup = `<!doctype html><meta charset="utf-8">
<div id="hardware-sample-clock"><svg><circle id="sample-countdown-ring"></circle></svg><span id="sample-countdown"></span><span id="overview-updated"></span><span id="sample-countdown-label"></span></div>
<div class="live-usage-ring"><svg><circle id="cpu-usage-ring"></circle><circle id="overview-ram-ring"></circle><circle id="overview-gpu-ring"></circle></svg></div>
<strong id="system-status" data-rolling-number>—</strong><strong id="overview-ram-percent" data-rolling-number>—</strong><strong id="overview-gpu-usage" data-rolling-number>—</strong><strong id="overview-gpu-name"></strong><span id="overview-gpu-state"></span><span id="overview-gpu-detail"></span>
<div class="temperature-dial"><svg><path id="cpu-temp-arc"></path><line id="cpu-temp-needle"></line></svg><strong id="cpu-temp-value" data-rolling-number>—</strong></div>
<div class="temperature-dial"><svg><path id="gpu-temp-arc"></path><line id="gpu-temp-needle"></line></svg><strong id="gpu-temp-value" data-rolling-number>—</strong></div>
<span id="temperature-sample-note"></span>`;

async function main() {
  const browser = await chromium.launch({headless: true, executablePath: EDGE});
  try {
    const page = await browser.newPage({reducedMotion: "no-preference"});
    await page.setContent(markup);
    for (const file of STYLE_FILES) await page.addStyleTag({path: file});
    await page.addScriptTag({path: SCRIPT});
    const firstSample = Date.now() / 1000 - 5;
    await page.evaluate(sampledAt => document.dispatchEvent(new CustomEvent("meowkeeper:status", {detail: {
      metrics: {
        system: {cpu_percent: 18.2, ram_percent: 42.7},
        ai: {device: "NVIDIA GeForce MX250"},
        telemetry: {sampled_at: sampledAt, system_gpu_percent: 8.3, gpu_name: "NVIDIA GeForce MX250", cpu_temperature_c: 51, gpu_temperature_c: 44}
      }
    }})), firstSample);
    const initial = await page.evaluate(() => ({
      cpu: document.getElementById("cpu-usage-ring").style.strokeDashoffset,
      gpu: document.getElementById("overview-gpu-ring").dataset.state,
      cpuNeedle: document.getElementById("cpu-temp-needle").getAttribute("transform"),
      cpuValue: document.getElementById("cpu-temp-value").getAttribute("aria-label")
    }));
    assert.equal(initial.gpu, "ready");
    assert.equal(initial.cpuValue, "51.0°C");

    const secondSample = Date.now() / 1000;
    const beforeRoll = await page.evaluate(() => [...document.querySelectorAll("#system-status .odometer-strip")]
      .map(strip => ({target: strip.style.transform, computed: getComputedStyle(strip).transform})));
    await page.evaluate(sampledAt => document.dispatchEvent(new CustomEvent("meowkeeper:status", {detail: {
      metrics: {
        system: {cpu_percent: 37.6, ram_percent: 61.1},
        ai: {device: "NVIDIA GeForce MX250"},
        telemetry: {sampled_at: sampledAt, system_gpu_percent: 24.8, gpu_name: "NVIDIA GeForce MX250", cpu_temperature_c: 67, gpu_temperature_c: 52}
      }
    }})), secondSample);
    await page.waitForTimeout(100);
    const rolling = await page.evaluate(beforeRoll => {
      const strip = document.querySelector("#system-status .odometer-strip");
      const animations = strip?.getAnimations() || [];
      const transition = animations.find(animation => animation.playState === "running");
      return {active: Boolean(transition), elapsed: transition?.currentTime ?? null,
        transitionDuration: strip ? getComputedStyle(strip).transitionDuration : null,
        transitionProperty: strip ? getComputedStyle(strip).transitionProperty : null,
        computedTransform: strip ? getComputedStyle(strip).transform : null,
        targetTransform: strip?.style.transform,
        beforeRoll,
        animations: animations.map(animation => ({state: animation.playState, currentTime: animation.currentTime})),
        directions: [...document.querySelectorAll("#system-status .odometer-strip")].map(item => item.dataset.direction)};
    }, beforeRoll);
    assert.equal(rolling.active, true, JSON.stringify(rolling));
    assert.ok(rolling.elapsed > 0 && rolling.elapsed < 720, JSON.stringify(rolling));
    await page.waitForTimeout(750);
    const changed = await page.evaluate(() => ({
      cpu: document.getElementById("cpu-usage-ring").style.strokeDashoffset,
      cpuValue: document.getElementById("system-status").getAttribute("aria-label"),
      cpuNeedle: document.getElementById("cpu-temp-needle").getAttribute("transform"),
      cpuDirections: [...document.querySelectorAll("#system-status .odometer-strip")].map(strip => strip.dataset.direction),
      cpuTransforms: [...document.querySelectorAll("#system-status .odometer-strip")].map(strip => strip.style.transform),
      cpuTransition: getComputedStyle(document.querySelector("#system-status .odometer-strip")).transitionDuration,
      sampleTime: document.getElementById("overview-updated").textContent,
      countdown: document.getElementById("sample-countdown").textContent
    }));
    assert.notEqual(changed.cpu, initial.cpu);
    assert.equal(changed.cpuValue, "37.6%");
    assert.deepEqual(changed.cpuDirections, ["up", "down", "up"]);
    assert.notDeepEqual(changed.cpuTransforms, []);
    assert.equal(changed.cpuTransition, "0.72s");
    assert.notEqual(changed.cpuNeedle, initial.cpuNeedle);
    assert.match(changed.sampleTime, /最近採樣：/);
    assert.ok(["4", "5"].includes(changed.countdown));

    await page.evaluate(() => document.dispatchEvent(new CustomEvent("meowkeeper:status", {detail: {
      metrics: {system: {cpu_percent: 12, ram_percent: 30}, telemetry: {sampled_at: Date.now() / 1000, system_gpu_percent: null, cpu_temperature_c: null, gpu_temperature_c: null}}
    }})));
    const missing = await page.evaluate(() => ({
      gpuState: document.getElementById("overview-gpu-ring").dataset.state,
      gpuValue: document.getElementById("overview-gpu-usage").getAttribute("aria-label"),
      cpuTemp: document.getElementById("cpu-temp-value").getAttribute("aria-label"),
      gpuNeedle: document.getElementById("gpu-temp-needle").dataset.state
    }));
    assert.equal(missing.gpuState, "unavailable");
    assert.equal(missing.gpuValue, "尚未感測");
    assert.equal(missing.cpuTemp, "尚未感測");
    assert.equal(missing.gpuNeedle, "unavailable");

    const layoutPage = await browser.newPage({viewport: {width: 1280, height: 900}});
    await layoutPage.setContent(`<!doctype html><meta charset="utf-8">
      <style>body{margin:0}main{width:min(1000px,100vw);margin:auto}</style>
      <main>
        <div class="overview-grid dashboard-hero-grid">
          <section class="panel dashboard-preview-panel">
            <div class="overview-preview"><img id="overview-frame" alt="與即時畫面共用的最新來源影格"></div>
          </section>
          <section class="panel dashboard-events-panel">Events</section>
        </div>
        <section class="panel dashboard-system" aria-label="即時硬體用量">
          <div class="panel-heading"><h3>即時硬體用量</h3><div class="dashboard-system-tools">
            <div id="hardware-sample-clock" class="sample-clock-live">Sample</div><button>Full status</button>
          </div></div>
          <div class="overview-kpis"><article class="overview-kpi">CPU</article><article class="overview-kpi">RAM</article>
            <article class="overview-kpi">GPU</article><article class="overview-kpi">Temperature</article></div>
        </section>
      </main>`);
    for (const file of STYLE_FILES) await layoutPage.addStyleTag({path: file});
    await layoutPage.addScriptTag({path: LAYOUT_SCRIPT});
    const desktopLayout = await layoutPage.evaluate(() => {
      const hero = document.querySelector(".dashboard-hero-grid");
      const preview = hero.querySelector(".dashboard-preview-panel");
      const hardware = hero.querySelector(".dashboard-system");
      const events = hero.querySelector(".dashboard-events-panel");
      const image = document.getElementById("overview-frame");
      const heading = hardware.querySelector(".panel-heading").getBoundingClientRect();
      const tools = hardware.querySelector(".dashboard-system-tools").getBoundingClientRect();
      return {
        state: hero.dataset.layoutState,
        order: [...hero.children].map(child => child.classList.contains("dashboard-preview-panel") ? "preview"
          : child.classList.contains("dashboard-system") ? "hardware" : "events"),
        previewWidth: preview.getBoundingClientRect().width,
        hardwareWidth: hardware.getBoundingClientRect().width,
        ratio: preview.getBoundingClientRect().width / hardware.getBoundingClientRect().width,
        imageFit: getComputedStyle(image).objectFit,
        previewAspect: getComputedStyle(image.parentElement).aspectRatio,
        timerInHeading: Boolean(hardware.querySelector(".panel-heading #hardware-sample-clock")),
        toolsAtHeadingRight: tools.left >= heading.left + heading.width * 0.45
      };
    });
    assert.equal(desktopLayout.state, "ready");
    assert.deepEqual(desktopLayout.order, ["preview", "hardware", "events"]);
    assert.ok(Math.abs(desktopLayout.ratio - 1.5) < 0.02, JSON.stringify(desktopLayout));
    assert.equal(desktopLayout.imageFit, "contain");
    assert.equal(desktopLayout.previewAspect, "16 / 9");
    assert.equal(desktopLayout.timerInHeading, true);
    assert.equal(desktopLayout.toolsAtHeadingRight, true);

    await layoutPage.setViewportSize({width: 390, height: 844});
    const mobileLayout = await layoutPage.evaluate(() => {
      const hero = document.querySelector(".dashboard-hero-grid");
      const panels = [...hero.children].map(child => child.getBoundingClientRect());
      const image = document.getElementById("overview-frame");
      return {
        widths: panels.map(rect => Math.round(rect.width)),
        stacked: panels[0].top < panels[1].top && panels[1].top < panels[2].top,
        imageFit: getComputedStyle(image).objectFit,
        previewAspect: getComputedStyle(image.parentElement).aspectRatio,
        horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth
      };
    });
    assert.equal(mobileLayout.stacked, true);
    assert.equal(mobileLayout.imageFit, "contain");
    assert.equal(mobileLayout.previewAspect, "16 / 9");
    assert.equal(mobileLayout.horizontalOverflow, false);
    await layoutPage.close();
    // Run the actual Hero builder before the animation module, as base.html does.
    const heroPage = await browser.newPage({viewport: {width: 1440, height: 810}});
    await heroPage.setContent(`<!doctype html><meta charset="utf-8"><main>
      <section class="dashboard-primary"></section>
      <section class="dashboard-preview-panel"><div id="overview-preview" class="overview-preview"></div></section>
      <section class="dashboard-system">${markup}</section></main>`);
    for (const file of STYLE_FILES) await heroPage.addStyleTag({path: file});
    await heroPage.addScriptTag({path: path.resolve(__dirname, "../../src/meowkeeper/app/web/static/js/monitor-ui.js")});
    await heroPage.addScriptTag({path: SCRIPT});
    await heroPage.evaluate(() => document.dispatchEvent(new CustomEvent("meowkeeper:status", {detail: {
      metrics: {telemetry: {sampled_at: Date.now()/1000, system_cpu_percent: 20}}
    }})));
    const heroClock = await heroPage.evaluate(() => ({
      count: document.querySelectorAll('#hardware-sample-clock').length,
      inHero: Boolean(document.querySelector('.hero-hardware .hero-hardware-tools #hardware-sample-clock')),
      state: document.getElementById('hardware-sample-clock').dataset.state,
      offset: Number(document.getElementById('sample-countdown-ring').style.strokeDashoffset)
    }));
    assert.equal(heroClock.count, 1);
    assert.equal(heroClock.inHero, true);
    assert.equal(heroClock.state, 'countdown');
    await heroPage.evaluate(() => document.dispatchEvent(new CustomEvent("meowkeeper:status", {detail: {
      metrics: {telemetry: {sampled_at: Date.now()/1000, cpu_temperature_c:55,
        cpu_sensor:{status:'available',hardware_name:'測試 CPU 型號',provider:'libre_hardware_monitor'}}}
    }})));
    const cpuLabel=heroPage.locator('[data-metric="cpu_temperature_c"] .cpu-model-label');
    assert.equal(await cpuLabel.innerText(),'測試 CPU 型號');
    assert.equal(await cpuLabel.getAttribute('data-sensor-source'),'由 LibreHardwareMonitor 監控');
    await cpuLabel.focus();
    assert.equal(await cpuLabel.evaluate(el=>getComputedStyle(el,'::after').visibility),'visible');
    await heroPage.waitForTimeout(650);
    assert.ok(await heroPage.evaluate(previous => Number(document.getElementById('sample-countdown-ring').style.strokeDashoffset) > previous, heroClock.offset));
    await heroPage.setViewportSize({width: 390, height: 844});
    assert.equal(await heroPage.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await heroPage.evaluate(() => document.dispatchEvent(new CustomEvent("meowkeeper:status", {detail: {
      metrics: {telemetry: {sampled_at: Date.now()/1000-60}}
    }})));
    assert.equal(await heroPage.locator('#hardware-sample-clock').getAttribute('data-state'), 'stale');
    assert.equal(await cpuLabel.innerText(),'CPU 型號尚未取得');
    assert.equal(await cpuLabel.getAttribute('data-sensor-source'),'感測來源尚未確認');
    await heroPage.emulateMedia({reducedMotion: 'reduce'});
    await heroPage.close();
    process.stdout.write(JSON.stringify({status: "passed", synthetic_fixture: true, initial, beforeRoll, rolling, changed, missing, desktopLayout, mobileLayout, heroClock}) + "\n");
  } finally {
    await browser.close();
  }
}

main().catch(error => { process.stderr.write(String(error.stack || error) + "\n"); process.exitCode = 1; });
