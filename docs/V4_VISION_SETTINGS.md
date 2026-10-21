# V4.3 視覺服務設定與安全保存（施工中）

## 目前接線

Dashboard `/settings/ai` 新增可選視覺服務表單。`/api/v1/vision/settings` 沿用登入與 CSRF；`/api/v1/vision/credential` 不回填 Key，只回傳是否保存，HTTP 金鑰操作僅限 loopback，其他連線需 HTTPS。未改登入／過期／密碼系統。

非秘密設定位於 data/state/vision-analysis.json。服務網址／模型變更後原同意失效，須重新明確確認；預設關閉。Key 由目前 Host Windows 身分的 DPAPI 加密，按服務／模型的安全摘要分開保存於 data/secrets/vision；不能把新服務 Key 送到舊網址。此資料夾明確排除於完整 ZIP 資料備份，換帳號／電腦需重新保存 Key，不暗中匯出不可攜的憑證。

Host 啟動讀取設定，啟用狀態與 grace 需重啟套用。已啟用 provider 每個工作重新讀取設定與同一份 Key 快照；停用後未开始請求會略過，已开始者不能保證召回。設定壞掉或秘密不可讀時略過進階分析，不阻塞本機影音與通知。

## 實際驗證與邊界

已通過暫存資料下的實際 Windows DPAPI roundtrip，不使用使用者 Key；登入/CSRF、重新同意、Key不回填、備份排除、停用無請求等測試亦有覆蓋。

隔離 server 127.0.0.1:18943 曾以測試帳號實際保存未啟用的 example.invalid 設定與 grace=7，重新載入仍為7，enabled/consent保持false；沒有外部 API。390px 寬 pageWidth=375，無水平溢出。Enter開啟說明、Esc關閉及焦點恢復已驗證，console error=[]。隔離分頁已關閉，port18943後續查詢無listener。

尚未完成正式 Host部署、真實Provider、測試連線按鈕、原生多協定服務選單、三段偵測/身分/API能力互鎖與全站無障礙驗收。不要把表單可保存當成辨識成功或已取得家庭外送同意。

## 2026-10-15 請求防護

schema19在同一SQLite新增vision_request_jobs／vision_provider_circuits，不新增第二套事件。每日上限預設100，按目前服務/模型摘要與UTC日計算；保留失敗與中斷的預算，這是保守的工作保留數，不等於已收費請求或金額。刪除影片不會刪除請求預算；非秘密工作稽核保留90天。

event＋provider摘要＋時序输入hash作冪等鍵，重送不再外送。跨SQLite連線一次只准一個running請求；45秒lease過期記interrupted、不自動重送同job。429遵守安全解析的Retry-After，至少冷卻60秒；連續3個timeout/5xx/格式錯誤冷卻60秒，期滿一次試探。401/403需使用者重新安全保存Key才能重置；一般設定保存不清掉封鎖或已用預算。

此版本仍不自動重試，避免重複費用。全階段API/通知統計、人工有效性分母與正式Provider效能仍待完成。
