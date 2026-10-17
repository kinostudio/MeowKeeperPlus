# 喵喵守護者+ / MeowKeeper+

**MeowKeeper+ — Local AI Pet Guardian**

Windows本地寵物AI監控套件：共享攝影機串流、事件影片、Telegram通知、逐軌跡回饋與選用寵物身分辨識。

## 最新下載

[r106 發布說明與下載](https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r106)

- 修正MSE流暢預覽的發布依賴；加入CPU溫度觀測與工程故障回歸保護。
- 提供完整Windows可攜包（內附Python、FFmpeg、模型），不要求終端使用者執行pip。
- 大型ZIP使用分卷資產；請依Release提供的下載腳本完成合併與SHA-256校驗，勿單獨解壓part檔案。
- 可攜開啟 `MeowKeeperPlus.cmd`；正式安裝／升級由使用者自行以管理員執行 `Install-MeowKeeperPlus.cmd -Upgrade`。

## 狀態與界線

身分與行為辨識仍為experimental。CPU感測、PTZ、連續播放取決於硬體、攝影機、權限與瀏覽器；未支援或無可信資料時會顯示原因，不能將本機短測視為所有設備或24H保證。

本repository為公開程式包發行區，不包含使用者私人設定、資料庫、帳密、攝影機畫面或Telegram憑證。更新清單有Ed25519簽章；請只使用此repository的官方Release。

[所有版本](https://github.com/kinostudio/MeowKeeperPlus/releases)
