# Xiaomi 攝影機 UDP 防火牆相容性

## 下一版必要修正：r85 Public UDP 衝突（2026-10-11）

狀態：已納入 r86 原始碼，發布與遠端實機結果見 `R86_XIAOMI_CONTROL_FIX.md`；未取得遠端修復後證據前不宣稱實機通過。

使用者回傳的實機證據：C701 所在筆電使用實體 Wi-Fi、Public profile，API／RTSP 為同一 go2rtc 程序；執行檔為可攜包 `vendor/go2rtc/ptz-feedback/go2rtc.exe`。該執行檔有 Private UDP Allow 與 Public UDP Block。Private Allow 不適用目前網路，明確 Block 又優先於 Allow；因此不能只新增 Public Allow 就宣稱修復。尚未收到本輪修復後 JPEG 或持續串流證據。

下一版發布 gate：

- 從實際 API port owner 確認 binary；官方與已核准 PTZ 實驗版各以自己的 manifest 校驗，不可固定只驗證官方路徑，不接受任意第三方 binary。
- 診斷目前出口網卡、profile、實際執行檔的有效 UDP Allow／Block；辨別「僅其他 profile 有 Allow」、「目前 profile 有 Block」及無規則。
- 預設唯讀；更新／安裝不得擅自停用 Windows 或使用者既有 Block。另行明確同意指定規則與影響，再由正式 UAC 授權修復。
- 修復前保存指定 Block 原始啟用狀態與必要 filter；重新核對 PID、路徑、hash、profile、規則身分，狀態已變則停止。先建立並驗證限縮 Allow，再停用明確授權的 Block；失敗復原本次變更。
- Allow 限定執行檔、攝影機 IP、實體 LAN 目的 IP 與實際 profile；不得停用整體防火牆、開放所有程式或額外暴露 API。跨版本路徑改變必須重新核對。
- 修復重跑須冪等；完全相符的自有規則已存在則沿用，衝突停止，不覆蓋其他規則。
- JPEG 與持續 RTSP 分開驗證；測試 Public Block、Private-only Allow、PTZ manifest、取消 UAC、無管理員權限、復原、重跑與跨版本路徑。實機未通過保持 `not_checked`。
- 提供復原入口。解除安裝只移除身分已驗證的自有 Allow；原 Block 是否恢復依保存的原始狀態與使用者決定，不得擅自建立新封鎖。

已修正 `Manage-XiaomiUdpFirewall.ps1` 的官方／核准 PTZ 選擇與 Block 診斷。Wizard 另外要求「同意停用」才傳入指定 Block 清單；安裝器不會自動停用既有規則。候選 Block 可能有額外位址／埠範圍，不將候選數量當成封包阻擋實證。手動修復片段不能原樣硬編碼到產品。

復原：在當時執行修復的帳號下，以管理員 PowerShell 執行 `scripts/Manage-XiaomiUdpFirewall.ps1 -Mode RestoreBlocks -ConfirmRule -BackupPath <報告的 block_backup_path>`。只重新啟用備份中、仍符合原 filter 的指定 Block；不移除或更動其他規則，既有產品 Allow 可另以 Remove 移除。

本流程適用於 Windows 10/11 上，米家登入與裝置清單正常、go2rtc 已啟動，但攝影機沒有影格的情況。**沒有影格不等於防火牆造成**；先分清帳號、來源、UDP 往返及解碼問題。攝影機若在另一住處，請於**攝影機同區網的 Windows 中繼電腦**執行，而不是只在經 Tailscale 看畫面的電腦執行。

## 使用者操作

1. Dashboard「小米攝影機設定」先完成登入、載入裝置及一次「測試畫質」。失敗時按同一裝置的「檢查連線／防火牆」；這只讀取狀態，不會改規則。
2. 在攝影機所在的 Windows 電腦開啟安裝目錄的 `scripts/Xiaomi-Firewall-Wizard.cmd`。輸入攝影機的區網 IPv4（可在路由器裝置清單查看）及已建立的 go2rtc 串流名稱；不知道串流名稱可留空，此時只做網路與規則診斷，**不能聲稱影格已驗證**。
3. 只有看到提示並輸入完整「同意」，工具才嘗試建立規則；若尚未提升權限，Windows 正式 UAC 會由使用者核准。取消 UAC 不改規則。不要輸入小米帳號、密碼、Token 或完整 `xiaomi://` 位址。
4. 報告的 `single_frame=passed` 只表示一張有效 JPEG；`sustained_stream=passed` 才表示指定秒數的 RTSP/FFmpeg 取流完成。兩者都未通過時，不宣稱修復成功。

## 規則範圍與安全

規則只允許已由官方 manifest SHA-256 驗證、且實際持有 localhost go2rtc API port 的 `go2rtc.exe` 收到**指定攝影機私人 IPv4 → 目前實體 LAN IPv4** 的 UDP。CS2 使用動態 UDP 連接埠，因此無法預先固定單一埠；使用程式、來源 IP、目的 IP 與目前 Public／Private／Domain 網路設定檔共同限縮。腳本不停止 Windows 防火牆，不開放所有程式或所有攝影機，也不開放 go2rtc API 或 RTSP 到公網。若出口是 Tailscale/VPN/虛擬網卡，拒絕在觀看端建立規則。

規則名稱由執行檔路徑與攝影機 IP 決定，群組／描述含 `MeowKeeperPlus.XiaomiUdp.v1` 識別。重跑時只沿用範圍完全一致的規則；若同名規則被改動，拒絕覆蓋。DHCP、go2rtc 安裝路徑或網路設定檔改變後須重新診斷，不自動放寬規則。

## 技術入口

`scripts/Manage-XiaomiUdpFirewall.ps1` 提供 `-Mode Diagnose|Install|Remove|RemoveAllOwned`。`Install` 另需 `-ConfirmRule`，`Remove` 需攝影機 IP；`RemoveAllOwned` 僅供解除安裝流程使用，先核對全部規則的程式路徑、SHA-256、群組、描述及 filter，識別不符時不移除任何規則。安裝後的卸載器先嘗試移除本產品擁有的規則，成功才刪除 App，資料仍依原卸載選項保留。

診斷只輸出安全摘要：官方 binary／PID 是否匹配、路由類型、Windows profile、規則是否精確、HTTP 狀態、錯誤分類、JPEG 狀態及持續影格數；不輸出完整 Xiaomi URL、帳密、DID、Cookie 或原始回應。若未有已登錄 stream，`stream_status=not_registered`，不從診斷腳本額外建立第二個 Xiaomi producer；請先由設定精靈既有的單一來源畫質測試建立／測試 stream。診斷不自動修復。

## 驗收界線

已驗證的 C701 個案是 Public profile 下臨時規則前 UDP timeout、加入限縮規則後取得真實 JPEG，並經 Tailscale RTSP 取得一張解碼影格；它**不代表所有陌生電腦或機型已通過**。新品／新版仍需分別驗證：Public、既有精確規則、無管理員權限及取消 UAC、取流至少 10 分鐘、重新開機、解除安裝後規則移除與其他規則保持不變。每項未實測前標記 `not_checked`，不得把單張影格寫成 24H 穩定。

| 驗收項目 | 目前狀態 |
| --- | --- |
| 新腳本語法、精確規則的模擬驗證及自動測試 | `passed` |
| C701 在 Public profile 加入限縮 UDP 規則後取得單張 JPEG | `passed`（先前實機個案，非新通用腳本） |
| 新通用腳本在陌生電腦上診斷、UAC 建立、既有規則重跑 | `not_checked` |
| 持續 RTSP 至少 10 分鐘、重新開機後仍正常 | `not_checked` |
| 實機解除安裝只移除本產品規則 | `not_checked` |
