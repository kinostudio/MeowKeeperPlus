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

依裝置與帳號實際驗證 region、畫質及持續影格；不能由帳號地區推定後端 region。
實機紀錄只保存在本機。接通與失敗 SOP 見 `XIAOMI_MIOT_REGION_SOP.md`。

## MVP Phase 3C.1 登入診斷

70016 登入回應、官方 release 比對、安全遮罩、冷卻與明確重試流程，請以
`docs/MVP_PHASE3C1_XIAOMI_LOGIN_REPORT.md` 為準。不得將 70016 且 `captchaUrl=null` 直接
翻譯成密碼錯誤。以本次安全診斷為準，不能把舊的登入阻塞寫成當前結論。

## MVP Phase 3C.2 真實裝置同步

當官方 go2rtc session 已存在時，MeowKeeper+ 使用與 go2rtc WebUI 相同的唯讀流程：

1. `GET /api/xiaomi` 取得已登入帳號數量。
2. 依序讀官方支援 region 的 `sources`，以同一 DID／model 建立候選；對候選 region 做單一
   SD JPEG 驗證，只有真實影格才記住該裝置 region。cache 僅含雜湊鍵與 region。
3. Dashboard 只收到 opaque `internal_id`、名稱、model、region、遮罩 IP 與狀態；完整
   `xiaomi://` source 只留在 backend 記憶體，供後續受控 stream PUT 使用。
4. 只有 FFprobe 實測通過後，才以 `xiaomi_camera_<index>` 建立 go2rtc stream，再切入
   既有單一 `FfmpegMediaIngest`；profile probe 的臨時 stream 會在 finally 清理。

尚未取得影格的 region 維持 `unconfirmed`，不以程序存活或 HTTP 200 代替成功證據。
裝置探索與驗證規則見 `XIAOMI_MIOT_REGION_SOP.md`。

## Xiaomi MISS PTZ（實驗、預設關閉）

MeowKeeper+ 已接上可選的 Xiaomi MISS PTZ Dashboard 代理，但正式官方 `v1.9.14` 不含
`/api/xiaomi/ptz`，因此預設只顯示「尚未啟用」，不會移動鏡頭。只有明確使用隔離的
`korasino/go2rtc` `feat/xiaomi-miss-ptz` 實驗版，並以 `--enable-xiaomi-miss-ptz` 或
`MEOWKEEPER_XIAOMI_MISS_PTZ_ENABLED=1` 啟用後，才會對已接入且有活動 producer 的單一
stream 提供方向／停止控制。實驗版、來源 commit、控制邊界與尚未完成的實機驗收，請見
`docs/XIAOMI_MISS_PTZ_EXPERIMENT.md`。不得替換正式 binary，不得在登入或啟動時自動校準或移動。

## 2026-09-25 JPEG／畫質探測診斷

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。
