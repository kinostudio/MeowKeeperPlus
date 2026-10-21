# C701 遠端同區網隔離取流測試

用途：在 **C701 所在住處的 Windows 電腦**上執行官方 go2rtc v1.9.14，驗證真實影格，並產生短效配對碼供另一台 MeowKeeper+ Host 接入。它不會修改正式 Host，也不會取代正式 go2rtc。

## 操作

1. 將整個測試包複製到遠端 Windows 電腦；確認該電腦已連上 Tailscale，且可連到 C701 的當地 LAN。
2. 雙擊 `scripts/Start-C701Relay.cmd`。它會核對官方 go2rtc SHA-256，將 API 綁定 `127.0.0.1:21984`、RTSP 僅綁定該電腦的 Tailscale IPv4 `:28554`，並開啟本機 go2rtc 頁面。
3. 在**遠端電腦本機**的 `Add → Xiaomi` 親自登入。不需把帳號、密碼、Token、DID 或完整來源 URL 傳給 Codex。
4. 登入後雙擊 `scripts/Test-C701Relay.cmd`。它只測 SG 清單中的 `chuangmi.camera.079ae2`、`subtype=1` 與一個 FFmpeg consumer。成功才保留單一 `c701_sd` stream；失敗時自動清除。
5. 測試報告與私人 JPEG 位於 `%LOCALAPPDATA%\MeowKeeperPlusC701Relay`。**不要把 config.yaml 或 JPEG 傳到聊天或提交 Git**；只回覆是否成功與報告中的非秘密欄位。
6. 在攝影機旁電腦雙擊 `scripts/Export-C701Pairing.cmd`。只有同一輪 SG 裝置清單中的 C701、唯一 producer 與真實 JPEG 都核對成功，才會輸出 10 分鐘有效的 `MKP1.` 配對碼。不要傳送登入憑證、設定檔或 JPEG。
7. 在正式 Host 的「小米攝影機」→ C701 卡片 →「遠端中繼配對」貼入配對碼。Host 會比對同一台裝置並從 Tailscale RTSP 再取得一張影格；通過後按「接入遠端監控」。之後來源設定會記住中繼位址，Host 下次啟動會嘗試恢復；Tailscale IP 改變時必須重新配對。
8. 若要讓遠端電腦**使用者登入 Windows 後**自動啟動，可在該電腦以一般使用者 PowerShell 執行 `scripts/Manage-C701RelayStartup.ps1 -Mode Install`。`-Mode Status` 查詢，`-Mode Remove` 只移除本包建立的啟動捷徑。它不需要 UAC，但**不保證未登入前持續監控**；後者需另行驗收服務帳號下的小米 Session、Tailscale 與防火牆規則。
9. 暫不需要串流時雙擊 `scripts/Stop-C701Relay.cmd`。它只會停止由本包啟動、且執行檔路徑核對相符的 PID，並讓登入後監看程序退出；下次登入會再次啟動。

預設不建立 Windows 自動啟動或防火牆規則。RTSP 綁在 Tailscale 位址但尚未設定 RTSP 密碼；僅可在可信任的 Tailnet 使用，並限制 Tailnet 成員權限。即使已配對成功，也尚需在遠端電腦完成重新登入、重新開機與長時間穩定性驗收。

## 遠端畫質與方向控制（新版候選）

新版配對包另附最小 Python runtime 與受限控制程序；不用另外安裝 Python。`Export-C701Pairing.ps1` 在確認真實影格、唯一 C701 producer 與同一裝置 DID 後，會把一次性控制密鑰以目前 Windows 使用者的 DPAPI 保存，並啟動控制程序。控制程序只綁 Tailscale IPv4 的 TCP 28555；go2rtc API 繼續只綁 `127.0.0.1:21984`。配對碼包含短效密鑰，請只貼到登入後的正式 Dashboard，不要傳到聊天或日誌。重發配對碼會輪替密鑰，正式 Host 需重新配對。

控制埠預設**不建立防火牆規則**。取得使用者同意後，在攝影機所在地電腦的管理員 PowerShell 執行 `scripts/Set-C701ControlFirewall.ps1 -Mode Install -HostTailnetIp <正式 Host 的 Tailscale IPv4>`。規則只允許指定 Host IP 連到此包的 `runtime/python.exe`、本機 Tailscale IP 的 TCP 28555；不開放 go2rtc API、WAN 或所有程式。`-Mode Status` 只讀，`-Mode Remove` 只移除本包識別碼與程式／位址／埠皆相符的規則。正式 Host 必須能連到 TCP 28555，否則**遠端影音仍可觀看，但畫質與方向按鈕保持不可用**。

畫質命令僅接受 `subtype=1/2/3/auto`，以同一輪 SG 裝置清單及配對指紋核對 C701，替換**同名單一 stream**，取得新 JPEG 才回報驗證成功；失敗會嘗試回退。官方 go2rtc v1.9.14 沒有 Xiaomi MISS PTZ API，所以即使遠端畫面和畫質接通，方向鍵仍會顯示未支援。只有經隔離實測的實驗版 PTZ 端點實際可讀且回應，方向命令才會被允許；指令回應不等於實體鏡頭位移已驗證。不得替換正式 go2rtc.exe 來宣稱 PTZ 已完成。

## 2026-10-04 實測結果與下一步

遠端 Windows 為 Public 網路、Windows 防火牆啟用且沒有此官方 go2rtc 的 UDP 入站規則時，C701 開流在 UDP 回應階段逾時。使用 `Probe-C701UdpFirewall.ps1 -Mode TemporaryRule` 建立**僅限該 go2rtc.exe、C701 IP、遠端 LAN 本機 IP 與 Public 網路**的臨時規則後，單一 `c701_sd` 成功取得真實 JPEG（848×480、38,959 bytes）；腳本確認臨時規則已移除。詳細證據與不應重犯的診斷分岔見 `docs/XIAOMI_MIOT_REGION_SOP.md`。

後續已在授權的遠端電腦以限縮規則取得 C701 JPEG；正式 Host 也曾經由 Tailscale RTSP 取得影格及短片。本配對包的**新流程**仍需在該遠端電腦實測；先前短片不構成新配對碼、開機恢復或 24H 驗收證據。

使用者同意後，可在 C701 同區網的 Windows 電腦、**已提升權限**的 PowerShell 執行 `Set-C701UdpFirewall.ps1 -Mode Install`。腳本先驗證 21984 唯一 owner、官方 go2rtc SHA-256、隔離設定、同區網路由與 Public profile；只建立該執行檔、C701 `192.168.18.62`、當前 LAN 本機 IP 的 UDP 入站規則。已存在且完全相符時可重跑；同名但內容不同時會拒絕覆蓋。新規則建立後做一次 JPEG 驗證，失敗則移除新規則。`-Mode Status` 唯讀；`-Mode Remove` 僅移除完全相符的同名規則。LAN IP 或網路 profile 改變後需重新審核，勿放寬為所有裝置或所有程式。這不會開放 go2rtc API 到 LAN 或 WAN，也不代表遠端 RTSP／24H 已驗收。
