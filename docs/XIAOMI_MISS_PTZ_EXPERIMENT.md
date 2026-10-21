# Xiaomi MISS PTZ 實驗整合

## 狀態

MeowKeeper+ 已加入對 Xiaomi MISS PTZ HTTP 介面的安全、可選接線，但**預設不啟用**，
也沒有替換正式的 `AlexxIT/go2rtc v1.9.14` binary。這一頁描述的是實驗功能，不是家庭環境
已驗收或可對外發布的 PTZ 支援。

目前採用的上游實驗來源是 `korasino/go2rtc` 的 `feat/xiaomi-miss-ptz` 分支，固定審查
commit `e746789ecbcfe663d6e22a69eac586813b4cb575`。該分支在 go2rtc 內新增
`/api/xiaomi/ptz`，並將 MISS motor operation 映射為左右上下、停止、校準、重新讀取位置與
絕對位置。官方 `AlexxIT/go2rtc` v1.9.14 沒有這個 endpoint；MeowKeeper+ 不會把第三方
分支偷偷放進正式 `vendor/go2rtc/v1.9.14/go2rtc.exe`。

## 執行邊界

- Dashboard 只呼叫 localhost go2rtc API，不直接接觸 Xiaomi source URL、DID、帳號、Cookie 或 token。
- PTZ 只有在 Host 明確使用 `--enable-xiaomi-miss-ptz`，或設定
  `MEOWKEEPER_XIAOMI_MISS_PTZ_ENABLED=1` 時才顯示控制介面。
- 即使啟用旗標，仍必須由已建 producer 的 Xiaomi stream 提供 PTZ controller；沒有活動 producer
  時只回報未就緒，不會建立第二條來源連線。
- 所有 Dashboard PTZ API 仍受既有登入與 CSRF 保護。
- 相對移動只允許 `left`、`right`、`up`、`down`；停止為獨立操作。位置值限制在 1～101。
- 不執行自動移動、不在登入或啟動時校準、不將 PTZ 指令寫入資料庫或日誌。
- 官方 binary、正式 manifest 與正式設定不會因為這項接線被覆蓋。

## Dashboard API

以下路徑是 MeowKeeper+ 的登入保護代理，不是瀏覽器直連 go2rtc：

```text
GET  /api/v1/cameras/xiaomi/devices/{opaque_device_id}/ptz
POST /api/v1/cameras/xiaomi/devices/{opaque_device_id}/ptz
```

「即時畫面」頁會讀取目前已選取的單一 Xiaomi 裝置，並在實驗端點已啟用時顯示
左右上下、停止與讀取狀態按鈕。每次按鈕只送出一個受 allow-list 限制的請求；
開啟預覽不會增加 RTSP、FFmpeg 或 producer。使用官方 go2rtc 時，控制區保持停用並
顯示未啟用原因。

POST body 只接受下列欄位：

```json
{"action":"move","direction":"left"}
{"action":"stop"}
{"action":"refresh"}
{"action":"calibrate"}
{"action":"position","angle":50,"elevation":50}
```

回應只保留連線狀態、位置、限制與 capability 欄位。go2rtc 失敗時，Dashboard 顯示繁體中文
分類，不回傳原始錯誤、完整 URL 或任何秘密。

每次讀取或控制回應也包含 `success` 布林欄位：`true` 只代表本次 localhost
MISS PTZ 請求得到可用回應；`false` 代表未啟用、尚未接入、端點不存在、攝影機未連線
或操作失敗。即時畫面會保留這次成功／失敗結果，不會在背景重新整理時覆蓋訊息。

## 建置實驗版（尚未在本機編譯）

目前開發環境沒有 Go toolchain，因此本次沒有自動安裝 Go、沒有下載或替換 binary，也沒有
宣稱 PTZ 已經實機成功。若要建立隔離實驗版，應由維護者在專用目錄：

```text
vendor/go2rtc/experimental/xiaomi-miss-ptz/e746789ecbcfe663d6e22a69eac586813b4cb575/
```

以該 commit 的原始碼、固定 Go 版本與可重現 build 產生獨立 executable，並另存來源 commit、
原始碼 hash、binary hash 及授權資訊。實驗版必須使用獨立 API／RTSP port 與工作目錄，先以
單一已驗證的 Xiaomi stream 做 GET 狀態測試，再由使用者明確按下方向按鈕；未通過前不得
替換正式 `go2rtc.exe` 或進入 Windows 發布包。

## 驗證狀態

- Python client 的 request allowlist、JSON body、方向與位置驗證：可由自動測試驗證。
- Dashboard 登入／CSRF 路由與停用時不動作：可由自動測試驗證。
- 官方 v1.9.14 是否支援 `/api/xiaomi/ptz`：已在隔離 port 21984、無帳號／攝影機 session
  的官方 binary 啟動中實測 HTTP 404；正式 binary SHA-256 為
  `923D57252E8139A69C52E4ACC1E399A640244A8EF457FD9B7267A25847D68F8C`，因此目前不能宣稱
  本地方向控制已可用。
- 實際 Xiaomi MISS motor command、方向、停止與位置回應：尚未驗證，需隔離實驗 binary、
  已登入 session、活動 producer 與使用者明確操作。
