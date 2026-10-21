(() => {
  const form = document.getElementById("zone-add-form");
  if (!form) return;
  const byId = (id) => document.getElementById(id);
  const message = byId("zone-message");
  let reference = null;
  let zones = [];
  const csrf = () => {
    const part = document.cookie.split(";").map((x) => x.trim()).find((x) => x.startsWith("meowkeeper_csrf="));
    return part ? decodeURIComponent(part.substring("meowkeeper_csrf=".length)) : "";
  };
  function render() {
    const list = byId("zone-list");
    list.replaceChildren();
    zones.forEach((zone, index) => {
      const row = document.createElement("li");
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = zone.enabled;
      checkbox.addEventListener("change", () => { zone.enabled = checkbox.checked; });
      label.append(checkbox, document.createTextNode(` ${zone.name}（${zone.polygon.length} 個邊界點） `));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "移除";
      remove.addEventListener("click", () => { zones.splice(index, 1); render(); });
      row.append(label, remove);
      list.append(row);
    });
    if (!zones.length) list.textContent = "目前沒有區域。";
    byId("zone-save").disabled = !reference;
  }
  async function load() {
    reference = null;
    byId("zone-save").disabled = true;
    try {
      const response = await fetch("/api/v1/settings/behavior-zones");
      const data = await response.json().catch(() => { throw new Error("服務回應暫時無法讀取，請稍後重新載入區域。"); });
      if (!response.ok) throw new Error(data.detail || "區域讀取失敗。");
      reference = data.source_ref;
      zones = data.zones;
      byId("zones-mode").textContent = data.analyzer_enabled ? "V0.2 已選用；候選結果仍需人工驗證。" : "目前未選用 V0.2；可先保存，但不會產生區域行為分析。";
      message.textContent = data.message_zh_tw;
      render();
    } catch (error) { message.textContent = error.message || "區域讀取失敗。"; }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!reference) { message.textContent = "請先設定來源並成功載入區域。"; return; }
    const [x, y, w, h] = ["zone-x", "zone-y", "zone-width", "zone-height"].map((id) => Number(byId(id).value) / 100);
    if (![x, y, w, h].every(Number.isFinite) || x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > 1 || y + h > 1 || zones.length >= 64) {
      message.textContent = "矩形不可超出畫面，寬高需大於零，最多 64 個區域。"; return;
    }
    const id = crypto.randomUUID ? crypto.randomUUID() : `zone-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    zones.push({ zone_id: id, name: byId("zone-name").value.trim(), zone_type: byId("zone-type").value,
      polygon: [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], enabled: true });
    render();
    message.textContent = "已加入清單，請按保存才會寫入。";
  });
  byId("zone-reload").addEventListener("click", () => {
    if (window.confirm("重新載入會捨棄未保存的區域修改，是否繼續？")) load();
  });
  byId("zone-save").addEventListener("click", async () => {
    if (!reference || !window.confirm("確認保存目前來源區域？需重新啟動監控才會套用。")) return;
    byId("zone-save").disabled = true;
    try {
      const response = await fetch("/api/v1/settings/behavior-zones", { method: "PUT",
        headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrf() },
        body: JSON.stringify({ source_ref: reference, zones, reauth: true }) });
      const data = await response.json().catch(() => { throw new Error("尚未確認區域已保存；請先重新載入檢查，不要重複送出。"); });
      if (!response.ok) throw new Error(data.detail || "區域保存失敗。");
      message.textContent = data.message_zh_tw;
    } catch (error) { message.textContent = error.message || "區域保存失敗。"; }
    finally { byId("zone-save").disabled = !reference; }
  });
  load();
})();
