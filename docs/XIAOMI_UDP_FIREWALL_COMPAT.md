# Xiaomi UDP 防火牆相容性

小米影音取流與 Dashboard TCP 存取屬於不同連線；Dashboard 網頁可開啟不代表攝影機 UDP 取流正常。

使用公開腳本 scripts/Manage-XiaomiUdpFirewall.ps1 進行診斷。先唯讀確認實際 go2rtc 執行檔、官方完整性、攝影機路由、網路 profile 及既有允許／封鎖規則。需要變更時明確顯示目標程式與攝影機位址，經使用者核准及 Windows UAC 後建立限縮規則。

不得自動關閉防火牆或放行所有程式／所有來源。已存在的衝突規則須列出並由使用者確認，不能無聲修改其他軟體規則。

分開記錄「規則已建立」「單張影格通過」「持續取流通過」。本機診斷輸出只保留在 Data，不推送到公開 repository。
