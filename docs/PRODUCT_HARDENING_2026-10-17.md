# 產品化補強：文件、故障閉環與身分衝突

日期：2026-10-17。範圍是repository研發；未建置、簽署、上傳、安裝、UAC或重啟正式Host。不是所有產品缺口已清零。

## 已修改

- README：由「規劃骨架」改為目前已有實作與experimental界線，加入工程閉環入口；最新候選包仍為既有r105，不冒稱新程式已部署。
- AGENTS：加入最新規格／當前目標與故障台帳路由；尊重CPU／Intel／NVIDIA偏好及實際後端，不再把640基線當高解析度硬上限；要求已解決故障有回歸與發布阻擋，不以堆疊文字代替防退化。
- `configs/release-regressions.json`：9類故障／17項可執行回歸。
- `tools/verify_regression_gate.py`：每次重新跑、skip／預期失敗／逾時／缺測拒絕；程式與台帳指紋；不原樣列出stderr／私人內容。
- `tools/build_portable.py`：正式建置強制門檻、複製後再核對程式指紋；包內證據與manifest綁定；不在失敗時替換已有輸出。
- `tools/prepare_release.py`：簽署／ZIP前核對本版證據，拒絕缺漏／過期／manifest不一致。不是全自動更新，也不是數位簽章驗證的替代。
- 安裝器：新增階段分類與Data/install-history安全JSON摘要；已存在Data後盡力保存，資料尚未建立／UAC前早期失敗可能無摘要；無自動上傳。
- V0.2身分：同時活動軌跡指向同一個體時，雙方改為unknown、清除既有投票，並向既有儲存路徑追加unknown證據、更新候選投影；原始immutable推論與歷史不改寫。不宣稱已解決所有tracker swap或家庭準確率。

## 驗收與可追溯證據

- 第一輪全量：795項／232.189秒／OK，`reports/product-hardening-2026-10-17-unittest.txt`。其後新增雙軌跡與門檻補強，因此此數字不能代替最後一次結果。
- 定向：門檻／建置／安裝／身分共44項通過；其中實際執行Windows PowerShell解析及抽取函式，在臨時目錄產生兩筆安全摘要，拒絕任意phase；沒有真正執行安裝器或終止使用者程序。
- 強制門檻實跑及最終完整回歸見本文件下方最終結果；不以先前r105的788項代替本輪。
- 機制測試覆蓋：故障台帳無效、測試失敗／skip／逾時、格式損壞、零測試假綠燈、原始碼變更、包manifest證據不符、舊輸出保留；雙軌跡衝突涵蓋雙方撤回、inactive peer與舊來源拒絕。雙軌跡儲存呼叫使用替身；不把它算真實Telegram／家庭SQLite驗收。

## 尚未完成：不得以本輪關閉

1. PTZ新HUD真實馬達／座標、包內／正式版驗收。
2. 身分回饋裁切provenance縮圖、特徵版本回退／校準啟用、家庭同框／交會holdout。
3. WebRTC與按需單一共享H.264備援。
4. App／Data／排程完整交易式回退、安裝結果Dashboard檢視與容量政策；現在只有局部App回復與本機摘要。
5. 跨Windows／CPU／GPU／手機／電視與24H／7天實機；真實Telegram命令與逐軌跡回報落庫、雲端服務帳戶額度。

上述是各自有範圍與驗收的後續階段，不把「已規劃」當作「已實作」。本輪沒有外部Key／家庭資料外送、修改防火牆或系統驅動；後續正式升級仍由使用者自行執行代碼。

## 最終結果

- 完整回歸：**798項／216.819秒／OK**，exit 0；輸出 `reports/product-hardening-2026-10-17-final-unittest.txt`。
- 故障台帳強制門檻：**9類／17項／passed，failed與skipped皆0**；輸出 `reports/product-hardening-2026-10-17-regression-gate.json`。完成後再次確認source指紋與目前程式一致。
- 全範圍 `ruff check src tools tests`、`compileall -q src tools tests`：passed。
- 第一方src/tools/scripts/configs/docs共436個文字檔的GitHub與Telegram Token樣式掃描：0命中文件；這不是任意秘密／圖片／二進位或發布包完整掃描的保證。
- 8份主要文件本機連結無缺失；本次程式差異空白檢查passed。
- not_checked：本輪未建立新包，未執行正式系統安裝、真實PTZ／Telegram、雲端Provider或家庭／跨機長時間驗收。既有r105不包含本輪程式修改。
