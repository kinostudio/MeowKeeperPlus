# r106 發布紀錄（2026-10-17）

本版彙整 repository 累積修改，包含 MSE／H.264 WebRTC共享預覽、三模式、PTZ位置HUD、來源尺寸記憶、工程回歸門檻及 CPU 溫度觀測。身分／行為仍為 experimental。既有權限不足時如實顯示不可用，不自動安裝驅動或提升Host權限。

- CPU：附經校驗的 CPU-only helper；需相容 .NET Framework 4.7.2+、已授權安裝的 PawnIO 與感測驅動存取權限。三分鐘管理員隔離驗收通過；不是每台電腦保證可讀。
- MSE：修正版真實攝影機1920×1080約20 FPS，雙觀看頁單producer與三模式隔離通過。HEVC WebRTC及共享H.264即時轉碼未完成；格式不支援仍可能回退JPEG。
- 升級：由使用者自行執行Install-MeowKeeperPlus.cmd -Upgrade；本輪不安裝、不觸發UAC、不重啟正式Host。
- 私人data、資料庫、照片／影片、帳號Session、憑證、簽章私鑰與本機測試reports不隨包或公開repository上傳。

建置與發布完成結果在本機reports保存，未完成前不將候選包視為已部署。
