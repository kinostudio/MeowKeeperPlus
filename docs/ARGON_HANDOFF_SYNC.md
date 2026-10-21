# Codex／Argon 原始碼協作

共同來源為 https://github.com/kinostudio/MeowKeeperPlus 。每個協作者使用獨立 Git clone；開始前檢查執行身分、GitHub 帳號、remote、工作樹及最新遠端提交，保留他人未提交修改。

## 一般流程

1. git fetch origin，從已確認的共同 commit 建立工作分支。
2. 檢查逐檔差異並執行相關測試；回報分支、完整 commit SHA 與限制。
3. 來源、測試與一般說明經審查後推送工作分支，再依授權合併。
4. Git push 不代表建置、發布或安裝。

## 固定內容交接包

在乾淨 clone 中提交完成後執行：

    python tools/source_handoff.py --base <共同基準SHA> --head <完成SHA> --output data/source-handoff

交接包包含40個指定來源／測試檔案、SHA-256 manifest、基準到完成提交的 changes.patch，以及完整40檔的 snapshot.patch。前者只適用於指定基準；後者只能套到空目錄。

manifest 列出兩個完整 commit SHA；檔案只取自 Git 物件，不讀取未提交工作區。輸出預設留在被忽略的 Data，別將私人測試輸出加入 Git。

## 舊式來源快照

tools/sync_to_argon.py 只供尚未使用 Git 的隔離交接目錄。需明確提供 --remote、--desktop、--destination 與既有 --identity；主機、帳號、SSH 私鑰及桌面位址不寫死在公開程式。目的地含 .git 時拒絕操作。

快照排除秘密、Data、模型、runtime、vendor 及大型二進位，只傳變更的允許檔案與 Git 狀態快照。它不建立 Git 歷史、不操作正式 Host，也不代替 Git 分支協作。
