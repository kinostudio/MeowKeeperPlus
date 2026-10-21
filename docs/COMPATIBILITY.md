# Phase 0 相容性檢測

## 1. 檢測原則

- 只讀、可重跑、可遮罩、可區分「失敗」與「未檢測」。
- 不猜測 D-Link RTSP 路徑。
- 不自動安裝 Python、FFmpeg、CUDA、驅動或額外大型模型；開發／建置階段的預設
  `yolo11n.pt` 由固定官方 release 取得並用 manifest 驗證。
- 不修改 ONVIF、影像、網路或 mydlink 設定。
- 報告不得含 Token、密碼或完整 RTSP userinfo。
- Windows 發布包應優先使用專案內 `vendor/ffmpeg/bin` 的 FFmpeg／FFprobe；使用者不需
  個別安裝全域 FFmpeg。
- YouTube 分析來源需要封裝在 Python runtime 內的固定版 `yt-dlp`、`yt-dlp-ejs` 與
  JavaScript runtime；FFmpeg／FFprobe 也必須由探針確認可執行。私人、會員、年齡限制、
  DRM 與 Cookie 流程不在第一版相容範圍，不猜測串流網址。

## 1.1 產品最低基線

`mx250_minimum` 固定使用以下保守設定：

| 項目 | 基線 |
|---|---|
| GPU | NVIDIA GeForce MX250 |
| VRAM | 2 GiB 預算 |
| 系統 RAM | 8 GiB 最低、16 GiB 建議 |
| 視覺模型 | 同時 1 個 |
| Batch | 1 |
| 輸入 | 640 像素級 |
| AI 採樣 | 約 5 FPS |
| V0.2 共享裁切影格 | 最高寬 1280（不放大來源），YOLO 推論仍為 640；CPU／記憶體增量待 MX250 實測 |
| 身分 embedding | 條件式、最多約 1 FPS |
| 行為彙整 | 約 1 Hz |
| 推論後端 | ONNX Runtime CUDA 優先，CPU 降級 |

這些是預設負載與驗收起點，不是未經實測的效能承諾。TensorRT、姿態模型、
多模型與高 FPS 均延後。

V0.2 的 DINOv2 權重由 `tools\fetch_identity_model.py` 從官方固定來源取得並以
`models\identity\dinov2_vits14\model_manifest.json` 驗證；身分模型只在新 track、低信心、
遮擋或低頻週期使用，不會把 MX250 的高頻 YOLO 負載再複製一份。

## 2. 建議順序

### 步驟 A：本機

```powershell
python tools\run_phase0.py --profile mx250_minimum --output reports\phase0.json
```

統一入口會彙整 Windows、Python、CPU、RAM、GPU、驅動、推論 Provider、FFmpeg、
攝影機及 Telegram。未提供攝影機或 Token 時回報 `not_checked`，不會猜測 RTSP、
修改 ONVIF 或傳送 Telegram 訊息。若 `reports` 不存在，工具會建立它。

仍可分別執行：

```powershell
python tools\system_probe.py --profile mx250_minimum --output reports\system.json
python tools\ffmpeg_probe.py --output reports\ffmpeg.json
```

若 `python` 或 `py` 指向 WindowsApps 且顯示「系統無法存取該檔案」，先用
`Get-Command python, py` 檢查路徑，再安裝可正常執行的 Python 3.10+ 或修正 Windows
「應用程式執行別名」。Codex 的內建 Python 可驗證原始碼，但不應取代本產品自己的
專案虛擬環境。

若在 RTX 2060 或其他較高規格電腦執行，報告會明確標示為非 MX250 開發機。資源
充足只代表探針可執行，不代表 MX250 已達到 5 FPS 或 24 小時穩定性要求。

### 步驟 B：取得攝影機 IP

可從路由器 DHCP 用戶端清單、mydlink App 的裝置資訊或 Windows 網路探索取得區域網路 IP。先確認：

- 電腦與攝影機在可互通的 LAN/VLAN。
- 攝影機 IP 固定或有 DHCP reservation。
- 不將 RTSP/ONVIF 連接埠轉發到公網。

### 步驟 C：準備 RTSP

RTSP 通常包含主機、帳號、密碼、連接埠與串流路徑，但路徑必須以實機文件或 ONVIF 查詢為準。

```powershell
$env:MEOWKEEPER_RTSP_URL = 'rtsp://使用者:密碼@攝影機IP:554/實測路徑'
python tools\camera_probe.py --host 攝影機IP --rtsp-url-env MEOWKEEPER_RTSP_URL --output reports\camera.json
```

工具從環境變數讀取 URI，避免將密碼放入 shell 歷史。報告只保存遮罩後 URI。

預期確認：

- TCP 可達性。
- FFprobe 是否取得影像。
- 影像 codec、解析度、平均／標稱 FPS。
- 是否含音訊及音訊 codec。
- Probe 逾時與認證錯誤類型。

### 步驟 D：ONVIF

目前 `camera_probe.py` 只保留唯讀 ONVIF 介面待辦，不內建第三方相依套件。後續實作前需選定經審核的 ONVIF client，並驗證：

- 裝置資訊與韌體版本。
- Media Profiles 與實際串流 URI。
- PTZ 能力與狀態。
- 既有 Preset 清單。

不執行解析度、編碼、網路、重啟或重設操作。

### 步驟 E：Telegram

先用 BotFather 手動建立一個 Bot，使用者至少傳一次 `/start`。最小 MVP 建議直接登入
Dashboard「設定 → Telegram」，依頁面引導輸入 Token 與 Chat ID；Windows 會使用
Credential Manager（Credential Manager 不可用時使用 DPAPI fallback），頁面只顯示布林狀態，
不回填秘密。可攜式或診斷模式仍可只使用環境變數：

```powershell
$env:MEOWKEEPER_TELEGRAM_BOT_TOKEN = '真實 Token'
python tools\telegram_probe.py --token-env MEOWKEEPER_TELEGRAM_BOT_TOKEN
```

需要明確傳送測試訊息時：

```powershell
python tools\telegram_probe.py --token-env MEOWKEEPER_TELEGRAM_BOT_TOKEN --chat-id 123456789 --send-test
```

`--send-test` 是本階段唯一會產生外部訊息的選項，必須由使用者主動指定。

## 3. 探針輸出狀態

- `ok`：檢測完成且條件符合。
- `warning`：檢測完成，但有缺項或降級。
- `error`：已嘗試檢測且失敗。
- `not_checked`：未提供資料或該檢測未執行。

「未安裝 FFmpeg」不等於攝影機不支援 RTSP；「未提供 Token」不等於 Telegram Bot 無效。

## 4. 仍需實機確認

- D-Link DCS-8526LH B1 的實際韌體、地區版本與 RTSP 路徑。
- 影像是 H.264 或 H.265、解析度、真實 FPS、碼率與 GOP。
- RTSP 是否包含音訊，以及 codec 與同步情況。
- 主／次串流與同時連線上限；mydlink App 開啟時是否互相影響。
- ONVIF Profile、PTZ、Preset 的可用範圍。
- 鏡頭移動完成的可靠判斷方式。
- CPU、GPU、VRAM、RAM、驅動、CUDA／OpenVINO／ONNX Provider。
- MX250 實機以單一模型、batch 1、640 像素級輸入及約 5 FPS 的穩定性。
- FFmpeg/FFprobe 版本與 PATH。
- Telegram Bot、Chat ID、群組權限與控制者白名單。
- 事件影片大小、家中上傳頻寬與 Telegram 傳送時間。

## 5. 報告判讀

Phase 0 報告只描述當次裝置、韌體、網路與軟體狀態。不要把單次失敗直接升級為永久不支援；先排除密碼、連接埠、網路隔離、同時串流數、GOP 與 FFmpeg build 差異。

## 6. V0.2B 推論後端與負載判讀

`tools/gpu_probe.py` 會分開報告 PyTorch CUDA、ONNX Runtime providers、YOLO 實際推論與
DINOv2 實際推論。只有模型實測的 `device` 才能說明本次使用 `PyTorch CUDA`、
`ONNX Runtime CUDA` 或 `CPU`；只看到 provider 名稱不代表 `.pt` 模型已使用 CUDA。

MX250 產品最低契約固定使用 2 GiB VRAM 預算、8 GiB RAM、batch 1、640 像素級輸入與單一
高頻視覺模型。16 GiB RAM 只增加系統餘裕，不解除 GPU profile 限制。CUDA 不可用時，
行為偵測仍以 1～3 FPS CPU fallback 運行，DINOv2 可延後或停用；不得因身分模型失敗而
停止 RTSP、環形快取或必要通知。

## 7. MVP Release Gate 補充

相容性探針與恢復測試只能描述當次環境。探針缺少 RTSP、YouTube、Token、FFmpeg 或推論
套件時回報 `not_checked`／`warning`，不猜測路徑、不傳送訊息、不自動安裝。現有 RTX 2060
開發機的結果只能作為開發證據；MX250、8 GiB RAM 最低與 16 GiB 建議仍需目標機實測。

`tools/build_portable.py` 驗證建置者提供的 embedded Python、內含 FFmpeg、YOLO manifest
與必要 runtime；它不會偷偷改動 Python、驅動或 CUDA。終端使用者只執行發布包內啟動器。
