/* Animated, accessible display for the existing five-second host telemetry sample. */
(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const clock = byId("hardware-sample-clock");
  if (!clock) return;

  const ringLength = 2 * Math.PI * 15;
  const sampleRing = byId("sample-countdown-ring");
  const countdown = byId("sample-countdown");
  const updated = byId("overview-updated");
  const countdownLabel = byId("sample-countdown-label");
  let latestSampleAt = null;
  let samplePeriodMs = 5000;
  let tickHandle = 0;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

  function setValue(element, rawValue) {
    if (!element) return;
    const value = String(rawValue ?? "尚未採樣");
    if (element.dataset.metricValue === value && (!/\d/.test(value)
      || (element.querySelector(".odometer-value") && element.querySelector(".odometer-accessible")))) return;
    const previousDigits = (element.dataset.metricValue || "").match(/\d/g) || [];
    const nextDigits = value.match(/\d/g) || [];
    const oldPattern = element.dataset.metricPattern || "";
    const pattern = value.replace(/\d/g, "#");
    element.dataset.metricValue = value;
    element.dataset.metricPattern = pattern;
    element.setAttribute("aria-label", value);

    if (!nextDigits.length) {
      element.textContent = value;
      return;
    }

    const sameShape = oldPattern === pattern && element.querySelectorAll(".odometer-strip").length === nextDigits.length;
    if (sameShape) {
      const accessible = element.querySelector(".odometer-accessible");
      if (accessible) accessible.textContent = value;
      let index = 0;
      for (const char of value) {
        if (!/\d/.test(char)) continue;
        const strip = element.querySelectorAll(".odometer-strip")[index++];
        const targetDigit = Number(char);
        const currentDigit = Number(strip.dataset.digit ?? targetDigit);
        strip.dataset.direction = targetDigit > currentDigit ? "up" : targetDigit < currentDigit ? "down" : "steady";
        strip.dataset.digit = String(targetDigit);
        strip.style.transform = `translateY(${-targetDigit * 10}%)`;
      }
      return;
    }

    const visual = document.createElement("span");
    visual.className = "odometer-value";
    visual.setAttribute("aria-hidden", "true");
    const accessible = document.createElement("span");
    accessible.className = "odometer-accessible";
    accessible.textContent = value;
    let digitIndex = 0;
    for (const char of value) {
      if (!/\d/.test(char)) {
        const literal = document.createElement("span");
        literal.className = "odometer-literal";
        literal.textContent = char;
        visual.append(literal);
        continue;
      }
      const cell = document.createElement("span");
      cell.className = "odometer-cell";
      const strip = document.createElement("span");
      strip.className = "odometer-strip";
      const targetDigit = Number(char);
      const previousIndex = digitIndex - (nextDigits.length - previousDigits.length);
      const startDigit = previousIndex >= 0 && previousIndex < previousDigits.length
        ? Number(previousDigits[previousIndex]) : targetDigit;
      strip.dataset.digit = String(startDigit);
      strip.dataset.direction = "steady";
      for (let digit = 0; digit <= 9; digit++) {
        const row = document.createElement("span");
        row.textContent = String(digit);
        strip.append(row);
      }
      strip.style.transform = `translateY(${-startDigit * 10}%)`;
      cell.append(strip);
      visual.append(cell);
      digitIndex++;
    }
    element.replaceChildren(accessible, visual);
    if (!reducedMotion && previousDigits.length) {
      const strips = visual.querySelectorAll(".odometer-strip");
      void visual.offsetHeight;
      requestAnimationFrame(() => {
        let index = 0;
        for (const char of value) {
          if (/\d/.test(char)) {
            const strip = strips[index++];
            const targetDigit = Number(char);
            const currentDigit = Number(strip.dataset.digit ?? targetDigit);
            strip.dataset.direction = targetDigit > currentDigit ? "up" : targetDigit < currentDigit ? "down" : "steady";
            strip.dataset.digit = String(targetDigit);
            strip.style.transform = `translateY(${-targetDigit * 10}%)`;
          }
        }
      });
    }
  }

  function setRing(id, rawValue) {
    const element = byId(id);
    if (!element) return;
    const value = typeof rawValue === "number" ? rawValue : NaN;
    const available = Number.isFinite(value);
    const circumference = 2 * Math.PI * 49;
    element.style.strokeDasharray = String(circumference);
    element.style.strokeDashoffset = String(circumference * (1 - (available ? Math.max(0, Math.min(100, value)) / 100 : 0)));
    element.dataset.state = available ? "ready" : "unavailable";
    const gauge = element.closest(".live-usage-ring");
    if (gauge) {
      const label = id === "cpu-usage-ring" ? "CPU 使用率" : id === "overview-ram-ring" ? "RAM 使用率" : "GPU 使用率";
      gauge.setAttribute("aria-label", available ? `${label} ${value.toFixed(1)}%` : `${label} 尚未感測`);
    }
  }

  function setTemperature(prefix, rawValue) {
    const value = typeof rawValue === "number" ? rawValue : NaN;
    const available = Number.isFinite(value);
    const needle = byId(`${prefix}-temp-needle`);
    const arc = byId(`${prefix}-temp-arc`);
    const dial = needle?.closest(".temperature-dial");
    if (needle) {
      const clamped = available ? Math.max(0, Math.min(110, value)) : 0;
      needle.setAttribute("transform", `rotate(${-90 + clamped * (180 / 110)} 60 66)`);
      needle.dataset.state = available ? "ready" : "unavailable";
    }
    if (arc) {
      arc.setAttribute("pathLength", "100");
      arc.style.strokeDasharray = "100";
      arc.style.strokeDashoffset = String(100 * (1 - (available ? Math.max(0, Math.min(110, value)) / 110 : 0)));
      arc.dataset.state = available ? "ready" : "unavailable";
    }
    if (dial) dial.setAttribute("aria-label", `${prefix.toUpperCase()} 溫度 ${available ? `${value.toFixed(1)}°C` : "尚未感測"}`);
    setValue(byId(`${prefix}-temp-value`), available ? `${value.toFixed(1)}°C` : "尚未感測");
  }

  function formatSampleTime(timestamp) {
    return new Date(timestamp).toLocaleTimeString("zh-TW", {hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true});
  }

  function drawCountdown() {
    if (!latestSampleAt) {
      clock.dataset.state = "waiting";
      countdown.textContent = "—";
      countdownLabel.textContent = "等待硬體採樣";
      sampleRing.style.strokeDashoffset = String(ringLength);
      return;
    }
    const elapsed = Date.now() - latestSampleAt;
    const remaining = samplePeriodMs - elapsed;
    if (remaining > 0) {
      const seconds = Math.max(1, Math.ceil(remaining / 1000));
      clock.dataset.state = "countdown";
      countdown.textContent = String(seconds);
      countdownLabel.textContent = `${seconds} 秒後預計更新`;
      sampleRing.style.strokeDashoffset = String(ringLength * Math.min(1, Math.max(0, elapsed / samplePeriodMs)));
    } else {
      const late = elapsed - samplePeriodMs;
      clock.dataset.state = late > Math.max(10000, samplePeriodMs * 2) ? "stale" : "sampling";
      countdown.textContent = "…";
      countdownLabel.textContent = clock.dataset.state === "stale" ? "硬體採樣逾時" : "等待新的硬體數據";
      sampleRing.style.strokeDashoffset = String(ringLength);
    }
  }

  function update(payload) {
    const telemetry = payload?.metrics?.telemetry || {};
    const system = payload?.metrics?.system || {};
    const sampleAt = Number(telemetry.sampled_at) * 1000;
    if (Number.isFinite(sampleAt) && sampleAt > 0 && sampleAt !== latestSampleAt) {
      if (latestSampleAt !== null) {
        const observedPeriod = sampleAt - latestSampleAt;
        if (observedPeriod >= 2000 && observedPeriod <= 15000) samplePeriodMs = observedPeriod;
      }
      latestSampleAt = sampleAt;
      updated.textContent = `最近採樣：${formatSampleTime(sampleAt)}`;
      clock.dataset.sampledAt = new Date(sampleAt).toISOString();
    }

    const cpu = Number.isFinite(system.cpu_percent) ? system.cpu_percent : null;
    const ram = Number.isFinite(system.ram_percent) ? system.ram_percent : null;
    const gpu = Number.isFinite(telemetry.system_gpu_percent) ? telemetry.system_gpu_percent : null;
    setValue(byId("system-status"), cpu === null ? "尚未採樣" : `${cpu.toFixed(1)}%`);
    setRing("cpu-usage-ring", cpu);
    setRing("overview-ram-ring", ram);
    setRing("overview-gpu-ring", gpu);
    setValue(byId("overview-ram-percent"), ram === null ? "尚未感測" : `${ram.toFixed(1)}%`);
    setValue(byId("overview-gpu-usage"), gpu === null ? "尚未感測" : `${gpu.toFixed(1)}%`);
    const gpuName = telemetry.gpu_name || "GPU 型號尚未提供";
    if (byId("overview-gpu-name")) byId("overview-gpu-name").textContent = gpuName;
    setValue(byId("overview-gpu-state"), gpu === null ? "感測未接通" : gpu < 70 ? "負載正常" : "負載偏高");
    setValue(byId("overview-gpu-detail"), gpu === null ? "目前沒有可用的 GPU 使用率讀值。" : "此值為整張 GPU 負載，包含其他程式。");
    setTemperature("cpu", telemetry.cpu_temperature_c);
    setTemperature("gpu", telemetry.gpu_temperature_c);
    const sensorTime = telemetry.cpu_sensor?.observed_at;
    const note = byId("temperature-sample-note");
    const sensor = telemetry.cpu_sensor || {};
    const sensorReasons = {
        broker_starting: "受限測溫工作正在等候下一次採樣（最長約一分鐘）；沒有把舊值當作現在溫度。",
        broker_invalid: "受限測溫結果無法安全讀取；請到維護中心檢查測溫工作，不影響影音。",
      driver_missing: "此電腦缺少 CPU 測溫驅動；請執行 Enable-CPU-Temperature.cmd 完成一次授權安裝。",
      driver_unavailable: "CPU 測溫驅動不可用或目前帳號權限不足；未取得真實溫度。",
      helper_missing: "此版本缺少 CPU 測溫元件；請使用包含測溫功能的完整發布包。",
      sample_expired: "CPU 溫度資料已逾時，已清除舊數值；背景將重新檢查。"
    };
    if (sensor.status !== "available" && sensorReasons[sensor.error_reason]) {
      setValue(byId("cpu-temp-value"), sensor.error_reason === "driver_missing" ? "缺少測溫驅動" : "測溫未接通");
    }
    if (note) note.textContent = sensorReasons[sensor.error_reason] || (sensorTime
      ? `CPU 感測器回報：${formatSampleTime(Date.parse(sensorTime))}；GPU 依主機採樣時間更新。`
      : "感測器沒有回報溫度時保留「尚未感測」，不以 0°C 代替。");
    drawCountdown();
  }

  document.addEventListener("meowkeeper:status", event => update(event.detail));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearInterval(tickHandle);
      tickHandle = 0;
    } else {
      drawCountdown();
      if (!tickHandle) tickHandle = window.setInterval(drawCountdown, reducedMotion ? 1000 : 200);
    }
  });
  if (!document.hidden) tickHandle = window.setInterval(drawCountdown, reducedMotion ? 1000 : 200);
  window.MeowKeeperHardwareMotion = {setValue, update};
  drawCountdown();
})();
