# 喵喵守護者+ / MeowKeeper+

**MeowKeeper+ — Local AI Pet Guardian**

Windows 本地寵物監控：共用攝影機影音來源、事件影片、Telegram 通知、逐軌跡人工回報與選用的貓咪身分／行為候選。

## 使用與下載

- [官方發布版本與安裝說明](https://github.com/kinostudio/MeowKeeperPlus/releases)
- [一般使用指南](docs/PUBLIC_USER_GUIDE.md)
- Windows 發布包內附 Python、FFmpeg 與必要模型；一般使用者不必建立 venv 或執行 pip。
- 安裝與更新由使用者執行發布包中的安裝入口；Git 原始碼更新不會自動升級正式 Host。

## 原始碼協作

本 repository 保存公開原始碼與一般說明。Codex／Argon 使用獨立 Git clone 與工作分支，以完整 commit SHA 交接；合併前檢查測試與公開內容。

- [協作與交接](docs/ARGON_HANDOFF_SYNC.md)
- [測試方式與驗收界線](docs/TESTING.md)
- [目前目標](docs/CURRENT_GOALS.md)
- [原始碼整合紀錄](docs/SOURCE_INTEGRATION.md)

身分與行為辨識仍為 experimental。硬體效能、CPU 測溫、PTZ 及連續播放須依目標設備驗收；單元測試不能取代家庭實拍或長時間測試。

私人設定、金鑰、資料庫、攝影機畫面、實機量測與操作紀錄只保存在使用者本機。安全管理見 [安全規範](docs/SECURITY.md)。
