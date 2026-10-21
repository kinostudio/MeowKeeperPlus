(() => {
  const period = document.getElementById('resource-period');
  const button = document.getElementById('resource-load');
  const message = document.getElementById('resource-message');
  const charts = document.getElementById('resource-charts');
  const chartsView = window.MeowKeeperResourceCharts?.mount(charts);
  let current = null;
  const temperature = document.getElementById('cpu-temperature-value');
  let temperatureBusy = false;
  function showTemperature(data) {
    if (!temperature) return;
    const sample = data.cpu_temperature || {};
    const states = {available:'可用',unavailable:'無法取得',permission_required:'需要額外權限',unsupported:'目前未支援',stale:'暫時無法更新',error:'讀取暫時失敗'};
    const reasons = {helper_missing:'此程式目錄尚未包含已校驗的 CPU 感測器元件。',driver_missing:'尚未安裝所需感測驅動。',driver_unavailable:'感測驅動不可用或目前權限不足。',monitoring_paused:'監控尚未啟動或已停止；不啟動額外感測工作。',disabled:'CPU 溫度監測已停用。',sample_expired:'資料已超過更新時間，不再顯示成目前溫度。',helper_timeout:'感測器讀取逾時，背景正在退避重試。',helper_invalid:'感測器完整性或回應驗證失敗。',trusted_sensor_missing:'沒有可確認物理意義的 CPU 感測值。'};
    temperature.textContent = sample.status === 'available' && Number.isFinite(sample.temperature_c) ? `${sample.temperature_c.toFixed(1)}°C · ${states.available}` : (states[sample.status] || '無法取得');
    const updated = sample.observed_at ? new Date(sample.observed_at).toLocaleString() : '尚無紀錄';
    const stampLabel = sample.status === 'available' ? '最近量測' : sample.status === 'stale' ? '上次量測' : '最近檢查';
    document.getElementById('cpu-temperature-context').textContent = `${sample.hardware_name || 'CPU 型號尚未回報'} · ${sample.sensor_name || '感測器尚未確認'}｜${stampLabel}：${updated}。`;
    temperature.style.color = sample.status === 'available' ? '#45674e' : '#825c2c';
    document.getElementById('cpu-temperature-reason').textContent = sample.status === 'available' ? '可信直接感測；不能用主機板溫度替代。' : (reasons[sample.error_reason] || '目前無法讀取這台電腦的 CPU 溫度，但不影響基本監控。');
    const stats = data.cpu_temperature_hour || {};
    const degree = value => Number.isFinite(value) ? `${value.toFixed(1)}°C` : '尚無有效資料';
    document.getElementById('cpu-temperature-hour').textContent = `近一小時平均：${degree(stats.average)}｜採樣峰值：${degree(stats.peak)}（歷史每分鐘記錄，不代表瞬間絕對峰值）`;
    const details = document.getElementById('cpu-temperature-details'); details.replaceChildren();
    const providers = {libre_hardware_monitor:'LibreHardwareMonitor（本機 CPU 感測）',psutil_cpu:'作業系統可信 CPU 感測'};
    const types = {cpu_package:'CPU Package（處理器封裝）',cpu_die:'CPU Die（處理器晶片）',cpu_core:'CPU Core（單一核心，非封裝）'};
    for (const [label,value] of [['感測來源',providers[sample.provider] || '尚未確認'],['感測類型',types[sample.sensor_type] || '尚未確認'],['採樣間隔',`${sample.sample_interval_seconds ?? 5} 秒（背景共用，不依觀看人數增加）`],['資料狀態',states[sample.status] || '尚未確認'],['Helper 狀態',sample.helper_running ? '正在運作' : '未運作'],['可信狀態',sample.confidence === 'direct_sensor' ? '直接感測' : '尚無可信值']]) {node(details,'dt',label);node(details,'dd',value);}
  }
  function node(parent, tag, text) {
    const item = document.createElement(tag); item.textContent = text; parent.append(item); return item;
  }
  function render() {
    if (!current) { charts.replaceChildren(); return; }
    const hours = Number(period.value);
    const specs = [
      {title:'CPU 使用率',series:[{key:'system_cpu_percent',label:'整台電腦',color:'#A94F24'},{key:'app_cpu_percent',label:'喵喵守護者+',color:'#426C81',dashed:true}],unit:'%',fixedRange:[0,100],rangeSeconds:hours*3600,
        note:'顯示整台電腦與喵喵守護者+程序的 CPU 使用率；可移動游標查看各次分段採樣。'},
      {title:'RAM 使用率',series:[{key:'system_ram_percent',label:'整台電腦',color:'#73543D'},{key:'app_ram_percent',label:'喵喵守護者+',color:'#55724D',dashed:true}],unit:'%',fixedRange:[0,100],rangeSeconds:hours*3600,
        note:'顯示整台電腦與喵喵守護者+程序群的記憶體比例；可移動游標查看各次分段採樣。'},
      {title:'CPU／GPU 溫度',series:[{key:'cpu_temperature_c',label:'CPU（可信感測）',color:'#A94F24'},{key:'gpu_temperature_c',label:'GPU（既有來源）',color:'#426C81'}],unit:'°C',rangeSeconds:hours*3600,
        note:'只顯示感測器實際回報的溫度；沒有感測資料時保留空白。'},
      {title:'守護者記憶體用量',series:[{key:'app_rss_bytes',label:'程序群 RAM',color:'#55724D',divisor:1000000}],unit:'MB',rangeSeconds:hours*3600,
        note:'顯示 Host、AI 與相關程序的記憶體總量；共享頁面可能重複計入。'},
      {title:'程式與資料磁碟占用',series:[{key:'total_disk_bytes',label:'總占用',color:'#A94F24',divisor:1000000000},{key:'app_disk_bytes',label:'程式與模型',color:'#426C81',divisor:1000000000},{key:'data_disk_bytes',label:'資料與影片',color:'#55724D',divisor:1000000000}],unit:'GB',rangeSeconds:hours*3600,
        note:'顯示程式、模型及資料檔案大小；總占用已包含程式與資料兩項。'}
    ];
    chartsView?.render(current, specs);
  }
  async function load() {
    button.disabled = true; message.textContent = '正在讀取資源歷史…';
    try {
      const response = await fetch(`/api/v1/analytics/resources?hours=${period.value}`, {cache:'no-store'});
      if (response.status === 401) throw new Error('請重新登入後查看資源趨勢。');
      const data = await response.json(); if (!response.ok) throw new Error(data.detail || '資源歷史無法讀取。');
      current = data; render(); showTemperature(data);
      const latest = data.latest;
      message.textContent = data.warning || (data.status === 'no_data' ? '這段期間尚無紀錄。Host 啟動約一分鐘後開始採樣，歷史不會追溯補造。' : `最近採樣：${new Date(latest.sampled_at * 1000).toLocaleString()}；${latest.process_count ?? '未確認'} 個相關程序。磁碟統計：${latest.disk_sampled_at ? new Date(latest.disk_sampled_at * 1000).toLocaleString() : '尚在統計'}。`);
    } catch (error) { message.textContent = error.message || '查詢失敗。'; }
    finally { button.disabled = false; }
  }
  button.addEventListener('click', load); period.addEventListener('change', load);
  document.getElementById('cpu-temperature-refresh')?.addEventListener('click', load);
  async function refreshTemperature() {
    if (!temperature || document.hidden || temperatureBusy) return;
    temperatureBusy = true;
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 3000);
    try {
      const response = await fetch('/api/v1/telemetry/cpu-temperature', {cache:'no-store',signal:controller.signal});
      if (!response.ok) throw new Error(response.status === 401 ? '登入已失效，請重新登入。' : 'CPU溫度狀態暫時無法更新。');
      showTemperature({cpu_temperature:await response.json(),cpu_temperature_hour:current?.cpu_temperature_hour});
    } catch (error) {
      temperature.textContent = '暫時無法更新';
      document.getElementById('cpu-temperature-reason').textContent = error.name === 'AbortError' ? '狀態查詢逾時；不繼續顯示舊溫度。' : error.message;
    } finally {clearTimeout(timeout);temperatureBusy = false;}
  }
  const cpuTimer = setInterval(refreshTemperature, 5000);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) refreshTemperature();});
  window.addEventListener('pagehide', () => clearInterval(cpuTimer), {once:true});
  load();
})();
