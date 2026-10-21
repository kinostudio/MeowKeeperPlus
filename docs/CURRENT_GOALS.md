# 目前進度與目標（2026-10-21）

## CPU 測溫圖形啟用入口

- 首頁 CPU 溫度缺值時顯示「啟用／檢查測溫」，導向趨勢頁的說明與授權按鈕。GET／首次開頁不啟動安裝；只有本機已登入、CSRF 與確認欄位皆通過時，才啟動已核對發布 manifest 的固定工具，拒絕遠端與偽造轉送標頭。
- 安裝視窗、UAC、官方授權條款與受限版選擇仍由使用者確認；受限 SYSTEM 測溫工作與 Host 帳號分離。工具結束僅標為完成流程，需真實感測讀值才能驗收。
- 隔離 Python／瀏覽器驗證涵蓋不自動安裝、取消、單次確認、重複點擊、遠端阻擋、套件竄改及390px布局；實機安裝、目標硬體溫度、候選包與正式 Host：尚未驗收，本次未觸發驅動安裝。

## 硬體 Hero 環形倒數回歸

- 新版 `monitor-ui.js` 另建硬體 Hero 時只放採樣時間，舊倒數留在原版區塊。已改為搬移既有唯一 `hardware-sample-clock` 到新版硬體標題右上角，沿用原有五秒狀態更新，不新增輪詢或感測程序。
- 路由／靜態接線回歸及隔離 Edge 瀏覽器通過；涵蓋真實 Hero builder 與動畫模組順序、圓環隨時間變化、390px 無水平溢出、過期狀態。`DASHBOARD-LIVE-HARDWARE-MOTION` 已補強。尚未建置、安裝或驗收正式 Host 更新後畫面。

## 2026-10-21｜管理中心 3A／3B 保存缺口

- 已重現並修正切換服務／模型時同次提交的額度品質被忽略：後端先載入目標模型設定，再套用明確提交的值；前端只在切換時省略未修改的舊模型欄位，保留模型間設定隔離。
- 停用狀態可保存網址或模型未填完整的草稿，仍驗證已填欄位的安全性；不完整設定禁止啟用與連線檢查。切換離開 Gemini 清除專用自動網址，兩種圖片同意一併取消。
- 59項設定／provider／guard／transport回歸與實際隔離瀏覽器流程通過，覆蓋同次修改、返回模型、草稿保存、啟用拒絕、重新整理與同意重置。故障台帳 `VISION-BOUNDARY-EDITS-DRAFT` 已加入；正式包／正式 Host 尚未更新。

## 2026-10-21｜預覽 HUD 按需顯示

- 首頁與即時頁共用 HUD 顯示控制：桌面滑鼠移入顯示，移開2秒後淡出，重新移入取消倒數。播放資訊、模式／候選開關、方向鍵與畫面內工具列一併處理，偵測框與操作結果提示獨立。
- 鍵盤聚焦保持可見；一般滑鼠點擊留下的焦點不阻止淡出。觸控裝置維持可見操作，尊重減少動態設定，離頁清理計時與事件。隔離瀏覽器驗收及17項預覽／疊圖回歸通過；未建置或安裝正式 Host。

## 管理中心保存接線與事件影片瀏覽器驗收

- 已找到並修正新版 AI 動作分析管理中心漏掛 `vision-settings.js`：載入標籤原先僅存在停用的舊版區塊。現在由新版 partial 掛載一次，保存按鈕回報進度與結果，首次設定說明改為先保存模型 ID，再安全保存 Key 及檢查連線。
- 隔離真實瀏覽器已完成設定保存、重新整理讀回、啟用／停用及無效模型阻擋；77 項相關 Python 回歸通過。新增故障台帳 `VISION-CENTER-SCRIPT-MOUNT`。
- 使用者事件影片經實際 CPU YOLO、預覽 API 與共享播放元件，在隔離 `/live`、`/dashboard` 完成可見框與開關驗收；候選文字移到框外。未確認的身分與動作仍顯示未知，不以合成標籤冒充辨識結果。素材、截圖與瀏覽器報告只留忽略的本機診斷目錄。
- 正式 Host 的功能操作、實際雲端圖片服務、候選包與升級後驗收仍未完成；此次沒有建置或安裝。本節不取代真實攝影機與正式包驗收。

## 即時偵測框事件影片修正

- 已修正正向框被下一筆空 AI 採樣立即覆蓋的時序問題；只在相同來源階段保留原始取樣起算 2 秒，並同步清除過期或換來源的結果。
- 使用使用者事件影片驗證 3840×2160 實際模型框可映射到 960×540 疊圖位置；模型在該片段穩定找到平台上的一隻貓，但床上另一隻仍未達正式信心門檻。詳見 [事件影片驗收](AI_OVERLAY_EVENT_CLIP_ACCEPTANCE_2026-10-20.md)。
- 17 項定向測試、Ruff、compileall、回歸登錄 JSON 通過。正式 Host 即時畫面與發布包：`not_checked`；本次未建置、安裝或重啟。

## 2026-10-20 接續：事件回顧、攝影機規格記憶與播放體驗

- 人工回報片段編輯改成同一條時間軸上的起點／終點雙拖曳把手；事件影片改為彈出式播放器。隔離回歸涵蓋播放權限、原片優先、相容播放及片段編輯。
- 事件播放先直出原檔；需要 H.264 相容版本時，轉碼副本放入有容量與期限上限的本機快取，後續播放命中快取不重做轉碼、不改原片。事件列表將逐列回報查詢改為同頁批次查詢，並新增事件時間索引。結構性效能修正已測；未在 SHRIMP 的實際事件資料量下做頁面載入基準比較。
- 已驗證的攝影機原始解析度、FPS、編碼與時間沿用來源設定記憶；重新啟動後可標為歷史驗證資料，而不是顯示成未知。若來源／設定不符仍不冒充目前實測。
- 流暢播放依瀏覽器能力選路：HEVC 瀏覽器支援 MSE 時先試原始 HEVC；不支援時使用共用 H.264 相容路徑，並快取完成後的相容副本。HEVC 不走 WebRTC。這是程式選路及隔離測試，不等於 C701 實機 HEVC 解碼成功或已量出實際切換速度；仍須在 SHRIMP 的目標瀏覽器驗收播放、延遲、FPS 與解碼相容性。
- 貓咪事件分布改回最近七日 × 每日24時段熱點圖；偵測事件量與 Telegram 送達資料分開統計，避免將偵測數誤當通知數。
- 驗證：全套 `unittest discover` 961 項通過、2 項隔離 vendor 媒體程序測試因未設定明確 opt-in 而略過；全專案 Ruff、`compileall` 通過，另 43 項目標回歸與修改 JavaScript 語法檢查通過。沒有實機 C701 HEVC 驗收、建置、發布或正式 Host 操作。

## 2026-10-20 接續：候選 AI 全管線比較與正式監控恢復

- 前次候選測量器曾在首張來源影格後就開始倒數，未等 AI worker 完成初始化；因此 `run01_cuda_960`／`run03_intel_960` 不構成有效效能比較。現已修正為等待首筆完成推論，再開始固定測量窗；快照讀取 live analyzer／worker，並保留安全的初始化階段與錯誤類別。回歸 `CANDIDATE-AI-FIRST-INFERENCE-DIAGNOSTICS` 已接線。
- 使用同一隔離候選、模型、即時來源及 960 px／5 FPS 上限，完成 CUDA 與 Intel GPU 的有效全管線比較。這次測量未達 5 FPS 設定上限；初步瓶頸在模型執行及追蹤處理，不是排程佇列或 IPC。完整階段數據、啟動時間、遙測可用性與限制只保存在被 Git 忽略的本機診斷報告，不得複製到公開文件或發布包：`data/diagnostics/candidate-pipeline-20261020/`。
- 監控由使用者恢復後，SSH 唯讀確認 Host 為 running、既有攝影機 stream 維持單一 producer，且候選程序數為 0。這是當時狀態，不代表長期穩定性；此次沒有更改正式設定、重啟／安裝正式 Host、建置或發布。
- 本機 10 項 benchmark、8 項 worker、5 項加速推論及 7 項發布回歸測試通過；96 項回歸門檻、Ruff 與 compileall 通過。這些驗證證明量測器與候選執行有效，不代表效能目標已達成，也不代表正式 AI 已優化。下一步若要調整正式效能，先在候選環境針對模型執行／追蹤的實測耗時做一次一因子變更比較，再決定是否保留。

## 2026-10-19 最新接續：GPU 監測、瓶頸覆核與隔離矩陣

> 本節含正式主機的內部驗收摘要，只留本機，不得推送公開 GitHub；逐樣本實機／GPU 紀錄留在忽略的 `data/diagnostics/`。

- 已補 NVIDIA 整卡監測：CUDA／顯存控制器使用率、專用 VRAM、溫度、核心／記憶體時脈、功耗及 throttling 原因，提供近60秒平均／峰值、樣本數與新鮮度；無資料維持 unavailable，不用0代替。Dashboard 明確標示是整卡值，不是 MeowKeeper+ 程序專屬使用率。
- 遠端 r107 只讀快照相隔約24秒：來源1920×1080、連線1且有新影格；FFmpeg restart_count 833→834、AI 0.8→0.6 FPS、累計 dropped_frames 5624→5631。AI 在640／2 FPS時第二筆平均延遲283.2 ms，低於500 ms單張預算；MX250 50→51°C、整卡瞬間GPU 0%。這不支持溫度降階，但來源 ingest 重啟或排程／等待成為優先查證方向，尚非定論。r107 未回傳 worker 階段 timings、P95 或 provider 節點，正式管線階段根因仍 `not_checked`。
- 最後修正 benchmark 交錯順序、超時不追趕排程及 P90／P95／P99／max 後，本機 RTX 2060 靜態圖隔離三輪測試中，960／1280／1920 px 實際4.78／4.79／4.94 FPS；總耗時平均56.87／68.67／94.81 ms，總 P95 179.56／188.85／125.80 ms。**1920 P95 比960低可重現，但平均與中位數更高，不代表1920整體更快**；低尺寸偶發模型延遲長尾的根因尚未定位。GPU整卡平均19.43／23.41／28.73%，VRAM平均821／861／945 MiB，峰值溫度67°C。此 RTX 2060 結果不能外推；目標 MX250 與正式 Host 的後續覆核見本節最新更新。詳見[本機 CUDA 診斷](CUDA_DIAGNOSTICS_2026-10-19.md)；完整本機測試報告在忽略的 `data/diagnostics/gpu-staged-benchmark-final-no-burst-rtx2060.json`，不可公開。
- 修正後 CUDA benchmark／GPU telemetry／自適應 analyzer／policy／加速推論／worker／system telemetry 共48項測試通過，Ruff及 compileall 通過。此次修正的是量測器排程與分布統計，不是正式 Host 推論效能；尚未建置、發布或重啟正式 Host。
- 目前程式已補分階段計時、provider 證據、非阻塞背景 GPU 採樣及新的隔離 benchmark；針對性單元測試／Ruff／compileall 驗證結果以本次回覆為準。正式 r107 不含這些新增監測接線；本輪沒有重啟、改溫度限制、手動升階、建置或發布。
- 下一步：先在已安全確認 SSH 主機指紋的條件下，於 SHRIMP-ASUS 執行同樣的隔離矩陣；候選版再回傳真正來源等待、佇列、IPC、解碼、模型、追蹤分布，與 FFmpeg restart timestamp 對照。不得只憑目前兩個短快照認定唯一根因，也不得將靜態圖5 FPS當成家庭長期驗收。

### 最新覆核覆寫（2026-10-19 晚間）

- 上方「下一步執行 MX250 隔離矩陣」已完成：SHRIMP-ASUS MX250 靜態影格 960／1280／1920 px 實際4.302／3.986／4.170 FPS；總耗時平均163.70／201.26／230.85 ms，P95 302.56／305.06／394.23 ms；峰值73°C，未觸發停止線。詳見忽略的本機 `data/diagnostics/mx250-staged-benchmark-20261019-summary.md` 與原始 JSON；不得公開。
- 正式 r107 監控測後已恢復；36秒只讀快照顯示來源 2.29→4.67 FPS、AI 1.4→0.6 FPS、累計丟棄 257→272、FFmpeg restart_count 2→3。AI最近一次耗時 331.1→428.2 ms，但近期平均 1241.7→1268.4 ms，均高於 500 ms 預算；MX250 52→54°C，瞬間整卡 GPU 0%。最後記錄的歷史降階觸發為640／3 FPS、平均1382.7 ms／333.3 ms預算、56°C／整卡11%。r107缺worker分階段timings與P95，來源重啟／影格供應及非模型工作是待驗方向，非唯一根因。
- 候選 Dashboard 已將單次耗時、近期平均／P95及「近期平均是否超時」分開呈現；29項針對性測試、Python Ruff及Node語法檢查通過。正式Host未重啟／升階，沒有建置、簽章、發布或GitHub推送。後續用候選版分階段量測對照真實來源與 FFmpeg 重啟時間，不得將靜態圖5 FPS當作家庭長期驗收。

## 2026-10-19 接續：AI 疊圖、身分候選、CUDA 與發布隱私

- 遠端正式 r107 即時畫面再次實測：攝影機畫面有貓，偵測框／身分候選／動作候選開關皆開，但影像無實際疊圖。AI 當時約 640 px／0.8 FPS、1 FPS 上限、CUDA、52°C／瞬間 GPU 0%。偵測效果未通過；身分／動作候選未驗收。含家庭影像的截圖沒有存入發布包。
- repository 身分候選 `minimum_margin` 由 0.08 降至 0.05；只讓接近分數較容易作為未確認候選顯示，其他相似度／unknown／單貓校準門檻不變。新增三個回歸；尚未建置／部署，r107 未變。
- 截圖及15秒錄影通知改為不攔截操作、無手動關閉的自動消失 snackbar。隔離 JS runtime 測試通過；完整 Edge 測試因缺 Playwright 未跑。
- 可攜包改用公開文件白名單，只複製一般 README 和 Windows 使用指南；測試驗證工程報告不進包。實機診斷文件仍只留本機；本輪沒有 GitHub 上傳。
- CUDA：既有 MX250 60秒隔離比較顯示背景 GPU 採樣阻塞由約65.94降至0.50 ms，吞吐約2.99→3.00 FPS、GPU平均約77%，無可證明的FPS提升；5 FPS長期與完整正式管線仍未驗收。詳見 [本輪本機覆核](AI_OVERLAY_CUDA_UI_ACCEPTANCE_2026-10-19.md)。

## 2026-10-19 接續：測溫工作與 r110 候選

- **發布停止點**：r110本機21,509檔建置／獨立校驗與簽署完成（ZIP 2,411,165,159 bytes），但公開候選分支推送遭安全審查拒絕：內容含內部實機紀錄／主機資訊，超出乾淨程式包授權。拒絕發生在整條命令執行前；沒有commit／push／Release／SHRIMP升級，沒有繞過。先前自行暫存的檔案已撤出暫存區，工作檔案保留；本機HEAD仍a028de4，候選分支只在本機。`dist/release-r110/DO_NOT_PUBLISH.md`標記此包不得直接公開。下一步需分離公開程式／一般使用說明與內部紀錄、確認公開範圍，再重新建置／審查；不把舊r110簽章當作隱私審查通過。

- 使用者已重新登入遠端並同意受限 SYSTEM 測溫工作。新增固定工作／受保護快取、Host 時間戳與 `-UserHost` 分離身分選項；新增10項與既有測溫合計23項通過，未部署／註冊。見 [接線與安全邊界](CPU_SENSOR_BROKER_2026-10-19.md)。r109已完成21,506檔校驗與runtime imports，但不含新工作，不能當最終交付；新完整回歸進行中。
- 實機疊圖：r107手動恢復後CUDA worker存活並有新分析；三項顯示開關可各自關閉／恢復，但detections為空。使用者確認床中央偏左確實是貓；同張1920×1080影格、同模型CPU在640／960、診斷分數0.1均無候選，確認漏偵測，未改正式0.55門檻。身分／動作正確性未通過，不能將開關測試當完整驗收。
- 1920隔離偵測僅在另一區域找到約0.693貓框，未找出已確認的床上目標；該區域裁切的DINOv2第一／第二候選0.62234／0.54570，差距不足而保留unknown。減少CPU執行緒沒有改善單張耗時，未修改正式設定。完整結果見 [實機AI覆核](AI_OVERLAY_REAL_ACCEPTANCE_2026-10-19.md)。
- 追加即時複核：預覽中同時有貓與人，三個疊圖開關已開但沒有實際框；正式 AI 約640 px／0.4 FPS。隔離推論有一張1920 px影格出現候選（0.7628、0.6395），之後新影格在640／960／1280／1920全部無候選，故高解析陽性不穩定。以舊框座標裁切較晚影格的DINO結果為unknown／low_similarity（嗨啵0.126）；時間戳未對齊，不能當床上貓的有效身分結果。正式偵測框未通過；逐軌跡身分與動作因缺少穩定活動track未驗收。未改門檻／特徵庫／攝影機連線。報告已補記。

- r110候選正在建置；首次完整924項／328.283秒通過、2略過；其後固定系統模組載入／工作識別補強的16項定向、Ruff與compileall通過。最新凍結版本再次完整回歸進行中。新增工作尚未做SYSTEM註冊／驅動／目標筆電讀值；不簽署正式完成聲明。
- 最新凍結版本完整924項／325.159秒通過、2略過；90項強制回歸通過。另Windows PowerShell 5.1的排程／ACL物件建構通過，沒有註冊或寫ACL。SHRIMP SSH目前為管理員邊界，但正式Host仍為user帳號；這兩件事不能混淆。GitHub已核對Aaron／kinostudio，最新公開仍r107，未發布新版本。

### 本輪較早檢查點（歷史，不代表目前登入或建置狀態）

- 最新凍結原始碼完整 unittest：914 項、2 項略過、230.060 秒；Ruff、compileall 與本輪兩個 JS 語法檢查通過。r109 建置已通過 27 類故障／80 個不重複測試的強制門檻（無失敗或略過），正在完成檔案雜湊；候選包尚未完成獨立校驗、簽署或發布，也未升級 SHRIMP。此數值取代下列較早的 905／910 項檢查點，不代表外部驗收完成。
- 2026-10-19 接續確認遠端登入仍過期（受保護狀態頁回「需要 Web Dashboard 登入」）；未操作正式 AI 恢復。候選第一方文字檔的常見 Token／私鑰格式掃描未命中，但這不是全面秘密或再散布授權審查。

- 新增確定性回歸重現5 FPS來源／外層3／安靜1的雙採樣錯位：修復前0.8 FPS、修復後1.0 FPS，外層較低安全上限仍生效；這是機制證據，不是當時正式Host安靜狀態的證明。增加排程器按優先級等待分布及升級失敗時遷移journal保留修復。先前910項（2略過）通過後還有上述異動，需重新完整驗證；未發布、未升級。
- 已詢問是否授權受限SYSTEM測溫背景工作、保留Host原user帳號與既有憑證；未獲答覆前不新增特權工作，也不直接切換SYSTEM導致Telegram失效。遠端Dashboard登入亦待使用者完成。

- SSH已恢復且實機完成同影格640／3 FPS兩段各60秒隔離比較；GPU採樣阻塞65.94→0.50 ms，但FPS2.99→3.00，不能宣稱正式0.8 FPS已修好。目標MX250觀測CUDA308／CPU55節點、GPU平均約77%，未升到5 FPS。原始碼加入細分耗時、非阻塞採樣與歷史／目前降階區分，尚未部署；見 `CUDA_DIAGNOSTICS_2026-10-19.md`。下一步是正式Dashboard登入、完整pipeline根因驗證，不把獨立模型當端到端。

- 使用者追加 CUDA 有效吞吐與降階診斷：先量測再優化，保留手動恢復及負載／溫控，不新增攝影機上游。最新正式 Host 快照已不是先前0.8 FPS：AI `suspended`、worker不存活、FPS為0，來源仍約4.94 FPS；不可用此快照當成持續CUDA推論基線。原始碼確認GPU採樣同步執行但排除於`analysis_end_to_end`計時，故該延遲不能解釋全部分析週期；需進一步實測各階段，不先宣稱根因。
- 本輪完整905項測試通過、2項略過；Ruff／compileall／JS通過，但尚未建置／簽署／發布r109，未升級遠端。新增CUDA修改後既有測試證據必須重跑。使用者新增診斷任務與未處理帳號／資料衝突前，不冒險升級正常運作的影音。
- 後續遠端唯讀 GPU 採樣時，VS Code Remote-SSH 已要求重新輸入 `user` 密碼；未送出登入，停止該視窗操作。需要使用者親自完成既有 SSH 登入後才能繼續實機基線；沒有取得時脈／60秒樣本，不補造結果。更新自動化為擴充遠端安裝／發布範圍遭安全審查拒絕，未建立新的週期性安裝行為；本輪明確授權的單次操作與既有心跳保持分開。

- 使用者本輪明確授權建置、發布 GitHub，並代為更新 SHRIMP-ASUS；這是本輪例外，不改變其他升級仍由使用者自行安裝的跨版本規則。UAC／官方驅動授權視窗由使用者親自確認。
- 安裝器加入選用 CPU 測溫同意提示、跨 UAC 保留選擇；系統安裝沿用同一個管理員程序，測溫準備失敗不阻擋影音。可攜包新增 `Enable-CPU-Temperature.cmd`。
- 同意後直接從官方固定版本下載，SHA-256 與 Authenticode 發行者雙重核對；未核對再散布授權，不直接附帶驅動。保留官方互動安裝器的授權／版本選擇，不靜默接受 EULA、不使用 unrestricted、不修改驅動 ACL、不自動重開機。
- SHRIMP-ASUS 本輪實測缺少 PawnIO；更新後仍須驗收連續新鮮溫度。一般權限可攜 Host 的 `permission_required` 不會因裝好驅動就保證消失；本輪不建立特權 IPC／感測服務。
- 遠端已修復745筆影片路徑並完成一支真實人工回報影片播放；見 `C701_EVENT_DATA_REPAIR_2026-10-19.md`。升級前需封存外層舊 Data 副本，不能混合兩份 SQLite。發布及更新結果需另記，不用原始碼測試宣稱部署成功。

## 2026-10-19 追加：安裝資料根目錄、事件影片與診斷回報修復（repository）

- 安裝 Host 預設與 Tray 事件／日誌操作共用資料根目錄解析；新安裝使用 `ProgramData\MeowKeeperPlus\Data`，舊 `App\data` 在 canonical Data 僅有收據／暫存時會維持有效。另已修正：Tray 依運作中 Host 回報核對實際資料路徑；開始功能表圖形入口依 Core 排程工作實際參數選擇設定檔，不再假定一定是 Data。下次明確升級時，先安全遷移舊資料與 `App\config.json`；同名衝突停止且不覆寫，檔案複製／校驗前失敗按 journal 回復。開始功能表「喵喵守護者+」捷徑是已安裝版的圖形重啟方式，不需 CLI。
- 事件播放在 DB 的舊絕對路徑失效時，只會在目前 `events` 根目錄用事件 ID 對應 `event_<id>.mp4`；不會讀取或提供事件目錄外任意路徑。
- 小米頁唯讀「檢查連線／防火牆」結果改為顯示於「連線遇到問題？」展開區，附白話摘要與可展開 JSON；`/api/v1/status` 的 Dashboard LAN 規則和 Xiaomi UDP 診斷分開解讀。
- 定向測試 23 項通過；全套 unittest 894 項通過（2 項略過）、Ruff、compileall、60 項發布回歸門檻、Windows PowerShell 語法解析及前端 JS 語法通過。已建置 `dist/MeowKeeperPlus-portable-2026-10-19-r108`；內附發布回歸 60/60 通過，包完整性驗證 21,495 檔通過且無私人資料。尚未安裝／升級、未搬移使用者資料、未重啟正式 Host；使用者提供的狀態快照顯示來源有連線但沒有有效影格欄位，實機根因仍 `not_checked`。
- 2026-10-19 後續資料目錄修正加入 4 個回歸案例；舊 r108 的建置／gate 證據不適用於本次程式變更。需重跑發布門檻及 Windows shell 驗證後才能建置新版。本機測試不會變更 C701 電腦上的 Data，也不等同於遠端實機驗收。

## 2026-10-18 追加：Xiaomi 隱私／PTZ fail-closed 修復

- Repository 已補上隱私操作分類、登入保護的診斷入口、最近 50 筆遮罩控制紀錄，以及絕對定位型號閘門；位置圖未有機型實測前不可操作，方向鍵維持原本短步移動與停止流程。
- 32 項定向測試通過，compileall 通過；Ruff 與本次提交候選測試紀錄見 [修復覆核報告](R107_XIAOMI_PRIVACY_PTZ_FIX.md)。正式 Host、包內與家庭攝影機馬達本輪未驗收／未重啟。
- 相對移動閉環理論可行，但需先量測每方向的座標變化與新鮮位置讀回，才可逐脈衝自動逼近目標；未完成校正前不開放座標圖或隱私位置移動。

## 2026-10-18 當前接續：接入與監控改版

- r107加入首頁硬體採樣倒數圓環、CPU／RAM／GPU數字垂直滾動、使用率環圈及溫度指針動畫；沿用既有約5秒 telemetry 快照，沒有新增輪詢。完整測試877通過、2項因隔離媒體條件略過；Ruff、compileall與Edge合成狀態測試通過。建置／簽章／GitHub狀態見[r107報告](R107_BUILD_REPORT.md)；正式Host未升級。

- 共用按需 H.264 MSE 備援已實作；原碼優先、多人共用、最後離開清理、硬體初始化探測／保守 CPU 路徑，真實合成 HEVC 及 Edge 自動回退通過。見 [本輪報告與啟動碼](ON_DEMAND_H264_PREVIEW_2026-10-18.md)。開發 Host 使用 18981；正式服務未升級。真實 4K、MX250 效能及長時間仍待驗。

- 已接線三步一鍵接入、本機／中繼偏好、同程式中繼、影片內保存提示、分組規格、雙欄 PTZ、點擊／鍵盤／觸控定位、硬體環圈／指針與事件捲動；隔離 Edge 27 項及 loopback 真實媒體通過，見 [本輪報告](CAMERA_MONITOR_REDESIGN_2026-10-18.md)。
- 本輪不安裝、不觸發 UAC 或重啟正式 Host。下一步：可連線環境驗證真實來源保持、實體短距離上下定位、異地配對／影音控制及長時間測試。不得用下列歷史證據勾掉本輪待驗門檻。

本文件為任務執行入口；完整需求、接線矩陣與正式產品階段見 [PRODUCTIZATION_SPEC.md](PRODUCTIZATION_SPEC.md)。原始驗收報告保留日期，歷史部署版本不視為目前版本。

- 2026-10-17 V4.3 驗收覆核：先前全套 859 tests、Ruff、compileall、6 個 JS runtime 與 `static/js` 根目錄 JS 語法通過；本輪 60 個 V4.3 相關測試通過，新增候選→原生影片回條→逐軌跡回報→SQLite 歷史閉環，以及語意 API 延遲不阻塞影片組裝的回歸。使用者已授權真實視覺 API／Telegram 測試，但隔離環境無 Host listener、LAN URL 逾時且瀏覽器工具中止，故未傳送任何影格／訊息；真實 API、Telegram MP4／message_id／按鈕與正式包驗收仍 `not_checked`，整體未驗收完成。詳見 [V4.3 驗收覆核](V4_3_ACCEPTANCE_AUDIT_2026-10-17.md)。

## 當前狀態

- 2026-10-17 Tailscale 遠端裝置／觀看者監控已接線：官方唯讀 peer 狀態、本機別名、Dashboard session 與 JPEG／MSE／WebRTC 實際觀看分開呈現；詳細驗收與限制見 [實作報告](TAILSCALE_REMOTE_VIEWER_IMPLEMENTATION_2026-10-17.md)。repository 測試通過不代表真實 Tailnet／異地播放通過；本次未建置、安裝或重啟 Host，正式運行版本 `not_checked`。
- r106已建置並公開發布（2026-10-17）：包含CPU感測helper、既有已驗證PTZ與DINOv2實驗模型。830項測試、包內校驗與MSE1080p約20 FPS、五個遠端資產大小／SHA-256、未登入下載與簽章驗證通過。CPU管理員三分鐘36／36樣本、78～95°C、平均86.03°C通過；正式Host未升級。見[r106建置發布結果](R106_BUILD_REPORT.md)與[GitHub Release](https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r106)。

- 2026-10-17追加真實攝影機MSE覆核：修正版repository隔離Edge 1920×1080約20 FPS，跨82秒播放、雙consumer單producer、三模式切換與觀看後釋放均通過；正式r104仍缺websockets且實測回退。CPU持續測試在Aaron一般權限亦被驅動拒絕，需管理員限時試驗，尚未通過。見[本次實機報告](CPU_MSE_REAL_ACCEPTANCE_2026-10-17.md)。

- CPU 持續觀測本輪收斂：830 項完整測試／273.130 秒 OK，12 類 24 項故障回歸通過；一般權限不可用提示、桌面與手機排版通過隔離 Edge 驗收。仍未建包／部署，未驗證具權限 24H 持續實溫；見[本輪接線報告](CPU_TEMPERATURE_CONTINUOUS_INTEGRATION_2026-10-17.md)。下列一次管理員讀值為先前證據，不是現在的數值。

- CPU溫度整合（2026-10-17）：使用者另行明確授權安裝PawnIO2.2.0與一次管理員試驗；本機i7-10875H六筆CPU Package（86～91°C）真實隔離讀取通過，Helper已退出，33項相關測試通過。正式Dashboard持續感測、AMD／其他硬體、24H及低負載仍待驗，未建立常駐特權bridge或改Host。見[真實驗收](CPU_TEMPERATURE_REAL_TRIAL_2026-10-17.md)。

- 同日新增：H.264 WebRTC原碼共用預覽、Telegram逐軌跡身分回報預設本機學習、來源尺寸跨重啟記憶已接線。隔離Edge約20 FPS、雙peer與登出撤銷通過；完整回歸803項通過。尚未建包／部署；真實攝影機LAN／Tailscale與Telegram待驗。詳見[本輪報告](WEBRTC_FEEDBACK_SOURCE_MEMORY_2026-10-17.md)。

- 2026-10-17已補工程回饋閉環：故障台帳→強制回歸→程式指紋→包內證據→簽署前檢查，以及安裝安全結果摘要；身分衝突雙向撤回也已接線。見 [補強報告](PRODUCT_HARDENING_2026-10-17.md)。以上均為repository修改，未進r105，不替代下列實機待驗項目。

- 本機最新候選目錄：r105；包內MSE／三模式隔離實機證據見 [r105報告](R105_MSE_RUNTIME_ACCEPTANCE.md)。本輪未重新校驗包、部署或查詢公開Release。
- r105後的半透明PTZ方向鍵、初始位置讀取與2秒座標變化提示：repository已接線、17相關測試通過，**真實鏡頭／包內／正式版仍待驗**，見 [驗收紀錄](PTZ_VIRTUAL_PAD_ACCEPTANCE.md)。
- V4.3與V5有程式接線，不等於外部Provider、家庭識別、正式Telegram與長時間測試已通過。H.264 WebRTC已隔離通過；HEVC WebRTC仍未實作；共用按需H.264 MSE於2026-10-18新增，真實攝影機／目標硬體仍待驗。
- 2026-10-17 場景參考編輯器已回到「保存畫面／上傳參考圖→拖曳框選物件→本機拼貼預覽」流程，並接到可選圖片 Provider 的獨立同意控制；排除／隱私區不送出，參考圖不當作事件或貓身分證據。定向60項、完整測試843項、回歸門檻13類／33項、全 repo Ruff／compileall 及 JS 語法檢查通過；真實 Provider、瀏覽器視覺與家庭畫面驗收仍待做，本輪不建置。詳見 [V5 AI 管理中心狀態](V5_AI_VISION_MANAGEMENT_STATUS.md)。
- 2026-10-17補強後完整回歸798項／216.819秒／OK；已知故障門檻9類17項passed。見 [補強報告](PRODUCT_HARDENING_2026-10-17.md)。這是repository測試，不代替未建置的新包或外部實機驗收；舊r105的788項保留在該版報告。
- 當前安裝／執行版本：本輪 `not_checked`。本輪不安裝、不觸發UAC、不重啟Host。

## 下一階段：P0 收斂，不繼續堆功能

CPU觀測接續：以CPU Package／Die為主、主機板部位為輔。Repository已接持續來源、5秒共用快取、登入API、目前值與歷史UI；隔離版一般權限的不可用狀態與手機排版通過，具權限持續實溫／24H／包內仍待驗。未改AI溫控、未建立特權服務。見[持續接線報告](CPU_TEMPERATURE_CONTINUOUS_INTEGRATION_2026-10-17.md)；不把一次實拍數據當長期監控。

1. **真實PTZ閉環**：在授權且已配對的活動來源驗證載入初始座標、短促上下、finally停止與前後讀回；未變／失敗不宣稱實際移動。優先隔離驗收，不要求先安裝。
2. **來源與控制一致性**：本地／遠端、切換攝影機／畫質、Host重啟、來源記憶與PTZ端點配對；不能按C701型號猜遠端。
3. **真實通知與回饋**：指定版本的事件JPEG／MP4→Telegram回條→收件播放→逐貓／逐軌跡回報→SQLite，另驗兩位用戶的通知／冷卻／每日靜音互不影響。
4. **交付保護**：內附runtime實跑、完整回歸、秘密／包校驗、安裝停止程序競態與資料保護。給精確升級命令，由使用者安裝；不將建包當作部署。

## 產品化順序

P0接線凍結 → P1安裝／升級／備份復原 → P2家庭24H／7天Beta → P3新手與跨裝置UX → P4 RC安全／支援／授權檢視 → P5穩定核心1.0。進階AI、WebRTC與更多來源列P6獨立迭代；研究功能可保留experimental，不阻擋可靠核心成為正式產品。

每項門檻、交付與量測方法以 [正式產品規格第5節](PRODUCTIZATION_SPEC.md#5-從現在到正式產品階段與完成門檻) 為準；新數值是規劃目標，不是已達成結果。

---

## 以下為歷史接續紀錄（截至2026-10-11，不再作當前部署指示）

以下保留當時任務與證據，包含過期的「最新」「尚未部署」等時間性用語；現況以本文上方及有日期的整合規格為準。

## 下一次版本更新必含：跨電腦 Xiaomi UDP 修復（2026-10-11）

使用者要求 r85 本地 C701 筆電的 Public UDP Block／Private-only Allow 衝突修復納入下一版。r86 原始碼已加入診斷與授權修復、畫質切換後重連及待機原位置恢復；完整實作與驗收見 [r86 報告](R86_XIAOMI_CONTROL_FIX.md)。遠端新版實機尚未驗收，更新不得自動改防火牆。

## 自適應 CPU／GPU 推論目標（2026-10-11）

設計與分階段驗收見 [ADAPTIVE_INFERENCE_DESIGN.md](ADAPTIVE_INFERENCE_DESIGN.md)。
40% 為可配置的長期負載目標；先完成影音 reader／AI worker 隔離，再實作尺寸與 FPS 回退。
GPU 與純 CPU 都要驗收，不改攝影機畫質或覆蓋使用者偏好。
repository 第一階段已實作持續 reader／容量 1 信箱、FPS 負載／溫度回退及 Dashboard 手動重試；[目前證據與缺口](ADAPTIVE_INFERENCE_IMPLEMENTATION_REPORT.md)。獨立 AI 程序、進階解析度、完整應用 CPU 預算及實機長測尚待完成，正式 Host 未部署本輪變更。

## 攝影機控制與來源記憶長期目標（2026-10-10）

最新三項目標及逐項驗收見 [CAMERA_CONTROL_PERSISTENCE_GOAL.md](CAMERA_CONTROL_PERSISTENCE_GOAL.md)：
真實畫質切換／分層規格顯示、Xiaomi MISS PTZ 實體方向與停止、最近來源及逐攝影機設定恢复。
已建立 active GOAL 並完成第一批 repository 修正；r73 候選包通過完整性驗證，但尚未部署或完成正式攝影機驗收。不可由單元測試推論三項已完成。
先盤點正式 Host 與保存契約，再依來源記憶 → 畫質 → PTZ → 正式發布驗收執行。

## 受控回饋學習接續（2026-10-09，repository-only）

本輪新增逐軌跡人工身分回報 → 獨立回饋特徵庫 → 既有身分分類器讀取的閉環。
行為回報先作評估，不自動改規則或重訓；整體「兩隻都在」沒有 track 對應時不學習個體。
細節與限制見 `docs/FEEDBACK_INCREMENTAL_LEARNING.md`。
本輪不重啟正式 Host、不修改正式資料庫，未建置／部署此項變更。
下一個驗收為新版部署後，真實 Telegram 的逐軌跡確認產生回饋特徵，
再用未參與學習的家庭片段驗證身分與行為候選；不得由單元測試推論準確率改善。

## 最新接續（2026-10-09）

原生 patch 新增／修改／刪除已實測恢復。CPU/GPU runtime fallback、V0.2 後端同步、
全螢幕尺寸修正已寫入 repository。具體證據與未完成項目見
`docs/ADAPTIVE_RUNTIME_TRACK_FEEDBACK_GATE.md`。
正式 Dashboard 已可登入；本地 RTX 2060 真實 CUDA 推論、CPU 推論與受控 GPU
故障後 CPU 重試已通過。最新程式部署、正式全螢幕與真實 Telegram 逐軌跡
標籤落庫仍待驗收；不得將本地測試等同正式 Host 完成。

補驗發現 r70 BoT-SORT 的相容性重試沿用舊 CUDA 參數；已修正並真實驗證
模型維持 CPU、2 個獨立 cat tracks 與 2 張 crop。r71 已建置並通過完整性驗證；r70 不應再作為
此目標的升級候選。正式版本仍為 r66；前次 UAC 已取消，不能重複觸發或宣稱已部署。
逐軌跡通知已加入 opt-in 編號截圖與先確認貓咪的提示，但仍需正式 Telegram 點擊落庫。

本輪全套 412 tests、Ruff、compileall 通過；r71 verifier 回報 19,301 檔、
`private_data_present=false`，四個關鍵推論／通知模組與 repository SHA-256 一致。
下一個必要操作：由使用者在管理員 PowerShell 升級 r71，再驗證正式 Host、
全螢幕及真實逐軌跡回饋；目前不是完整端到端驗收完成。

## 最新實測覆寫（2026-09-28，r32 升級後）

發布跟進：`dist/MeowKeeperPlus-portable-2026-09-28-r33` 已建置（18,683 files），包含回條紀錄與 QuickEdit guard。發布前 291 unittest、全範圍 Ruff、compileall 通過；指定 Telegram Token／真實 Xiaomi URL 格式掃描 0 命中。新包 `telegram_commands.py` 與 repository SHA-256 均為 `8D05A585C3A1364F480C8C4F80D96657A6F2735B8FFD2F0A2E1CE4461F9C20A7`。內附 verifier 退出 0、status=passed、private_data_present=false；SYSTEM 安裝尚未執行，正式 Host 仍是 r32。此跟進不改包內文件或 manifest。

後續 repository-only 修正：Telegram 命令成功傳送新增安全回條紀錄（實際 message_id、匿名 user_ref、種類、耗時與重試次數）；無型別回條或缺少 message_id 不標為 verified。20 項命令測試通過，未向外傳送。這項修正尚未重新建置／安裝；因此以下兩檔 hash 一致是修正前快照，現在 `telegram_commands.py` 已與正在執行的 r32 不同。不得宣稱新紀錄已在正式 Host 生效。

以下證據取代本文後面 r22 正在執行／r31 尚未安裝的歷史敘述；不代表所有 runtime 檔案均已逐一核對。

- `/healthz` HTTP 200：`status=ok`、`core_status=running`。
- TCP 8000 owner 為 Host PID 19908；go2rtc PID 30300 同時持有 1984／8554；三個 listener 均為 `127.0.0.1`。
- ProgramData App 與 repository 的 `tools/run_host.py` SHA-256 均為 `80999AC20B8503D70E77CBDBB96869B4ACFDD5399C3795C888D307593E0AD587`。
- ProgramData App 與 repository 的 `telegram_commands.py` SHA-256 均為 `5001FF84C72F04003CBE588E10E5053D419529B9C1999745B2AD70356FDD2408`。
- Data config 的 `telegram.enabled=true`；這只證明保存設定，不證明目前 poller／outbound queue 已啟動或 Bot 已連線。
- 本轮 `test_telegram_commands.py` 17 tests 通過；是本機 mock／服務測試，不能替代實際 Telegram message_id、JPEG、MP4 或 camera adapter 回應。
- 原 r32 installer 的停滯已追到 PowerShell QuickEdit 選取狀態；解除選取後安裝程序結束、Host 健康。repository 的 QuickEdit guard 修正不在既有 r32 包內，下一包需重新建置。
- C701 `chuangmi.camera.079ae2` 已確認在另一住處；本輪 SG discovery 找到唯一裝置，但自動／TCP SD 測試均在 UDP 握手超時，未取得 JPEG。不再重複當地 LAN profile 測試，也不把候選 SG 當成影格驗收。
- 真實 Telegram 命令 E2E 仍未驗收；本輪 Windows UI 工具無法確認 Edge URL 而停止，未透過它發送訊息或變更設定。

下一步：在既有登入 Dashboard 確認 Telegram runtime（不僅 enabled 設定），使用同區網已驗證攝影機完成 `/status`、`/home`、`/snapshot`、`/live` 與確認式 `/reset` 的真實回條。隱私 adapter 尚未配置時 `/hide`、`/show` 維持 unavailable，完整目標仍未完成。

## 歷史驗收基線（前次檢查；本輪未重新盤點）

- Git：`a028de49d4cabf5e5344b8cec8a00a79d647b6cb`；r22 可攜式包。
- 前次 `/healthz` 回應 ok／running；Host PID 21380，執行 r22 embedded Python。
- 前次唯一直接 FFmpeg child PID 29200，來自 r22；當時最新 JPEG 約 0.16 秒前更新。
- 9/27 的兩小時觀測：7200 秒、240 筆樣本、預覽超過 10 秒未更新為 0。
- 9/27 指定 FFmpeg 終止恢復：5.813 秒、Host 不變、有效 640×360 JPEG；不是攝影機／網路斷線驗收。
- 前次唯讀資料庫檢查：5 筆 `cat_detected`，影片均 completed、通知均 sent；video message_id 為 18、19、20、21、22。
- 前次 FFprobe 檢查五支 MP4 均可讀、640×360、14.945～15 秒；第一支 HEVC，其餘 H.264。
- 上述事件與 API 回條已可查證；人工標註回饋尚未驗證。r22 舊 schema 不記錄觸發來源，所以舊事件不可用來聲稱 detector 樣本。
- 261 unittest、Ruff、compileall 是 r22 建置時的歷史驗證結果，不代表本輪執行。

證據位於本任務工作目錄 `work/`：`r22-soak-summary.json`、`r22-live-recovery.json`、
`r22-two-hour-real-camera-report.md`、`progress-review-2026-09-28.json`；私人事件資料不得提交至 Git。

## Telegram 貓咪回饋與遠端命令面板（r31 建置時歷史證據）

- 貓咪 detector JPEG／MP4 提供「是貓咪／不是貓咪」按鈕；單一 poller 驗證私人 Chat、回條 message_id、標註權限、重複 update 與 offset，並保存人工標籤。
- SQLite schema 7 保留來源、模型版本／SHA／當時門檻、每人最新標籤、修改歷史、多人衝突、回饋 offset 與 reset 請求；舊事件維持 `unknown`。
- 本輪 Telegram 遠端命令原始碼已接通 persistent menu、角色授權、`/status`、`/home`、`/snapshot`、`/live`、`/today` 與確認式 `/reset`。`/reset` confirmation/update offset 原子提交，成功後由 Host graceful restart，健康啟動後回報。
- `/status` 與 Host startup notification 已共用 `RuntimeStatusService`：以真實 `/healthz`、目前 metrics、最近影格、go2rtc、LAN／Tailscale 網址、CPU／RAM、SQLite 與磁碟狀態組成快照；不再由兩套不同程式各自推測運行狀態。
- `/hide`／`/show` 已經 `PrivacyService` 抽象且 fail-closed；目前沒有可設定並經實機驗證的相機隱私／恢復位置 adapter，因此不會移動攝影機，也不能算實體隱私控制已完成。
- 命令回覆狀態使用 live metrics、近 20 秒影格、當前 Host PID／uptime、go2rtc 健康、SQLite、動態 LAN URL、CPU/RAM 與儲存資料；沒有已驗證來源能力時行為統計仍不可用。
- 本輪驗證：287 unittest 全過；Ruff、compileall 通過。r31 可攜包已建置，內附 verifier 通過（18,683 個檔案、私人資料檢查 false、模型 SHA-256 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`）；r31 的 app/runtime 檔案雜湊與目前原始碼一致，僅後續補充了不納入發布包的測試案例；r31 尚未安裝或證明正在執行。
- 外部層尚未通過：未向真實 Telegram 傳送本輪命令面板訊息或點擊 callback；未重啟／升級 Host。唯讀 `netstat` 顯示 TCP 8000 owner PID 21380，`Get-Process` 的完整 executable path 是 `dist/MeowKeeperPlus-portable-2026-09-27-r22/runtime/python/pythonw.exe`，且 `/healthz` HTTP 200；因此目前回應 Dashboard 的確是舊 r22，而非 r31。CIM 命令列盤點遭拒，ProgramData manifest 只提供 `package_type`；go2rtc PID 24092 的 executable path 亦無法讀取。SYSTEM 安裝身份仍未證明，不可把健康回應誤認為最新包。
- 信心改善只提供一致人工標註的已告警 precision、95% Wilson 區間、樣本數與模型分數分桶；這不是全域 accuracy、recall 或機率校準，亦不自動改分數、門檻或重訓。評估漏報仍需未告警負樣本及獨立驗證集。

## 下一階段順序

1. **安裝與真實回饋／命令驗收**：r31 建置與 verifier 已完成；SYSTEM 升級仍需使用者核准 UAC，之後確認既有 DB 可增量遷移、Host/Tray/go2rtc 單一實例、真實 bot menu、至少一筆「是貓咪」和一筆「不是貓咪」回饋、/snapshot、/live、/status、/today、/home 及 reset 結果。未驗收 hide/show 的實體控制前維持 unavailable。
2. **家庭資料校準評估**：收集人工正負標註及未告警影格；已告警樣本只估 precision，另需未告警影像評估漏報／recall。先統計，再人工批准門檻，不自動訓練或換模型。
3. **發布與穩定性**：SYSTEM 與新包一致、實際來源中斷、Windows 重啟、12～24 小時家庭驗收。已完成兩小時與 FFmpeg 恢復不重做。
4. **隱私相機 adapter**：由使用者明確選定安全 privacy/restore position，再驗證結果回讀；未配置前不得把 /hide、/show 標為成功。寵物登錄、行為與統計仍屬 experimental，需家庭標註驗收。
5. 最後處理聲音與隱私自動化，不影響單一 ingest 與主通知流程。

## r31 建置時界線（以最上方最新實測為準）

Telegram outbound 有真實回條；本輪本機 source feedback integration 與最新可攜包檢核通過，但正式 Host 當前 build 無法由 CIM 盤點確認，且最新包未安裝，
不得宣稱真實 callback 端到端成功。回饋 precision 僅對一致人工標註的已告警事件計算。
SYSTEM 安裝未證明已更新；GPU、MX250、PTZ 真實成功與完整 24H 皆不得宣稱完成。
本次修改程式與文件；未重啟 Host、改動正式 go2rtc 或修改秘密。
