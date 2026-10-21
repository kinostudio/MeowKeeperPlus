(() => {
  const start = document.getElementById("summary-start"), end = document.getElementById("summary-end");
  const message = document.getElementById("summary-message"), results = document.getElementById("summary-results");
  const button = document.getElementById("summary-load");
  const quickButtons = Array.from(document.querySelectorAll("[data-range-days]"));

  function formatDate(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function applyQuickRange(days) {
    const now = new Date();
    const to = formatDate(now);
    const fromDate = new Date(now);
    fromDate.setDate(fromDate.getDate() - Math.max(0, days - 1));
    start.value = formatDate(fromDate);
    end.value = to;
    quickButtons.forEach((btn) => {
      btn.classList.toggle("is-active", Number(btn.dataset.rangeDays) === days);
    });
  }

  applyQuickRange(1);

  quickButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const days = Number(btn.dataset.rangeDays || 1);
      applyQuickRange(days);
      load();
    });
  });

  const clearQuickActive = () => quickButtons.forEach((btn) => btn.classList.remove("is-active"));
  start?.addEventListener("change", clearQuickActive);
  end?.addEventListener("change", clearQuickActive);

  const labels = {
    sleeping: "睡眠",
    resting: "休息",
    eating: "進食",
    drinking: "喝水",
    playing: "玩耍",
    grooming: "理毛",
    litter_box: "使用貓砂",
    other_active: "其他活動",
    out_of_view: "不在畫面",
    unknown: "無法辨識"
  };

  const colors = {
    sleeping: "#5c768d",
    resting: "#7a8f68",
    eating: "#c86d3b",
    drinking: "#4a8da8",
    playing: "#d49b38",
    grooming: "#9b6b82",
    litter_box: "#8c6d53",
    other_active: "#b55d3d",
    out_of_view: "#9a9187",
    unknown: "#8a7e72"
  };

  function el(parent, tag, className = "", textContent = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (textContent !== "") node.textContent = textContent;
    if (parent) parent.appendChild(node);
    return node;
  }

  function formatDuration(sec) {
    const s = Number(sec || 0);
    if (s <= 0) return "0 秒";
    if (s < 60) return `${s.toFixed(1)} 秒`;
    const mins = Math.floor(s / 60);
    const rem = Math.round(s % 60);
    if (mins < 60) return rem > 0 ? `${mins} 分 ${rem} 秒` : `${mins} 分`;
    const hours = (s / 3600).toFixed(1);
    return `${hours} 小時（${s.toFixed(0)} 秒）`;
  }

  function sumGroupSeconds(group) {
    return (group.behaviors || []).reduce((acc, b) => acc + Number(b.seconds || 0), 0);
  }

  function sumGroupEvents(group) {
    return (group.behaviors || []).reduce((acc, b) => acc + Number(b.intersecting_event_count || 0), 0);
  }

  function buildGroupCard(group, subtitleNote = "") {
    const card = document.createElement("article");
    card.className = "behavior-group-card";

    const header = el(card, "div", "behavior-group-header");
    const titleWrap = el(header, "div");
    el(titleWrap, "h4", "", group.name);
    if (subtitleNote) el(titleWrap, "small", "muted", subtitleNote);

    const totalSec = sumGroupSeconds(group);
    const totalEvents = sumGroupEvents(group);
    const badges = el(header, "div", "behavior-group-badges");
    el(badges, "span", "behavior-badge is-highlight", `累計 ${formatDuration(totalSec)}`);
    el(badges, "span", "behavior-badge", `觀察區間 ${totalEvents} 筆`);
    if (group.pending_count > 0) {
      el(badges, "span", "behavior-badge", `進行中 ${group.pending_count} 筆`);
    }

    const activeBehaviors = (group.behaviors || [])
      .filter((b) => Number(b.seconds || 0) > 0 || Number(b.intersecting_event_count || 0) > 0)
      .sort((a, b) => Number(b.seconds || 0) - Number(a.seconds || 0));

    if (activeBehaviors.length === 0) {
      el(
        card,
        "div",
        "behavior-empty-state",
        group.pending_count > 0
          ? `目前有 ${group.pending_count} 筆尚未結束的觀察軌跡，本期間尚無已結束的行為秒數。`
          : "本期間尚無已結束的行為時段紀錄。"
      );
      return card;
    }

    if (totalSec > 0) {
      const stackBar = el(card, "div", "behavior-stack-bar");
      const legend = el(card, "div", "behavior-legend");
      for (const item of activeBehaviors) {
        const sec = Number(item.seconds || 0);
        if (sec <= 0) continue;
        const share = Math.max(2, (sec / totalSec) * 100);
        const color = colors[item.state] || "#8a7e72";
        const label = labels[item.state] || "其他候選";
        const seg = el(stackBar, "span", "behavior-stack-segment");
        seg.style.width = `${share}%`;
        seg.style.background = color;
        seg.title = `${label}：${formatDuration(sec)}（占已觀察行為 ${((sec / totalSec) * 100).toFixed(1)}%）`;

        const leg = el(legend, "span", "behavior-legend-item");
        const dot = el(leg, "span", "behavior-dot");
        dot.style.background = color;
        el(leg, "span", "", `${label} ${formatDuration(sec)}`);
      }
    }

    const barsList = el(card, "div", "behavior-bars-list");
    const maxSec = Math.max(...activeBehaviors.map((b) => Number(b.seconds || 0)), 1);
    for (const item of activeBehaviors) {
      const row = el(barsList, "div", "behavior-bar-row");
      const labelWrap = el(row, "span", "behavior-bar-label");
      const dot = el(labelWrap, "span", "behavior-dot");
      const color = colors[item.state] || "#8a7e72";
      dot.style.background = color;
      el(labelWrap, "span", "", labels[item.state] || "其他候選");

      const track = el(row, "div", "behavior-bar-track");
      const fill = el(track, "div", "behavior-bar-fill");
      fill.style.width = `${Math.max(4, (Number(item.seconds || 0) / maxSec) * 100)}%`;
      fill.style.background = color;

      const calPct = item.calendar_percent === null || item.calendar_percent === undefined
        ? ""
        : ` · 日曆占比 ${item.calendar_percent}%`;
      el(
        row,
        "span",
        "behavior-bar-stats",
        `${item.seconds.toFixed(1)} 秒 · ${item.intersecting_event_count} 次（平均 ${item.mean_observed_seconds.toFixed(1)} 秒${calPct}）`
      );
    }

    if (group.overlapping_intervals) {
      el(card, "small", "field-help", "存在重疊候選區間，日曆時間百分比已隱藏，避免重複計算。");
    }

    const details = el(card, "details", "behavior-detail-disclosure");
    el(details, "summary", "", `查看「${group.name}」完整行為明細表（含 0 秒項目）`);
    const table = el(details, "table");
    const thead = el(table, "thead");
    const headerRow = el(thead, "tr");
    for (const title of ["行為", "累計秒數", "本期間開始次數", "相交事件數", "範圍內平均秒數", "占選定日曆時間"]) {
      el(headerRow, "th", "", title);
    }
    const tbody = el(table, "tbody");
    for (const item of group.behaviors || []) {
      const tr = el(tbody, "tr");
      const values = [
        labels[item.state] || "其他候選",
        item.seconds.toFixed(1),
        String(item.started_event_count),
        String(item.intersecting_event_count),
        item.mean_observed_seconds.toFixed(1),
        item.calendar_percent === null ? "不計算" : `${item.calendar_percent}%`
      ];
      for (const v of values) el(tr, "td", "", v);
    }

    return card;
  }

  function mergeUnconfirmedGroups(groups) {
    const byState = new Map();
    let pendingCount = 0;
    let invalidCount = 0;
    let overlapping = false;

    for (const g of groups) {
      pendingCount += Number(g.pending_count || 0);
      invalidCount += Number(g.invalid_count || 0);
      if (g.overlapping_intervals) overlapping = true;
      for (const b of g.behaviors || []) {
        const cur = byState.get(b.state) || {
          state: b.state,
          seconds: 0,
          started_event_count: 0,
          intersecting_event_count: 0,
          mean_observed_seconds: 0,
          calendar_percent: null
        };
        cur.seconds += Number(b.seconds || 0);
        cur.started_event_count += Number(b.started_event_count || 0);
        cur.intersecting_event_count += Number(b.intersecting_event_count || 0);
        byState.set(b.state, cur);
      }
    }

    const mergedBehaviors = Array.from(byState.values()).map((item) => ({
      ...item,
      mean_observed_seconds: item.intersecting_event_count > 0
        ? item.seconds / item.intersecting_event_count
        : 0
    }));

    return {
      id: "unconfirmed_merged",
      name: "尚未確認身分（全部獨立軌跡彙整）",
      pending_count: pendingCount,
      invalid_count: invalidCount,
      overlapping_intervals: overlapping,
      behaviors: mergedBehaviors
    };
  }

  async function load() {
    button.disabled = true;
    results.replaceChildren();
    message.textContent = "正在讀取已保存事件…";
    try {
      const first = new Date(`${start.value}T00:00:00`), last = new Date(`${end.value}T00:00:00`);
      last.setDate(last.getDate() + 1);
      const query = new URLSearchParams({ start: first.toISOString(), end: last.toISOString() });
      const response = await fetch(`/api/v1/analytics/behavior?${query}`, { cache: "no-store" });
      if (response.status === 401) throw new Error("請重新登入後查詢。");
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "無法查詢，請確認日期。");
      message.textContent = data.status === "no_data"
        ? "尚無行為紀錄。主線貓咪偵測事件不會自動當成進食或睡眠。"
        : data.message_zh_tw;

      const allGroups = Array.isArray(data.groups) ? data.groups : [];
      if (allGroups.length === 0) return;

      const namedGroups = allGroups.filter((g) => !String(g.id || "").startsWith("unconfirmed:"));
      const unconfirmedGroups = allGroups.filter((g) => String(g.id || "").startsWith("unconfirmed:"));
      const nonEmptyUnconfirmed = unconfirmedGroups.filter((g) => sumGroupSeconds(g) > 0 || sumGroupEvents(g) > 0);
      const emptyPendingTracks = unconfirmedGroups.length - nonEmptyUnconfirmed.length;

      const totalObservedSeconds = allGroups.reduce((acc, g) => acc + sumGroupSeconds(g), 0);
      const totalObservedEvents = allGroups.reduce((acc, g) => acc + sumGroupEvents(g), 0);
      const namedWithData = namedGroups.filter((g) => sumGroupSeconds(g) > 0).length;

      const kpiRow = el(results, "div", "behavior-kpis");
      const kpiItems = [
        ["已建立寵物檔案", `${namedGroups.length} 隻`, namedWithData > 0 ? `${namedWithData} 隻已有確認身分紀錄` : "目前事件皆尚未確認個體身分"],
        ["已結束行為總時長", formatDuration(totalObservedSeconds), `共 ${totalObservedEvents} 段已結束行為區間`],
        ["未確認身分有效軌跡", `${nonEmptyUnconfirmed.length} 條`, nonEmptyUnconfirmed.length > 0 ? "已自動合併為單一總覽卡片" : "本期間無未確認身分之已完成軌跡"],
        ["進行中／0 秒暫存軌跡", `${emptyPendingTracks} 條`, "已自動折疊隱藏，避免空白表格洗版"]
      ];
      for (const [kTitle, kVal, kSub] of kpiItems) {
        const kCard = el(kpiRow, "article", "behavior-kpi-card");
        el(kCard, "span", "", kTitle);
        el(kCard, "strong", "", kVal);
        el(kCard, "small", "", kSub);
      }

      const cardsGrid = el(results, "div", "behavior-group-cards");
      for (const group of namedGroups) {
        cardsGrid.appendChild(buildGroupCard(group, "已登錄寵物檔案"));
      }

      if (unconfirmedGroups.length > 0) {
        const merged = mergeUnconfirmedGroups(unconfirmedGroups);
        cardsGrid.appendChild(
          buildGroupCard(
            merged,
            `彙整自 ${nonEmptyUnconfirmed.length} 條已完成軌跡（已自動過濾 ${emptyPendingTracks} 條 0 秒暫存軌跡）`
          )
        );
      }

      if (nonEmptyUnconfirmed.length > 0) {
        const trackDetails = el(results, "details", "behavior-unconfirmed-tracks");
        el(trackDetails, "summary", "", `展開查看 ${nonEmptyUnconfirmed.length} 條「尚未確認身分」獨立軌跡明細（依累計秒數排序）`);
        const trackGrid = el(trackDetails, "div", "behavior-unconfirmed-grid");
        const sortedTracks = [...nonEmptyUnconfirmed].sort((a, b) => sumGroupSeconds(b) - sumGroupSeconds(a));
        for (const tGroup of sortedTracks) {
          const itemCard = el(trackGrid, "div", "behavior-mini-track");
          const trackId = String(tGroup.id || "").replace(/^unconfirmed:/, "");
          const topBehaviors = (tGroup.behaviors || [])
            .filter((b) => Number(b.seconds || 0) > 0)
            .map((b) => `${labels[b.state] || b.state} ${b.seconds.toFixed(1)}s`)
            .join(" · ");
          el(itemCard, "strong", "", `軌跡 ${trackId}`);
          el(itemCard, "span", "", topBehaviors || `事件 ${sumGroupEvents(tGroup)} 筆`);
        }
      }
    } catch (error) {
      message.textContent = error instanceof RangeError ? "請選擇有效日期。" : (error.message || "查詢失敗，請稍後再試。");
    } finally {
      button.disabled = false;
    }
  }
  button.addEventListener("click", load);
  load();
})();
