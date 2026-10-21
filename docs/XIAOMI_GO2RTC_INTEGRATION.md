# 小米 Mi Home／go2rtc 整合邊界

## 目標架構

```text
小米原廠韌體 → 官方 go2rtc Xiaomi source → localhost RTSP／WebRTC restream
             → 單一 MeowKeeper+ FfmpegMediaIngest
```

Dashboard 不會直接登入 Mi Home，也不會另開攝影機連線。使用者在官方／go2rtc 流程自行
完成 Email、簡訊、CAPTCHA 或其他驗證；MeowKeeper+ 只接收不含秘密的裝置清單與使用者
確認的 model／region／firmware metadata。

## go2rtc 安全條件

- 只接受 `AlexxIT/go2rtc` 官方 repository 的固定 release；binary 必須有版本、授權、大小
  與 SHA-256 manifest。
- 目前已內含官方 `v1.9.14` Windows x64 binary：`vendor/go2rtc/v1.9.14/go2rtc.exe`，並由
  `vendor/go2rtc/model_manifest.json` 固定 release、asset、大小與 ZIP／EXE SHA-256。
- 管理與 restream 只綁 localhost；不將 go2rtc 管理 UI 暴露到 LAN、Tailscale 或 Funnel。
- 程序要有逾時、watchdog、正常關閉；程序失敗不能停止既有 AI、環形快取或 Telegram。

## 畫質驗證

橋接先以單一 `subtype=sd` 影格驗證後端 region，再以 FFprobe 記錄解析度、實際 FPS、編碼、
音訊、錯誤與穩定性。已驗證 region 的 MVP 路徑不做全 profile sweep；不能因名稱是 HD 就宣稱 2K。

go2rtc binary／localhost start-health-stop 已由自動測試與真實程序驗證。2026-09-20 的
隔離實測中，`chuangmi.camera.026c02` 在 `sg` 後端 region 以官方 go2rtc v1.9.14 取得
真實 JPEG；單一 SD profile 的 FFprobe 得到 640×360 HEVC、G.711 A-law。`isa.camera.hlc7`
仍無影格，標記 `unconfirmed`。這不代表台灣帳號一律使用 SG，也不代表正式 Dashboard 已完成驗收。
接通與失敗 SOP 見 `docs/XIAOMI_MIOT_REGION_SOP.md`。

## MVP Phase 3C.1 登入診斷

70016 登入回應、官方 release 比對、安全遮罩、冷卻與明確重試流程，請以
`docs/MVP_PHASE3C1_XIAOMI_LOGIN_REPORT.md` 為準。不得將 70016 且 `captchaUrl=null` 直接
翻譯成密碼錯誤。該報告記錄的是當時狀態；目前隔離 session 已由使用者完成登入並取得兩台裝置，
不能再把舊的登入阻塞寫成當前結論。

## MVP Phase 3C.2 真實裝置同步

當官方 go2rtc session 已存在時，MeowKeeper+ 使用與 go2rtc WebUI 相同的唯讀流程：

1. `GET /api/xiaomi` 取得已登入帳號數量。
2. 依序讀官方支援 region 的 `sources`，以同一 DID／model 建立候選；對候選 region 做單一
   SD JPEG 驗證，只有真實影格才記住該裝置 region。cache 僅含雜湊鍵與 region。
3. Dashboard 只收到 opaque `internal_id`、名稱、model、region、遮罩 IP 與狀態；完整
   `xiaomi://` source 只留在 backend 記憶體，供後續受控 stream PUT 使用。
4. 只有 FFprobe 實測通過後，才以 `xiaomi_camera_<index>` 建立 go2rtc stream，再切入
   既有單一 `FfmpegMediaIngest`；profile probe 的臨時 stream 會在 finally 清理。

`cn` 的歷史實測曾收到 `HTTP 500: streams: xiaomi: no p2pid allocated, please reboot device`；
同一 DID 在 `sg` 的單 stream 真機測試已有 JPEG。當前自動探索對 `.55` 選 `sg`，對 `.56`
維持 `unconfirmed`。舊報告是歷史診斷，不能拿來覆蓋新的成功／失敗邊界；詳見
`docs/MVP_PHASE3C2_XIAOMI_DEVICE_SYNC_REPORT.md` 與 `docs/XIAOMI_MIOT_REGION_SOP.md`。

## Xiaomi MISS PTZ（實驗、預設關閉）

MeowKeeper+ 已接上可選的 Xiaomi MISS PTZ Dashboard 代理，但正式官方 `v1.9.14` 不含
`/api/xiaomi/ptz`，因此預設只顯示「尚未啟用」，不會移動鏡頭。只有明確使用隔離的
`korasino/go2rtc` `feat/xiaomi-miss-ptz` 實驗版，並以 `--enable-xiaomi-miss-ptz` 或
`MEOWKEEPER_XIAOMI_MISS_PTZ_ENABLED=1` 啟用後，才會對已接入且有活動 producer 的單一
stream 提供方向／停止控制。實驗版、來源 commit、控制邊界與尚未完成的實機驗收，請見
`docs/XIAOMI_MISS_PTZ_EXPERIMENT.md`。不得替換正式 binary，不得在登入或啟動時自動校準或移動。

## 2026-09-25 JPEG／畫質探測診斷

- 隨附 FFmpeg 8.1.2 的 RTSP demuxer 使用 `-timeout`（微秒）；對本機 RTSP 使用
  `-rw_timeout` 會在連線前回 `Option rw_timeout not found`。品質 FFprobe 已改用前者。
- Xiaomi 視訊來源若沒有音訊，go2rtc 可能先收到影像卻在音訊探測期限後回
  `xiaomi: probe: no audio`。視訊優先的來源在未明確指定音訊時加入 `audio=0`；
  明確指定的音訊值保持不變。
- go2rtc `/api/frame.jpeg` 對 H.265 需要 FFmpeg 轉碼；若 SYSTEM 程序找不到
  `ffmpeg.exe`，即使已有活動 producer，仍會回 HTTP 500。MeowKeeper+ 新啟動的
  go2rtc 子程序會把包內 FFmpeg 目錄加入自身 PATH，不覆寫既有 Xiaomi Session 設定。
- 正式既有本機 RTSP 通道已由包內 FFmpeg 取得有效 640×360 JPEG；FFprobe 讀到
  HEVC、20 FPS；repository 的 `FfmpegFrameSource` 與 Xiaomi 內附 FFmpeg 備援也取得
  真實 JPEG。這僅驗證當時已接通的通道，不代表 `isa.camera.hlc7` 已成功。
  修正後的 `FfmpegFrameSource(max_frames=1)` 實測輸出有效 JPEG；Xiaomi 的
  `_probe_bundled_jpeg_frame` 對同一既有通道也回 `True`。相關 212 個 unittest、Ruff
  與 compileall 已通過；這些自動檢查不能代替 HLC7 真機驗收。
- 正式 go2rtc 設定已經由 localhost API 最小 PATCH 加上包內 FFmpeg 路徑；PATCH 前後
  除 `ffmpeg.bin` 外的設定語意相同，Xiaomi 帳號數維持 1。`POST /api/restart` 回 200
  但正式 SYSTEM 程序沒有重新載入設定，故其 JPEG API 仍為 HTTP 500。需要先確認
  SYSTEM 工作可安全重新啟動，才可宣稱該 API 修復完成；不可因此重建 Xiaomi 登入。
- `isa.camera.hlc7` 在修正逾時參數、視訊專用模式及 FFmpeg 路徑後仍欠一輪限縮
  UDP 規則下的真實 JPEG 驗收。前一輪臨時規則已移除、測試串流已清空；不得把
  producer 曾建立或 JPEG HTTP 500 誤寫成影格成功。
