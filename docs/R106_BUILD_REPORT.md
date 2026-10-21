# r106 建置與發布驗證（2026-10-17）

本文件是建包後的本機補記，不改寫已完成的包內manifest。正式Host未安裝或重啟。

## 本機結果

- 完整unittest：830項／229.829秒／OK（`reports/r106-unittest.txt`）。
- Ruff、compileall：passed。
- 第一方文字秘密樣式掃描：建置前458檔、命中0；檢查Token、私鑰及完整數字小米帳號URI，不等於所有秘密或二進位掃描。
- 強制故障回歸：12類24項，passed、無失敗或略過；本版程式指紋綁在包內證據。
- 包內Python驗證21,473個manifest檔案，private_data_present=false。
- 包內CPU helper雜湊通過，websockets15.0.1與aiortc1.15.0實際匯入通過。
- 包內實際app／HTML／JS／內附Python的Edge驗收：頁尾r106，真實MSE1920×1080約20 FPS，currentTime13.858519→39.032493，readyState4、paused=false。節能JPEG1920×1080；休眠清空src並停止播放；恢復MSE至40.172354秒、20.0 FPS。
- 關閉測試後：既有來源producer1、consumer1；正式healthz200。未新增攝影機ingest，未操控鏡頭。
- 完整ZIP含21,895個項目（含執行測試衍生bytecode）；禁用路徑檢查命中0，無reports、資料庫、私鑰或.env。
- 簽章由既有信任根產生並以公開金鑰驗證通過，未更換信任根。

## 交付

- 目錄：`dist/MeowKeeperPlus-portable-2026-10-17-r106`
- ZIP：`dist/MeowKeeperPlus-r106-windows-x64.zip`
- 大小：2,413,952,438 bytes
- SHA-256：`f514e5fc02ca76ee1e795e1373543457d7c134280f8674559998dc5facb4d745`
- 分卷：1,073,741,824／1,073,741,824／266,468,790 bytes。
- 下載腳本：`dist/Download-MeowKeeperPlus-r106.ps1`；Windows PowerShell parser passed，UTF-8 BOM。
- 下載腳本SHA-256：`58a0ac80e08aa1152acde66f1b36f69e9a4c03f725c47e788d65bcb8a16f729d`。
- 使用者自行執行 `Install-MeowKeeperPlus.cmd -Upgrade`；不將發布成功等同已安裝。

## GitHub

執行身分aero15\aaron；keyring授權可用；`gh api user`確認kinostudio。公開repository原本為發行區、只有README，沒有將本機Git歷史或私人工作樹推上公開庫。

已公開發布[r106](https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r106)，三個分卷、簽章清單與下載腳本共五個遠端資產大小／SHA-256全部一致。未登入下載簽章清單與下載腳本再次驗證通過；Release isDraft=false、latest=r106。

公開首頁README已透過GitHub API更新，提交`093610a5b698b8efb37c176c7eed824c762a0fbb`。本機Git工作樹與歷史未推送，只有乾淨發行資產及公開README。發布回條保存於`reports/r106-publish-receipt.json`；沒有安裝或重啟正式Host。

## 保留限制

CPU持續實溫已管理員隔離三分鐘驗收，不是一般權限可讀、24H或所有硬體保證。MSE是同一部真實攝影機的包內隔離驗收；正式Host仍由使用者自行更新後再驗。身分／行為仍為experimental，HEVC WebRTC與共享即時H.264轉碼未完成。
