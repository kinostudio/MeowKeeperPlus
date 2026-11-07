# 喵喵守護者+ / MeowKeeper+

**MeowKeeper+ — Local AI Pet Guardian**

Windows 本地寵物監控：共用攝影機影音來源、事件影片、Telegram 通知、逐軌跡人工回報與選用的貓咪身分／行為候選。

## 使用與下載

- [官方發布版本與安裝說明](https://github.com/kinostudio/MeowKeeperPlus/releases)
- [一般使用指南](docs/PUBLIC_USER_GUIDE.md)
- Windows 發布包內附 Python、FFmpeg 與必要模型；一般使用者不必建立 venv 或執行 pip。
- 安裝與更新由使用者執行發布包中的安裝入口；Git 原始碼更新不會自動升級正式 Host。
- [r115 候選版與分階段驗收](docs/R115_CANDIDATE.md)：候選建置不等同正式部署或實機更新／回滾已通過。

## 原始碼協作

本 repository 保存公開原始碼與一般說明。Codex／Argon 使用獨立 Git clone 與工作分支，以完整 commit SHA 交接；合併前檢查測試與公開內容。

- [協作與交接](docs/ARGON_HANDOFF_SYNC.md)
- [測試方式與驗收界線](docs/TESTING.md)
- [目前目標](docs/CURRENT_GOALS.md)
- [原始碼整合紀錄](docs/SOURCE_INTEGRATION.md)

身分與行為辨識仍為 experimental。硬體效能、CPU 測溫、PTZ 及連續播放須依目標設備驗收；單元測試不能取代家庭實拍或長時間測試。

私人設定、金鑰、資料庫、攝影機畫面、實機量測與操作紀錄只保存在使用者本機。安全管理見 [安全規範](docs/SECURITY.md)。
# 安全更新整合候選

已發布 [r119 累積差量 hotfix 候選](https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r119)：包含 r118 修正及 Telegram 接收器衝突後自動退避恢復，r116 可直接更新。差量 ZIP 約 1.37 MB，不重新下載未變 runtime／CUDA／FFmpeg／模型。完整測試 1,086 項、發布回歸 185 項、實際包重建、原 r116 runtime 準備與包內 HTTP 啟動通過；公開資產下載／簽章／雜湊再驗通過。正式換版／回滾仍待使用者實機驗收。[適用基準與更新命令](docs/DELTA_HOTFIX.md#r119-更新命令)。

r118 新增真正的簽章檔案差量：未變 runtime／CUDA／FFmpeg／模型不下載，精確基準不符即停止、不自動改抓完整包。[差量更新契約與驗收邊界](docs/DELTA_HOTFIX.md)。套用仍會短暫中斷；正式 Host 升級由使用者執行。

已發布 [r117 候選版](https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r117)。整合與驗收邊界見 [r117 安全更新](docs/R117_SAFE_HOTFIX.md)：包含三秒拼貼與雲端說明修正，更新採官方簽章及資料快照，不直接覆蓋 Git 原始碼。正式 SYSTEM 更新交易仍待使用者安裝後驗收，不能將隔離測試等同正式部署成功。
