# 攝影機畫質、PTZ 與來源記憶長期目標

建立日期：2026-10-10。狀態：`in_progress`；長期 GOAL 已建立，三項實機驗收尚未通過。
已進行 repository 修正與候選包驗證；正式設定、攝影機與服務尚未因此變更。

## 問題與證據界線

- 使用者回報：切换畫質後仍顯示 640×360；026c02 PTZ 顯示未啟用；來源往復需重新設定。
- repository `core/media/ingest.py` 對 JPEG 影格套用縮放；`app/core_controller.py` 區分 analysis_width 與 identity_frame_width。因此預覽尺寸不等於來源原始尺寸；目前尚未證明畫質變更失敗的根因。
- `XIAOMI_MISS_PTZ_REAL_TEST_2026-10-02.md` 與 `C701_REMOTE_CONTROL_GATE_2026-10-06.md` 記錄隔離實驗、失敗及正式版能力限制，不能当作 PTZ 已驗收。舊文件與安裝版本差異需在實作前重新比對。
- 不以 region 候選、已登錄 producer、HTTP 200 或按鈕可點代替收到影格、實體移動或持久化成功。

## G1：畫質切換與真實規格顯示

目標：即時畫面可對已配對的本地或遠端單一來源套用 subtype=1/2/3/auto；顯示攝影機實際輸出，而不是把 AI 尺寸當畫質。

實作順序：比對正式 Host 與 repository 版本 → 追查選項、保存、stream 更新、ingest 重接與 metadata → 修正最小接線 → 真實往返驗收。

驗收：

- [ ] UI 分開顯示來源原始解析度／FPS／codec、預覽解析度／更新 FPS、AI 輸入尺寸／分析 FPS；無證據顯示「尚未取得」，不沿用舊來源數值。
- [ ] `1→2→3→auto→1` 每次記錄請求 profile、實際生效 profile、實際來源尺寸、首次新影格時間與 producer 數量。代碼與尺寸按機型驗證，不寫死 3=4K。
- [ ] 相同解析度的 profile 顯示「已套用，裝置輸出相同規格」；只有真實新影格及生效來源參數確認後才成功，不用放大低解析度圖片冒充高畫質。
- [ ] 切換同一 stream，不新增攝影機端第二個 producer；AI 仍用 640 級、batch 1、約 5 FPS。預覽尺寸與來源畫質解耦。
- [ ] 操作有進度、期限與繁體中文結果；失敗回復上次有效設定、恢復影格，保留安全錯誤；相同失敗不無限重試。
- [ ] 本地 026c02 先驗收；HLC7 及 C701 本地／遠端分別驗收。未持有實機的型號標 `not_checked`。

## G2：啟用 Xiaomi MISS PTZ 並實機驗收

目標：目前接入的可旋轉攝影機可由即時畫面控制；控制成功必須有實際移動證據。第一台為 026c02，後續 C701 本地及遠端分開驗收；固定鏡頭不得顯示可用方向鍵。

實作順序：盤點既有實驗 binary、commit／hash 與 API 能力 → 單一來源隔離測試 → 主代理驗證 → 明確選用已驗證 backend → 候選包／正式 Host 驗收。不能只打開旗標假裝官方 binary 支援，不覆蓋官方 binary；保留可回退選擇。

验收：

- [ ] 顯示 backend、enabled、capability、ready、最後結果及未就緒原因；沒有端點或設備不支援時不宣稱啟用成功。
- [ ] 左／右／上／下各一次短移動及停止，保存匿名指令結果、操作前後影格變化與位置讀回（設備支援時）。影格變化需確認是鏡頭運動，不把貓咪移動當證據。
- [ ] 若缺少位置讀回，方向功能可單獨驗收；「保存隱私位置／恢復原位」不得宣稱已驗收，直到有可靠可驗證的定位能力。
- [ ] 停止指令與限時自動停止有安全界線；失敗／逾時顯示失敗，不造成連續移動；啟動、登入、重連不自行校準或轉動。
- [ ] 隱私位置与 C701 關閉待機是不同能力及可選操作；不能把轉向描述為停止錄影，也不能把待機描述為仍在監控。
- [ ] 只控制目前配對來源；遠端經既有已驗證控制代理，管理 API 不向 LAN／WAN 公開。登入、授權、CSRF 與秘密遮罩不退化。
- [ ] 控制期間不建立第二個攝影機 producer；移動期間行為／軌跡候選妥善暫停或重置，影音／環形快取保留。
- [ ] 正式 Host 重啟後控制能力仍可用，回退官方 backend 後影像正常，UI 明確顯示 PTZ 不可用。

## G3：最近來源與逐攝影機設定記憶

目標：保存來源清單與每台最近有效設定，直接 A↔B 切換，不要求重新登入、手填 RTSP 或重跑完整配對／profile sweep。

設定契約：一般設定保存 opaque source/device reference、類型、別名、本地／遠端／自動連線模式、region 驗證結果、profile、relay pairing reference、最近成功時間與 active source reference；秘密使用現有安全憑證保存。不可明文保存含密碼的 URL，也不可重用已過期的直接 YouTube 媒體 URL。

驗收：

- [ ] 前端顯示已保存來源、目前選擇、實際監控來源、待切換狀態，四者不混淆；RTSP／YouTube／Xiaomi 本地／已配對遠端皆可列出。
- [ ] A→B→A 恢復各自 profile、連線模式與攝影機配對；連續往復三輪，無需重新輸入設定，沒有遺留 producer／consumer。
- [ ] 重啟 Host 後自動恢復最近有效來源，不跳回 mock；來源暫不可用保留設定並退避恢復，不悄悄改成其他來源。
- [ ] 切換原子保存，失敗保留舊有效來源；崩潰中途重啟可恢復有效配置，不保存半完成狀態。
- [ ] 「免重新設定」不等於略過健康檢查。每次短查連線、session、配對與影格；只有憑證失效或配對改變才提示必要重新驗證。
- [ ] 本地優先／遠端優先／自動模式可選；自動模式依實際路由與一次有界影格驗證選路，不只靠私有 IP、ping 或米家裝置清單。失敗後最多依配置切換一次已配對路線，不搜尋／信任陌生中繼。
- [ ] 多瀏覽器只共享既有 ingest，不增加來源連線；Host 自動恢復不要求 Dashboard 登入後才開始監控。

## 階段與交付

1. 基線：核對正式 binary／manifest／PID、已保存來源与目前 stream；保存安全摘要，保留既有 session。
2. G3 設定契約與來源恢復：先建立穩定裝置識別，避免 G1/G2 控制錯來源。
3. G1 畫質：同來源 metadata 与 profile 切換，完成 026c02 實測。
4. G2 PTZ：隔離驗證通過後才部署可選 backend；不以 504 或 HTTP ACK 代替移動。
5. 發布：相關及全套回歸、秘密掃描、包完整性、升級／回退；正式 Host 三項驗收，最後至少 2 小時持續監控觀測。

證據規格：每項記錄 `passed/failed/not_checked`、版本/hash、匿名來源標記、時間、PID、producer／consumer 峰值與錯誤分類。家庭影格只存本機私人 data，不進 Git／發布包。報告只保存安全 metadata。
發布／完整完成門檻：正式 Host 的 G1–G3 必要項通過，已支援本地與已配對遠端路線各有證據；未測機型不得列為已驗收。測試套件通過與可攜包建好都不等於部署或實機成功。

## 可恢復進度

- G1：`in_progress`，本地同名 stream 替換後加入原始 metadata＋記憶體有效 JPEG 門檻；失敗嘗試舊 profile 回退並驗證，不再只靠 PUT 成功。前端分開顯示最近驗證來源規格與預覽縮放尺寸；正式畫質往返仍未驗收。
- G2：`planned`，既有隔離失敗需按目前 backend 重核，不宣稱硬體不支援。
- G3：`in_progress`。已修正 repository 的來源類型切換清空其他類型 URL，以及本地影片未保存問題；受保護來源 API 新增 validated remembered_sources，前端切換類型帶回上次設定並保留未保存表單。仍未完成逐攝影機清單／profile、原子 runtime 切換及正式重啟恢復驗收。
- 下一步：唯讀比對正式 Host、active source、已保存來源與 profile metadata，建立三項現況表；必要權限、人工登入或遠端配對才請使用者操作。

### 2026-10-10 第一輪實作證據

- 正式 `/healthz` 真實 HTTP 200，core_status=running；沒有重啟服務或切換攝影機。
- `tests/test_source_memory.py` 3 項通過：來源設定往復與重新讀取、秘密 legacy URL 不回傳、無效更新不破壞既有設定。
- `tests/test_phase3_source_mvp.py` 9 項通過；指定 Python 檔 Ruff 與 compileall 通過。
- 本輪只做 repository 修改，未建置／部署；瀏覽器實際表單往復與跨攝影機切換仍 `not_checked`，不能當作 G3 完成。
- G1 盤點發現本地 set_live_profile 只在 activate_stream 返回後就標 success，沒有與遠端相同的真實新影格門檻；下一輪需檢查 manager 本身是否驗證，修正成功／等待／回退契約。

### 第二輪 repository 畫質修正

- manager 原 activate_stream 確認只送 PUT，未驗證。新增 verify_active_profile，使用既有本地 RTSP 同名 stream，FFprobe metadata 與 FFmpeg 單張 JPEG 依序檢查；各程序 12 秒期限，JPEG 只留記憶體，stderr 不回傳。沒有新增攝影機來源，但會短暫增加本地驗證 consumer，峰值需實測。
- 缺少 verifier 不變更來源；有 verifier 時未取得有效影格不保存新 profile，回退也需驗證才能顯示已恢復。
- 更新即時頁標籤為「最近驗證來源原始規格」與「預覽解析度／更新 FPS」，避免把縮放後 640×360 宣稱攝影機原始尺寸；AI 分析尺寸與遠端 metadata 接線仍待補齊。
- 19 項 Xiaomi Phase3C2 與 3 項新增 verifier 測試通過；指定檔案 Ruff／compileall 通過。屬 Mock 程序回歸，不是家庭實機切換成功。未部署／替換正式 binary。

### 第三輪逐攝影機記憶與 PTZ 證據核對

- 新增 camera_preferences.py：以匿名 hash 保存每台最後驗證 profile 與 stream name，原子替換、嚴格欄位檢查；無帳號／DID／完整 Xiaomi URL。來源清單顯示 resume_available；既有有效設定可直接按接入，仍驗證本輪影格，不再要求先手動測試 SD。
- 3 項偏好檔測試、19 項 Phase3C2（含 service 重建後恢復 subtype=3）、10 項 go2rtc 相容測試通過；舊 sd/hd alias 保存時正規化。正式 Host 重啟與多攝影機往復仍未測。
- 原始 reports/ptz-026c02/latest-trial.json 回報 producer=1、consumer=1、PTZ HTTP504 response_timeout、move/stop 均 false、正式监控恢復 true。對照試驗工具，此失敗在 `GET refresh=true` 位置讀回，方向指令尚未送出；不能推論方向控制本身不支援。
- 既有實驗原始碼 RefreshPosition 等待 MOTOR_RESP，5 秒沒有回應就 504；下一輪分開診斷位置讀回與短方向／停止能力，所有控制需保留停止安全界線。未替換官方 binary。
- 全套回歸第一輪找到舊 mock profile 名稱相容問題，已修正並通過專項；不能把第一次全套執行宣稱為全綠，需完整重跑確認。
- 第一輪完整回歸 449 項／92.725 秒，1 項舊 hd alias 錯誤；修正後專項 10 項通過。完整重跑已啟動（本輪工具 handle `66946`），完成前不得重啟相同測試。完整 Ruff 與 compileall 已通過。

### 第四輪驗收進度

- handle 66946 已完成：449 tests／88.969 秒，OK。隨後新增控制鎖及兩項 PTZ 流程測試；4 項攝影機記憶、19 項 Phase3C2、2 項 PTZ 流程專項通過，最新完整 Ruff／compileall 通過。449 全套不是後續新增修改的完整驗收。
- 同一 setup service 的接入／畫質替換加非阻塞互斥鎖，第二次操作回 busy，不建立新來源。遠端接入與其他 mutation 的互斥覆蓋仍待確認。
- 隔離 PTZ 工具新增明確 opt-in 的 relative-without-position 路線；僅 refresh HTTP504 response_timeout 時可繼續一次短方向＋停止。預設仍停止；方向 HTTP ACK 不宣稱實體移動成功，標 visual_verification_required。尚未實機執行。
- 本輪唯讀確認：正式 Host port8000（0.0.0.0）PID14004、go2rtc 本機1984／8554 PID23424；隔離21984／28554未占用。此為時間點快照，不是後續程序身分證明。
- 執行上下文 is_admin=false；正式 SYSTEM 隔離試驗需要核准 UAC，不能自行繞過。實驗 binary hash 為工具固定值 `3AFA1B357D80E49A8149C34FB663BA2E0803709172D6B98AA0A25F09FB8F33CF`，正式 binary 未替換。
- 預覽 status 新增既有 AI 輸入尺寸／實際分析 FPS，畫面與預覽規格分層；沒有額外拉流。

### 第五輪發布前與實機前置

- 最新完整回歸 452 tests／89.906 秒：OK。PowerShell 試驗 wrapper 語法通過；wrapper 支援顯式 relative-without-position 與時間戳報告，不覆寫既有失敗證據。
- 只讀 SYSTEM 排程查詢實際 stderr 為 `ERROR: Access is denied.`，不能記為排程不存在；已安裝 Host／go2rtc 路徑與父子關係一致。正式 026c02 source 的 config 驗證通過、Session 存在，當時 producer=1／consumer=0，並不代表持續收流成功。
- r73 候選包已啟動建置，工具 handle `50766`；本輪未啟動第二次建置。完成後需 verifier、包／repository hash 比對與秘密掃描，尚未宣稱發布通過。
- 已向使用者詢問當下能否核准 UAC；沒有自行停止 SYSTEM、移動鏡頭或覆寫正式 binary。待使用者可核准時，正式 UAC 執行隔離試驗，保留 source/model/hash preflight。

### 第六輪可恢復建置監看

- 建置 handle 50766、實際工作 PID10428 持續存活；CPU 從21.67→34.41秒、read_bytes達3454369667，write_bytes為2050900230。已完成複製後進行 manifest hash，不能因沒有 stdout 就重啟建置。
- 暫存候選包七項關鍵檔案與 repository SHA-256 一致（來源設定、setup service、camera preferences、device discovery、Web state、live／xiaomi setup templates）。這只證明指定檔案一致，不代表完整包通過 verifier。
- 尚未收到使用者能核准 UAC 的回覆；正式服務保持原狀。完成包驗證與後續實機驗收仍是原長期目標，未縮小為僅建置成功。

### 第七輪候選包驗證

- r73 建置 handle 50766 已正常完成；候選目錄 `dist/MeowKeeperPlus-portable-2026-10-10-r73`，共 19,268 檔。
- 使用候選包內附 Python 執行 verify_portable，exit code 0，status=passed、private_data_present=false；YOLO SHA-256 為 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`。
- 272 個指定文字檔的秘密樣式掃描命中三檔；逐項核對為 probe_common 的固定遮罩字串、COMPATIBILITY 的帳密欄位範例、SECURITY 的遮罩示例。此範圍未发现真實秘密，不宣稱所有二進位／任意資料皆經秘密掃描。
- 最新完整回歸為 452 tests 通過，Ruff／compileall 通過。上述只代表程式與候選包驗證，不代表正式 Host 已升級。
- 正式部署、畫質實際往返、PTZ 實體移動／停止、跨攝影機往復與重啟恢復、兩小時觀測仍為 not_checked。正式 binary 未替換，未擅自停止 SYSTEM 監控。

### 第八輪遠端偏好與併發隔離

- 遠端 activate_remote 加入與本地 activate／set_live_profile 相同的非阻塞控制鎖；忙碌時不做來源工作。
- 遠端 profile 僅在 success=true 且 video_verified=true 時保存。遠端偏好使用獨立匿名 hash namespace，不能覆蓋本地 stream_name／profile；未驗證的遠端切換保留上次有效值。
- 4 項 CameraPreferences、5 項 relay pairing 測試通過；namespace 修正後重跑 5 項通過（20.103 秒），相關 Ruff／compileall 通過。仍屬隔離測試，不是遠端攝影機實測。
- 正式 healthz HTTP200、status=ok、core_status=running；執行上下文仍非管理員。本輪 listener 查詢未取得結果，不沿用舊 PID 當本輪證據。
- 此修改晚於 r73 建置，r73 不含第八輪程式變更；後续候選需重新建置，不改已驗證包 manifest。遠端接入自動恢復上次 profile、連線模式與正式重啟驗收仍未完成。

### 第九輪遠端畫質恢復契約

- 遠端重新接入若有已保存 profile，先經既有認證控制通道恢復，要求 success=true 與 video_verified=true，再經 Host 端 JPEG 檢查，最後才切換監控來源；控制或影格失敗不切換。配對 stream name 改變時拒絕沿用舊畫質，避免控制錯誤來源。
- 遠端切換成功把實際恢復的 profile 傳給 source_switch 並保存 wizard state，不再全部寫成 remote。沒有已保存 profile 的舊配對仍沿用既有 JPEG 驗證流程。
- 介面不再將所有驗證結果一律稱為 SD；僅有記憶而未有本輪 quality 時，明確標示接入會重新驗證，而非 SD 已驗證。
- 6 項 relay pairing、23 項 Web／Tray 專項通過；最新完整回歸 453 tests／89.314 秒通過，完整 Ruff／compileall 通過（handle 91427 已完成）。這些測試不是正式攝影機／遠端連線驗收。
- 變更仍為 repository-only，r73 不包含本輪；正式 UAC、PTZ 實體移動／停止、畫質往返與 Host 重啟的實機驗收待完成。沒有重啟正式 Host，也沒有替換官方 go2rtc。

### 第十輪 PTZ 影格證據

- 隔離試驗的唯一 FFmpeg consumer 改為 2 FPS、有界尺寸的單張更新 JPEG，不另開消費者；移動前必須取得有效 JPEG，停止後須取得時間更新的 JPEG，舊影格不能通過。
- 前後 JPEG 保存至被 Git 排除的 `data/ptz-trials/<隨機識別>/`；報告只含局部參照、hash、尺寸與平均像素差，不包含私人影像或來源秘密。
- 畫面差異可能來自人／貓／光線，不能自動證明鏡頭運動；即使位置回應成功也保持 visual_verification_required，不再直接 trial_complete。必須檢視場景及方向／停止證據才能完成 G2。
- 4 項 PTZ 流程測試通過；首輪 Ruff 發現巢狀 with，修正後通過。相關 compileall 通過。
- 本機內附 FFmpeg 真實合成影片驗證相同 JPEG 更新参数：exit0、JPEG 20,296 bytes、只產生一個输出檔。第一次 CLI 引號失敗，修正 PowerShell 傳參後通過；不是攝影機移動實測。
- 此輪未執行 UAC／正式程序停止／鏡頭移動。r73 不含第八至十輪修正；正式驗收仍未完成。

### 第十一輪接入保存門檻

- 核對 run_host：啟動時已由 _source 讀取保存配置、_prepare_saved_go2rtc 只恢復既有設定，缺少設定不建立空 Session、不改 mock。此為程式證據，正式重啟驗收仍未執行。
- 發現 switch_xiaomi_source 原本在非同步 start_monitor 返回 starting 後立即保存；已增加最多 20 秒的本輪 JPEG 門檻，要求仍為指定來源、檔案時間新於啟動且通過 JPEG verify。錯來源／舊影格／核心 error 不通過；失敗進入既有停止新來源、恢復前來源與配置的回退。
- 2 項新影格門檻測試及 9 項 Phase3B 啟動器專項通過；相關 Ruff／compileall 通過。不是 SYSTEM 啟動或真實來源往復驗收。
- 本輪唯讀 healthz HTTP200，status=ok、core_status=running，is_admin=false。正式 Host 未重啟，未套用最新 repository；部署與 PTZ 實測需正式 UAC 授權。第十一輪修改亦不在已驗證的 r73 包內。

### 第十二輪候選收斂（執行中）

- 已啟動同一個命令 handle 54485：完整 unittest → Ruff → compileall → r74 build，各階段失敗會停止。新輸出目錄 `dist/MeowKeeperPlus-portable-2026-10-10-r74` 啟動前不存在，不覆寫 r73。
- 本命令尚未完成；不得把開始建置當作 r74 已通過。後續必須觀察同一 handle，完成後以包內 Python 執行 verifier、關鍵檔案 hash 比對及秘密掃描。
- 沒有啟動 installer、停止正式程序或轉動鏡頭；正式實機門檻保持不變。
- handle 54485 已回報最新完整回歸 457 tests／90.107 秒 OK、Ruff 通過，compileall 成功後進入建置；整個命令仍存活，r74 尚待建置與 verifier，勿啟動第二次建置。
- 本輪再次觀察同一 handle 54485 仍存活；唯讀程序確認建置 PID1516、repository .venv executable、CPU20.58秒、read_bytes3027967952、write_bytes2050913188，不能將暫時無 stdout 當作停止。
- r74 staging 的八項關鍵來源檔已複製且與 repository SHA-256 相同（run_host、PTZ trial、setup service、camera preferences、source_config、xiaomi setup／live templates、source.js）。這不是完整 manifest verifier；仍待同一建置程序完成。
- handle 54485 已正常完成 exit0，r74 最終輸出 19,268 檔。包內 Python 的 verifier（handle 47261）已完成 exit0、status=passed、private_data_present=false；模型 hash 與 r73 相同。
- r74 的 src／tools／docs／scripts 共 275 文字檔秘密樣式掃描，三個命中分別確認為固定遮罩、帳號／密碼欄位範例及遮罩示例；此範圍未發現真實秘密。不是所有二進位的秘密掃描。
- r74 為待部署候選，不含已啟用／已驗收的 MISS PTZ backend，不能僅安裝此包就宣稱方向控制成功。正式 UAC、PTZ 實體影格檢視、畫質往返與重啟恢復實測仍未通過。

### 第十三輪正式 UAC 升級等待

- 已核對 r74 installer：系統模式自行走正式 RunAs，升級目標固定 `C:/ProgramData/MeowKeeperPlus/App`，先验证 manifest，停止已驗證安裝程序後才刪除舊 App；Data 分離保存。沒有改動防火牆或官方 go2rtc binary 選擇。
- 已透過 Windows 正式 PowerShell 啟動 r74 `Install-MeowKeeperPlus.ps1 -Upgrade`，可見互動視窗；啟動器 PID19728。僅 Windows 使用者可核准 UAC，沒有自行核准或繞過。
- 精確腳本參數比對確認 PID19728 存活、視窗 Windows PowerShell 且 Responding=true。初次寬鬆匹配把診斷命令自身算入，已改用完整獨立參數匹配排除；不是多個實際 installer。
- 當時 installed manifest 尚不等於 r74，沒有確認高權限子程序或安裝成功。已詢問使用者是否看見／核准 UAC；保持等待該人工操作，不重新開啟第二個安裝器。後續先觀察 PID19728、manifest 與正式 health，不能僅根據視窗仍在（NoExit）判定安裝工作仍在執行。

### 第十四輪等待邊界核對

- 再次精確程序檢查只有原 r74 PowerShell PID19728，CPU0.734375秒；installed manifest 仍不等於 r74，healthz HTTP200／running。沒有取得真正高權限 installer 或驗證子程序的存活證據；NoExit shell 存活不能作為安裝仍工作之證明。
- r74 installer 已確認 UTF-8 BOM；五項目前使用者安裝 marker 全數不存在，排除這兩項啟動前阻礙。沒有修改、刪除或停止任何程序／配置。
- 仍待使用者回覆 UAC／既有升級視窗狀態。不要重試相同啟動或再建包來代替權限確認；下一個操作是查看原視窗或核准 Windows UAC，再重新查 manifest 與正式 runtime。

### 第十五輪人工操作阻塞確認

- 再次實際核對：執行上下文 is_admin=false，installed manifest 仍不等於 r74；只查到原 PowerShell PID19728，未確認高權限安裝工作。正式 healthz HTTP200／running。
- 同一 Windows UAC／原升級視窗狀態需求已連續三輪未解除；不是仍運行中的建置或 verifier，也不能以 NoExit shell 存活宣稱有效安裝等待。沒有重複啟動或嘗試繞過權限。
- 本階段標記 blocked，保留完整 G1–G3 目標與已驗證 r74，未宣稱完成。需要使用者核准 UAC，或提供既有升級視窗的錯誤，以解除正式部署／實機控制驗收阻塞；收到新證據後恢復診斷，不重做建置。
