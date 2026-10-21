# Xiaomi MISS PTZ 隔離實測紀錄（2026-10-02）

## 範圍

本次只使用已登入的本機 Xiaomi session，以獨立 go2rtc 實驗程序測試
`chuangmi.camera.026c02`。沒有修改正式 `go2rtc.exe`、正式設定、
MeowKeeper+ 串流核心，也沒有執行 profile sweep 或 PR 套用。

隔離端點：API `127.0.0.1:21984`、RTSP `127.0.0.1:28554`。測試只建立
一個暫時 stream，完成後刪除；正式 `xiaomi_camera_2` stream 已由記憶中的
來源恢復。

## 安全結果

| 項目 | 結果 |
| --- | --- |
| 隔離 Xiaomi account count | 1 |
| 目標裝置 | 找到（只保存 model） |
| 暫時 stream PUT | 成功 |
| 隔離 producer 峰值 | 1 |
| 隔離 JPEG | 成功 |
| PTZ 狀態 | 失敗；Python client 收到 RuntimeError |
| PTZ 方向移動 | 未送出（狀態端點未先確認可用） |
| 暫時 stream 釋放 | 成功 |
| 正式 stream 恢復 producer | 1 |
| 正式 go2rtc binary | 未替換 |

清理後對隔離端點作唯讀探測得到 HTTP 404；這只能證明清理後沒有可用的
PTZ stream controller，不能推論攝影機已移動或 PTZ 硬體不支援。正式環境
仍應把 PTZ 顯示為 experimental / not verified，直到同一個活動 producer
上取得狀態成功並由使用者明確按下一次方向按鈕。

## 實驗 binary

隔離 binary 為既有 `go2rtc 1.9.14+dev` 實驗版，SHA-256：

`3AAAFB4D2D0504FEBE7E1BF2A10BF002806F9F86F5D33FF263A3C68934070E46`

此 hash 只用於本次診斷，沒有寫入正式 vendor manifest。
