# 位置讀回後端發布（2026-10-10）

本包選用獨立 `vendor/go2rtc/ptz-feedback/go2rtc.exe`，保留官方 `vendor/go2rtc/v1.9.14/go2rtc.exe`。不是將 fork 偽裝成官方版本，也不覆寫 Xiaomi Session。

## 已驗證與尚待驗證

- 026c02 真實隔離測試：位置讀回成功，angle=75、elevation=29、ret=0。完整證據見 `reports/ptz-026c02/position-feedback-fix-20261010.json`（開發工作區，不放入發布包）。
- 本次選用 SHA-256 `3F64D4C5144350DC816ABB4DE41BD686E248A9528EB2825A00CD8FC65EFEA0F3`，啟動前必須校驗；失敗時不下載、不偷偷切換版本。
- 發布包的 `vendor/go2rtc/backend.json` 明確選用 `ptz-feedback`，Host 會自動啟用已登入來源的 PTZ API。
- 位置數值是裝置內部座標，不代表角度度數。讀位置不移動鏡頭。
- C701、81ac1、其他攝影機的位置讀回未經本次實機驗收；不能以 026c02 成功宣稱全部成功。
- 絕對移動、隱私位置往返未驗收；前端仍須依能力與有效位置讀回限制操作。
- 正式 Dashboard 端到端仍須使用者安裝後驗證；建置成功不等於正式 Host 已升級。

## 防火牆

SYSTEM 安裝時呼叫 `Sync-PtzUdpFirewall.ps1`，對所有既有 MeowKeeper+ 官方 go2rtc 逐攝影機 UDP 入站允許規則，建立獨立 PTZ 執行檔規則。需符合完整官方執行檔路徑、MeowKeeper 名稱、啟用、非 Any 網路設定檔、私人 IPv4 單一位址、UDP、Inbound、Allow；保留原有 Profile、位址、Port、網卡範圍。不新增全 LAN 規則，不修改原規則。

沒有可驗證規則時只提示，不自行放寬。遠端 C701 規則須在攝影機所在地中繼電腦配置，不能用本機规则代替。無管理員權限的規則操作必須安全失敗。本輪工具無管理員權限，因此真實規則遷移及解除安裝仍待使用者執行安裝後驗收。

後續升級停止程序時涵蓋官方及 PTZ 兩個精確路徑，仍驗證命令列、建立時間與 PID；不按名稱停止其他專案。解除安裝只移除符合 `MeowKeeperPlus.PtzUdp.v1` 且完整規則身分驗證的自建規則。

## 回退

由使用者安裝保留官方後端的上一個發布包（r75），沿用同一資料與 Xiaomi Session。不得為回退清空設定或重新登入。不要在已驗證的發布包內直接改寫 binary 或 manifest。
