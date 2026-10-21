/* Shared interactive resource charts; both Dashboard and Analytics use the same renderer. */
(() => {
  "use strict";
  const finite = value => Number.isFinite(value);
  const homeUsage = [
    {key:"system_cpu_percent", label:"CPU · 整台電腦", color:"#A94F24"},
    {key:"app_cpu_percent", label:"CPU · 喵喵守護者+", color:"#426C81", dashed:true},
    {key:"system_ram_percent", label:"RAM · 整台電腦", color:"#73543D"},
    {key:"app_ram_percent", label:"RAM · 喵喵守護者+", color:"#55724D", dashed:true},
    {key:"system_gpu_percent", label:"GPU · 已接通感測器", color:"#8064A2"}
  ];
  const homeTemperatures = [
    {key:"cpu_temperature_c", label:"CPU · 直接感測", color:"#A94F24"},
    {key:"gpu_temperature_c", label:"GPU · 硬體感測", color:"#426C81"}
  ];
  const localTime = stamp => new Date(stamp * 1000).toLocaleTimeString("zh-TW", {hour:"2-digit", minute:"2-digit"});

  function mount(target) {
    if (!target) return {render() {}, destroy() {}};
    let current = null, resizeTimer = 0, chartSpecs = [];

    function chart(spec) {
      const {title, series, unit, fixedRange = null, note = "", rangeSeconds = 86400} = spec;
      const points = Array.isArray(current?.points) ? current.points : [];
      const divisor = item => Number.isFinite(item.divisor) && item.divisor > 0 ? item.divisor : 1;
      const valueOf = (point, item) => finite(point?.[item.key]) ? point[item.key] / divisor(item) : null;
      const format = (value, item) => finite(value) ? `${(value / divisor(item)).toFixed(1)} ${unit}` : "尚無資料";
      const figure = document.createElement("figure"); figure.className = "dashboard-chart dashboard-chart-interactive";
      const heading = document.createElement("h4"); heading.textContent = title; figure.append(heading);
      const plot = document.createElement("div"); plot.className = "dashboard-chart-plot";
      const canvas = document.createElement("canvas"); canvas.tabIndex = 0; canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", `${title}互動折線圖；滑鼠懸停、觸控，或用左右方向鍵查看數值，Home／End 跳至最早／最新資料。`);
      const tooltip = document.createElement("div"); tooltip.className = "dashboard-chart-tooltip";
      tooltip.setAttribute("role", "status"); tooltip.hidden = true; plot.append(canvas, tooltip); figure.append(plot);

      const legend = document.createElement("ul"); legend.className = "dashboard-chart-legend";
      for (const item of series) {
        const entry = document.createElement("li"), swatch = document.createElement("span");
        entry.className = item.dashed ? "is-dashed" : "";
        swatch.className = "dashboard-chart-swatch"; swatch.style.setProperty("--series-color", item.color);
        swatch.setAttribute("aria-hidden", "true"); entry.append(swatch, document.createTextNode(item.label)); legend.append(entry);
      }
      figure.append(legend);

      const caption = document.createElement("figcaption"); caption.className = "dashboard-chart-summary";
      const tableWrap = document.createElement("div"); tableWrap.className = "dashboard-chart-table-wrap";
      const table = document.createElement("table"); table.className = "dashboard-chart-table";
      const tableCaption = document.createElement("caption"); tableCaption.textContent = title + "數值摘要"; table.append(tableCaption);
      const thead = table.createTHead(), header = thead.insertRow();
      for (const label of ["指標", "最新分段值", "已採樣均值", "最高分段均值"]) {
        const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = label; header.append(cell);
      }
      const tbody = table.createTBody();
      for (const item of series) {
        const row = tbody.insertRow(), label = document.createElement("th"); label.scope = "row";
        label.textContent = item.label; label.style.color = item.color; row.append(label);
        const values = points.map(point => valueOf(point, item)).filter(finite);
        const latest = [...points].reverse().find(point => finite(point[item.key]));
        row.insertCell().textContent = latest ? format(latest[item.key], item) : "尚無資料";
        row.insertCell().textContent = values.length ? `${(values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(1)} ${unit}` : "尚無資料";
        row.insertCell().textContent = values.length ? `${Math.max(...values).toFixed(1)} ${unit}` : "尚無資料";
      }
      tableWrap.append(table); caption.append(tableWrap);
      const noteElement = document.createElement("p"); noteElement.className = "dashboard-chart-note";
      noteElement.textContent = note || (unit === "°C"
        ? "只呈現感測器實際回報的溫度；未接通或讀取失敗會留白，不以 0°C 代替。"
        : "CPU／RAM 同時列出整台電腦與程式用量；GPU 只代表已接通的硬體感測來源。");
      caption.append(noteElement); figure.append(caption); target.append(figure);

      const windowSeconds = Math.max(60, Number(rangeSeconds) || 86400);
      const end = Date.now() / 1000, start = end - windowSeconds;
      const observed = points.flatMap(point => series.map(item => valueOf(point, item))).filter(finite);
      let minValue = 0, maxValue = 100;
      if (fixedRange) [minValue, maxValue] = fixedRange;
      else if (observed.length) {
        const low = Math.min(...observed), high = Math.max(...observed), padding = Math.max(5, (high - low) * .18);
        minValue = Math.max(0, Math.floor((low - padding) / 5) * 5);
        maxValue = Math.ceil((high + padding) / 5) * 5;
        if (maxValue - minValue < 20) maxValue = minValue + 20;
      }
      const height = 240, left = 50, top = 18, dpr = Math.min(window.devicePixelRatio || 1, 2);
      let width = 0, right = 0, bottom = height - 40, selectedIndex = -1, pendingPointerX = null, pointerFrame = 0;
      function setupCanvas() {
        width = Math.max(280, plot.clientWidth || 320); right = width - 12;
        canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
        canvas.style.width = "100%"; canvas.style.height = height + "px";
      }
      function pointX(point) { return left + (right - left) * (point.sampled_at - start) / windowSeconds; }
      function pointY(value) { return bottom - (bottom - top) * (value - minValue) / Math.max(1, maxValue - minValue); }
      function draw() {
        setupCanvas();
        const context = canvas.getContext("2d"); context.setTransform(dpr, 0, 0, dpr, 0, 0);
        context.clearRect(0, 0, width, height); context.font = "11px Segoe UI, sans-serif";
        context.textBaseline = "middle"; context.textAlign = "right";
        for (let step = 0; step <= 4; step++) {
          const ratio = step / 4, y = bottom - (bottom - top) * ratio;
          const value = minValue + (maxValue - minValue) * ratio;
          context.strokeStyle = "#E5DACD"; context.lineWidth = 1;
          context.beginPath(); context.moveTo(left, y); context.lineTo(right, y); context.stroke();
          context.fillStyle = "#62544A"; context.fillText(value.toFixed(0) + unit, left - 8, y);
        }
        context.textAlign = "center"; context.textBaseline = "alphabetic";
        for (let step = 0; step <= 4; step++) {
          const x = left + (right - left) * step / 4, stamp = start + windowSeconds * step / 4;
          const label = windowSeconds >= 86400
            ? new Date(stamp * 1000).toLocaleDateString("zh-TW", {month:"2-digit", day:"2-digit"})
            : localTime(stamp);
          context.fillStyle = "#62544A"; context.fillText(label, x, height - 10);
        }
        for (const item of series) {
          context.strokeStyle = item.color; context.lineWidth = 2.2; context.lineJoin = "round";
          context.setLineDash(item.dashed ? [6, 4] : []); context.beginPath();
          let previous = null;
          for (const point of points) {
            const value = valueOf(point, item);
            if (!finite(value)) { previous = null; continue; }
            const x = pointX(point), y = pointY(Math.max(minValue, Math.min(maxValue, value)));
            if (previous === null || point.sampled_at - previous > (current.bucket_seconds || 60) * 2) context.moveTo(x, y);
            else context.lineTo(x, y);
            previous = point.sampled_at;
          }
          context.stroke(); context.setLineDash([]);
          const selected = points[selectedIndex], selectedValue = selected && valueOf(selected, item);
          if (finite(selectedValue)) {
            const x = pointX(selected), y = pointY(Math.max(minValue, Math.min(maxValue, selectedValue)));
            context.fillStyle = "#fffdf9"; context.beginPath(); context.arc(x, y, 5, 0, Math.PI * 2); context.fill();
            context.strokeStyle = item.color; context.lineWidth = 2.5;
            context.beginPath(); context.arc(x, y, 4, 0, Math.PI * 2); context.stroke();
          }
        }
        if (points[selectedIndex]) {
          const x = pointX(points[selectedIndex]); context.strokeStyle = "#73533899"; context.setLineDash([3, 4]);
          context.beginPath(); context.moveTo(x, top); context.lineTo(x, bottom); context.stroke(); context.setLineDash([]);
        }
      }
      function showPoint(index) {
        if (!points.length) return;
        selectedIndex = Math.max(0, Math.min(points.length - 1, index)); draw();
        const point = points[selectedIndex]; tooltip.replaceChildren();
        const timestamp = document.createElement("strong");
        timestamp.textContent = new Date(point.sampled_at * 1000).toLocaleString("zh-TW"); tooltip.append(timestamp);
        for (const item of series) {
          const row = document.createElement("span"); row.style.setProperty("--series-color", item.color);
          const value = valueOf(point, item);
          row.textContent = item.label + "　" + (finite(value) ? `${value.toFixed(1)} ${unit}` : "無感測資料");
          tooltip.append(row);
        }
        tooltip.style.top = "8px"; tooltip.hidden = false;
        tooltip.style.left = Math.max(8, Math.min(width - tooltip.offsetWidth - 8, pointX(point))) + "px";
      }
      function nearestPoint(clientX) {
        if (!points.length) return -1;
        const rect = canvas.getBoundingClientRect(), localX = (clientX - rect.left) * width / Math.max(1, rect.width);
        const stamp = start + (localX - left) / Math.max(1, right - left) * windowSeconds;
        let best = 0;
        for (let index = 1; index < points.length; index++) {
          if (Math.abs(points[index].sampled_at - stamp) < Math.abs(points[best].sampled_at - stamp)) best = index;
        }
        return best;
      }
      canvas.addEventListener("pointermove", event => {
        if (event.pointerType !== "mouse" || !points.length) return;
        pendingPointerX = event.clientX;
        if (pointerFrame) return;
        pointerFrame = requestAnimationFrame(() => {
          pointerFrame = 0;
          const nearest = nearestPoint(pendingPointerX);
          if (nearest !== selectedIndex) showPoint(nearest);
        });
      });
      canvas.addEventListener("pointerdown", event => {
        if (event.pointerType !== "mouse" && points.length) showPoint(nearestPoint(event.clientX));
      });
      canvas.addEventListener("pointerleave", () => {
        if (pointerFrame) cancelAnimationFrame(pointerFrame);
        pointerFrame = 0; pendingPointerX = null;
        if (document.activeElement !== canvas) { tooltip.hidden = true; selectedIndex = -1; draw(); }
      });
      canvas.addEventListener("focus", () => { if (points.length) showPoint(selectedIndex < 0 ? points.length - 1 : selectedIndex); });
      canvas.addEventListener("blur", () => { tooltip.hidden = true; selectedIndex = -1; draw(); });
      canvas.addEventListener("keydown", event => {
        if (!points.length || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault(); const currentIndex = selectedIndex < 0 ? points.length - 1 : selectedIndex;
        const next = event.key === "Home" ? 0 : event.key === "End" ? points.length - 1
          : currentIndex + (event.key === "ArrowLeft" ? -1 : 1);
        showPoint(Math.max(0, Math.min(points.length - 1, next)));
      });
      draw();
    }

    function render(data, specs = chartSpecs) {
      current = data; chartSpecs = specs || []; target.replaceChildren();
      if (!current) return;
      for (const spec of chartSpecs) chart(spec);
    }
    function resize() {
      if (!current || document.hidden) return;
      clearTimeout(resizeTimer); resizeTimer = setTimeout(() => render(current), 180);
    }
    window.addEventListener("resize", resize);
    return {render, destroy() { clearTimeout(resizeTimer); window.removeEventListener("resize", resize); target.replaceChildren(); }};
  }

  window.MeowKeeperResourceCharts = {mount};
  const target = document.getElementById("overview-resource-charts");
  if (!target) return;
  const home = mount(target, {rangeSeconds:86400});
  const renderHome = data => home.render(data, [
    {title:"CPU／RAM／GPU 使用率", series:homeUsage, unit:"%", fixedRange:[0, 100], rangeSeconds:86400},
    {title:"CPU／GPU 溫度", series:homeTemperatures, unit:"°C", rangeSeconds:86400}
  ]);
  document.addEventListener("meowkeeper:resource-history", event => renderHome(event.detail));
  if (window.MeowKeeperResourceHistory) renderHome(window.MeowKeeperResourceHistory);
  target.closest("details")?.addEventListener("toggle", event => {
    if (event.currentTarget.open && window.MeowKeeperResourceHistory) renderHome(window.MeowKeeperResourceHistory);
  });
})();
