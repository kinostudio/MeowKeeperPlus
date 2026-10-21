# 小米 MJSXJ03HL／BHR5255GL 韌體選項（規劃）

文件中的型號與代號只是待確認線索，不代表所有「小米智慧攝影機標準版 2K」都相同。正式
操作前必須核對機身標籤、區域、原廠韌體、SoC、感光元件與目前 Mi Home 行為。

## A. go2rtc 免刷機橋接（優先）

保留原廠韌體，使用官方 go2rtc Xiaomi source，建立只綁 localhost 的本機 restream，再由
MeowKeeper+ 單一 ingest 使用。這是 MVP 後優先實機驗證路線，仍需帳號、型號與畫質實測。

## B. Thingino 替代韌體（Post-MVP Experimental）

本階段不格式化 SD 卡、不寫入韌體、不執行未知腳本、不自動下載第三方 shell script，也不把
刷機流程放進正式 installer。未來若建立「製作小米攝影機解鎖 SD 卡」精靈，必須先完成：

- 精確型號／SoC／感光元件／韌體與固定 release SHA-256。
- FAT32／MBR、原廠韌體與 SD 卡資料備份、可恢復流程。
- 斷電、變磚、Mi Home 功能遺失與多次確認風險提示。

不得使用「一鍵破解」「一鍵刷機」或「保證解鎖」等不實名稱。

## C／D. 備份與恢復

原廠韌體備份與恢復必須是使用者明確選擇、可驗證檔案與可中止流程；目前只有 TODO，未
實作磁碟寫入或 SD 卡操作。

## E. Tailscale

正式推薦是小米／Thingino 在區域網路輸出串流，由 Windows 官方 Tailscale Client 提供
遠端 Dashboard。攝影機內直接執行 Tailscale 仍屬 Post-MVP，需先驗證 CPU、RAM、TUN、
24H 溫度、斷線重連、Node Key 保存與韌體恢復，不得宣稱所有機型支援。
