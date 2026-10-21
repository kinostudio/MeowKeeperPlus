# 攝影機接入與即時監控改版：接線與隔離驗收

日期：2026-10-18。本輪為 repository 修改與隔離驗收，**沒有建置、發布、安裝、UAC 或重啟正式 Host**；既有 r106 包不自動包含本次程式。

## 接線規格

- 小米設定統一為登入、選攝影機、一鍵連線並監控三步；手動測試及登入診斷為次要操作。首次採 subtype=1，已有畫質沿用後確認影格。背景工作需影格證據、重複點擊不重建，沿用來源切換 callback 的失敗恢復流程。
- 每裝置保存明確 local／relay 選擇；已配對不代表正在使用遠端，不依型號猜所在地，不偷偷 fallback。
- 同套 Host 的「此電腦提供中繼」：選裝置、驗證畫面、選在線 Tailnet 對端、明確授權限縮規則、產生十分鐘配對碼。首次只分享影音／控制，不啟動 AI／Telegram；已有相同來源時共用。不同活動來源先拒絕。
- 固定串流 TCP RTSP 28554、控制 28555 只綁 Tailnet IPv4 並限制對端。拒絕 UDP SETUP、其他串流／任意路徑；控制驗證碼以 Windows 加密保存。go2rtc API 不公開；停止撤銷碼並關閉端點，不自行修改使用者規則。角色保存於 Data 對應 state 的 `xiaomi-relay-provider.json` 與 `.dpapi`，啟動恢復失敗會明示待啟用。
- 首頁與即時頁統一「截圖」「錄影（15秒）」；錄影保存按下前十五秒，以結果實際長度為準。提示在影片內、全螢幕可見，成功六秒、hover／focus 保留，可展開／複製，錯誤手動關閉。
- 原始影像／目前播放／AI 三張規格卡；設定上限不當成實測 FPS、無資料不補零或編造驗證時間。PID 收進技術資訊；畫質／PTZ 桌機雙欄、窄版堆疊。
- 座標圖支援點擊、觸控、方向鍵選點及 Enter。需有效讀回與 absolute_move 能力；範圍優先裝置值，缺少時採協定已驗證 1–101，不稱角度。目標空心、實際實心，容差內才報到位；忙碌不重送、Esc／停止可用，停止未確認不能報成功。來源 epoch 改變清空舊位置。載入讀一次，結果 HUD 四秒；舊中繼缺能力停用。
- 首頁硬體主區並列預覽，下列三服務狀態；CPU／RAM／GPU 環圈與 CPU／GPU 指針共用五秒快取。歷史每分鐘保存、磁碟掃描不提高頻率。缺值／過期與正常值分開，尊重 reduced-motion。24H 曲線、事件及通知統計留在下方；事件清單限高，篩選／分頁在外，後端篩選後才分頁。

## 新接口與主要檔案

- POST `/api/v1/cameras/xiaomi/devices/{id}/connect` 接受 `connection_mode: local|relay`；GET `/api/v1/cameras/xiaomi/setup/operations/{id}` 讀進度及安全錯誤。
- GET `/api/v1/cameras/xiaomi/relay-provider`；POST 其 `enable`、`pairing`、`firewall`、`stop`、`revoke`。登入／CSRF 保護；配對碼單次領取、有期限，重產需停止後啟用。
- `connection_jobs.py`、`relay_provider.py`、`relay_firewall.py`、`Manage-XiaomiRelayFirewall.ps1`；`xiaomi-wizard.js`、`monitor-ui.js`、`monitor.css`、`live.html`。SYSTEM 不假稱 UAC 已出現，提供管理員命令；本次未執行真實規則變更。

## 隔離驗收

| 項目 | 結果／界線 |
| --- | --- |
| 完整回歸 | passed：871 tests／239.282 秒／OK（包含明確啟用的真實 loopback 媒體測試、零略過），`unittest-final.log`。之後新增的配對 API 單次領取／撤銷測試另隨十項定向測試通過；不把首輪 3 failures／1 error 當成功 |
| 最後修改覆核 | passed：13 項接入／中繼真實媒體／PTZ 定向測試，`targeted-final.log`；不同連線方式不可取得既有工作的成功結果、Session 0 不假稱能顯示 UAC、中繼畫質保留已驗證尺寸。最終 Ruff／compileall 與 38 項故障門檻再通過 |
| 接入、錯誤不切來源、記憶畫質、重複工作、無影格失敗 | passed：`tests.test_camera_connection_workflow`，替身 manager |
| 登入／CSRF、單次配對／撤銷、範圍與停止未確認不能報成功 | passed：同上，無真實 UAC／馬達 |
| 真實媒體轉送 | passed：`RUN_MEOWKEEPER_RELAY_MEDIA=1 python -m unittest tests.test_relay_loopback_media -v`；vendor FFmpeg 合成 H.264、真實 go2rtc／正式 TCP bridge 取得 JPEG、單 producer、控制及畫質通路。位置與畫質為替身，不是異地測試 |
| 隔離 Edge | passed：`tests/browser/camera_redesign_browser.cjs` 27 項、無 pageerror；首頁影格、第一屏、保存／全螢幕、點擊／鍵盤／觸控座標、過期值與三步介面 |
| 響應式 | passed：360／390／768／1440／1920px 四頁無水平溢出；720×405 CSS viewport 作 200% 等效重排，不冒充真實系統縮放 |
| 靜態／JS | passed：六支 JS runtime、Ruff、compileall、PowerShell parser |
| 故障門檻 | passed：14 類／38 tests，新增 CAMERA-ONE-CLICK-RELAY。`reports/camera-ui-redesign-2026-10-18/regression-gate.log` 不代替新包簽署證據 |

瀏覽器先啟動 `tests/camera_redesign_fixture.py --port 18979`，再執行上述 Node 腳本，`MEOWKEEPER_PLAYWRIGHT` 指向本機套件；只接受 loopback fixture。畫面明示合成數據，不使用秘密、真實影像或感測器。截圖、JSON、完整測試 log 位於 `reports/camera-ui-redesign-2026-10-18/`。

## 本輪發現及修正

- 合成測試的 exec 裸 H.264 讓 go2rtc unsupported header／404；改成 MPEG-TS 後取得有效影格。這不是攝影機協定結論。
- 完整回歸抓到最後 WebRTC 觀看者離開時 `_stop` 重複關閉 PyAV container 的競態。先在 event loop 脫離 track 回呼，再單次停止 worker，雙 peer 關閉回歸通過。
- 事件篩選原只篩當頁，改後端篩選再分頁；未有全域待回報查詢，不顯示誤導選項。
- 首頁超出第一屏／HUD 重疊，已收斂留白與重排標記，加入第一屏幾何斷言。保存測試改等待實際結果，不只等提示出現。
- 舊測試綁定已移除說明或 inline JS，改檢查新流程及共享連線選擇；安全及結果斷言保留，非刪除故障測試。

## 仍需實機驗收（not_checked）

- 真實兩台 Tailscale 電腦的授權、影音／畫質／PTZ、錯配／過期／撤銷、重啟及單攝影機上游。
- 實體短距離上下絕對定位、前後讀回、停止、未到位／斷線；替身位置不能當成馬達證據。
- 小米雲端全新／重新登入、區域探索失敗、真實舊來源恢復；全新 Windows 包、UAC、24H、中低階硬體、完整輔助科技與人工視覺覆核。
- 本次唯讀檢查 `127.0.0.1:8000/healthz` 及 `192.168.31.200:8000/healthz` 均無法建立連線，沒有為此啟動／升級正式服務。

結論：上述 repository 接線與隔離證據成立；**不宣稱正式實機全部完成**。建置／發布另依要求，正式安裝由使用者操作。

## 2026-10-18 首頁卡片與預覽尺寸覆核修正

- 覆核發現 `.hero-services` 曾用 CSS 隱藏三張卡的資料列、說明、狀態補充及副標；現已恢復全部資訊。資訊太多時讓頁面自然延伸，不刪除資料。
- 預覽容器明確固定 16:9，不讓視窗高度上限壓扁影像；桌機首頁預覽與硬體欄改為 60／40，窄版堆疊。圖片仍 `object-fit: contain`，不裁切來源影格。
- 新增 `DASHBOARD-SERVICE-CARD-DETAILS-AND-PREVIEW-SIZE` 回歸測試。專案 `.venv` 的 Dashboard 測試 5/5 passed；本機開發頁實際 DOM 驗收：390／768／1000／1440 px 均無水平溢出、預覽比率約 1.778、三卡明細均顯示；1440 px 預覽欄寬占硬體＋預覽約 60%。
- 驗收頁是 `18981` repository 開發 Host，監控未啟動、無攝影機來源；因此只證明布局與前端接線，不代表真實攝影機影格或正式安裝版已驗收。本輪未建置、未安裝、未重啟正式 Host。

## 使用者授權啟動後補記

2026-10-18：使用者允許開啟 Host。正式 SYSTEM Core 的排程查詢與執行皆被 Windows 拒絕，未變更其權限、安裝或啟動 UAC。改以最新 repository 程式啟動獨立 Host：`http://127.0.0.1:18980`，`/healthz` HTTP 200，`core_status=stopped`。資料根為本報告同名 reports 目錄下的 `interactive-host/`；監控未啟動、未連真實攝影機，沒有用正式資料。這個入口可供使用者首次設定並查看最新 UI，不表示正式 SYSTEM 服務恢復或真實驗收完成。
