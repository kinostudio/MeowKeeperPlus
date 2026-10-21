# C701 遠端畫質／PTZ 控制驗收界線（2026-10-06）

## 已由本機驗證

- Tailscale RTSP `c701_sd` 可連線；FFprobe 回報 HEVC、848×480、20 FPS；內附 FFmpeg 成功解出一個影格。這只驗證影音，不代表遠端控制。
- 原有 r54 遠端配對只保存 RTSP 位址，沒有可認證的控制端點；正式 Host 對遠端來源呼叫本機 `_ptz_stream` 必然失敗。即時畫質下拉選單卻仍可按，是介面狀態錯誤。
- 新候選版將配對碼的隨機 nonce 以 Windows DPAPI 分別保存於遠端與 Host；遠端控制代理只聽 Tailscale IPv4 的 TCP 28555，go2rtc API 繼續只聽 `127.0.0.1:21984`。只接受 `status`、單一 stream 的 `subtype=1/2/3/auto`、PTZ 狀態／方向／停止；先驗證 producer 的 DID 指紋與唯一性，不回傳或記錄秘密來源 URL。
- 畫質變更後需重新取得 JPEG 才回報成功；失敗時嘗試回復舊 producer。Host 不對遠端來源呼叫本機畫質 API。控制狀態在背景更新，不讓 Dashboard 等待網路逾時。
- 官方 go2rtc v1.9.14 沒有 Xiaomi MISS PTZ API。候選控制代理在官方 binary 上應回報 `ptz_available=false`；PTZ 指令即使有 HTTP 回應，也不能當作實體鏡頭已轉動。正式 go2rtc.exe 未替換。
- 新 r3 遠端包內附精簡 Python embedded runtime、官方 go2rtc、FFmpeg 與腳本；SHA-256：`0466F340BA24E6A6E9D1DC5FD889ECCDF1DD868FB3BD5707F35A885146F13B5D`。包內不含配對碼、小米 Session、DID、JPEG、Token 或私人影片。

## 尚需真實遠端驗收

1. 在 C701 所在電腦使用 r3 包、完成一輪真正配對；不能以舊 r2 配對碼代替。遠端電腦需以使用者授權建立 `MeowKeeperPlus-C701-Control-Tailnet` 防火牆規則：只允許指定 Host Tailnet IP、此包 `runtime/python.exe`、遠端 Tailnet IP、TCP 28555。未完成前影像可用、控制不可用。
2. Host 端重新配對並實測 `subtype=1→3→1`；每一步比對唯一 producer、真實 JPEG 及畫面規格，確認失敗可回退。切換期間不新增第二個 Xiaomi producer。
3. PTZ 需在隔離實驗版確認端點可讀，並由實體畫面／位置讀回驗證鏡頭方向。先前 026c02 實驗曾回 HTTP 504，不能據此宣稱可用；C701 也尚未實測。官方 binary 不提供此功能。
4. 重新登入、重啟遠端 Windows、Tailscale 斷線恢復與 24H 監控仍需驗收。新版包不能覆蓋正式安裝的 go2rtc.exe。

## 發布狀態

此版是**候選接線，不是完成的 PTZ 發布**。程式測試與遠端影音通過不等於遠端畫質／PTZ 實機驗收通過。只有遠端 r3 程序、安全防火牆、Host 新版、真實畫質往返與實體方向控制全部取得證據，才可標記完整支援。
