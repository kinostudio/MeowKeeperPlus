# r94 安裝入口修補：執行期程序鎖

## 原因

啟動 r94 可攜版後，`CrossProcessLock` 在 `data/locks/` 建立 Host PID sidecar。
原本安裝入口使用全新發布包的嚴格驗證，將此檔案判定為 manifest 外檔案。
本輪檢查時 Host PID 17440 仍存活，不能將鎖檔當成 stale 直接刪除。
後續完整驗證亦發現原包 `data/meowkeeper.db`；因此不能直接沿用該使用中的目錄作為安裝來源。

## 最小修正

- 預設 `verify_portable` 仍嚴格拒絕額外檔案。
- 安裝來源驗證明確使用 `--allow-runtime-locks`；僅接受 Host、Tray、Go2Rtc 三種已知 JSON sidecar，並限制大小及結構。
- 模型、程式與所有 manifest 檔案的大小及 SHA-256 仍完整驗證。
- 複製後僅移除安裝目錄內的三種 sidecar；不刪除來源的活動鎖檔。
- 安裝目錄仍使用嚴格驗證，不能攜帶舊 PID 鎖。
- r94 發布包只更新 verifier、installer 與其 manifest 校驗碼，未混入尚未驗收的首頁改版。
- 依 manifest 建立獨立 `dist/MeowKeeperPlus-portable-2026-10-13-r94-install-fix`；不複製私人資料庫或程序鎖，原使用資料保留不動。

## 驗證邊界

5 個相關單元測試、Ruff 與 Windows PowerShell parser 通過。
完整發布包校驗另由 bundled Python 實際執行。
乾淨修補包的嚴格校驗結果：`passed`，20,569 個檔案，`private_data_present=false`；YOLO SHA-256 仍為 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`。
本輪不自動安裝、不觸發 UAC；真正升級仍由使用者執行。
若另有可攜版 Host 正在監聽 Dashboard 埠，先透過其 Tray 完整關閉，避免舊程序讓安裝後健康檢查產生誤判。

尚未完成：正式安裝後首頁真實影格、Tray 原生文字與逐頁 Figma 視覺驗收。
