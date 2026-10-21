# 可直接貼入 Figma 的重設計提示詞

請先附上 `FIGMA_DESIGN_HANDOFF.md` 與 `figma/design_tokens.json`。本文件是設計生成需求，不是部署程式或功能完成證明。

---

請為「喵喵守護者+ / MeowKeeper+ — Local AI Pet Guardian」設計完整繁體中文桌面／手機 Dashboard。它是在Windows本地24H背景運行的單一來源寵物AI監控工具，不是多攝影機雲端NVR。請依附件的**現有UI功能清單**保留所有操作，並把新增建議標為Proposal，不捏造未實裝功能。

風格：米白、卡其、低彩度大地色，安靜可信賴的居家工具；清楚、精緻、耐用，而非娛樂性展示頁。以Webby／Awwwards／FWA的設計完成度為參考，不宣稱已達得獎評審結果。字體使用Noto Sans TC；不要添加雲端AI、付費牆、聲音診斷、九宮格同時監控、社群或不在規格內的功能。

第一屏要能看懂三件事：收到新畫面嗎、AI正在分析嗎、Telegram通知可用嗎。背景服務running不等於攝影機已接通；未知、候選、實驗性與未驗證狀態要清楚。長篇技術資料放次級展開區，但關鍵限制必須就地可見。

請交付：

1. IA導覽與主要流程；來源與攝影機合併，Xiaomi是子流程，不重複側欄。
2. Design foundations：colors、typography、spacing、radius、grid、icons；保留米白大地色，不換科技藍黑。
3. Components與variants：shell/nav、狀態卡、設備卡、事件卡、影片toolbar、PTZ pad、位置圖、secret field、helper/error、收件者列、pet card／feature status、圖表／數值表、dialog、toast、empty/error states。
4. 桌面1440/1280、平板768、手機390/360版型，Auto Layout與constraints；不靠固定高度，中文長文要wrap。
5. 所有現有頁面：首次設定、登入、總覽、即時、事件、趨勢、來源、小米精靈、AI／區域、寵物、Telegram、Tailscale、診斷、日誌、備份／區網。
6. 正常、loading、empty、offline、reconnecting、unsupported、failed、stale、disabled、experimental、saved_pending_apply等完整狀態。
7. 可點原型：首次接入、來源切換、Telegram啟用／測試、寵物編輯／重試特徵、查看事件、隱私／恢復、備份還原、區網啟用。
8. 交接表：每個設計元件對應現有route／DOM ID／功能；Proposal列為需接線，不當成現成能力。

即時畫面：影片主區＋旁側/下方能力面板；截圖、前15秒、全螢幕、短步上下左右及停止容易找到。分開顯示「來源原始規格」「預覽」「AI實際分析」。畫質代碼不是固定像素，支援按鈕受當前裝置能力限制。位置圖只是有效讀回0～101座標的線性參考，不是房間平面圖、角度或可拖曳定位。未讀回不得畫假位置。

寵物與回饋：多貓分別確認，當次框號不等於永久身分；名稱動態取登錄資料。照片保存與特徵建立分開；結果標實驗性。Telegram回饋自願參與；先個體／數量再逐貓行為，步驟不能合成一個整片標籤。

安全與操作：本機管理者不是小米帳號；秘密不回填，示例不使用真實帳號／IP／Chat ID／token或家庭照片。保存與套用分開；停止／重啟／還原需說明影響。完整關閉停止服務但不關瀏覽器分頁。Dashboard離開不停止24H監控。

UI完整度：主要CTA每區一個，清楚hierarchy、keyboard focus、至少44px touch target、狀態不只顏色、正文對比目標4.5:1、reduced-motion、不新增持續動畫或重型效果。日期、單位、採樣、缺口與過期狀態清楚；圖表保留數值表。所有難懂設定附「用途／調整影響／何時用」白話說明，不只tooltip。

不要輸出僅有漂亮總覽的單頁。先給完整IA與components，再生成全頁面和錯誤狀態。新增照片相簿、ROI拖曳、事件filters、GPU selector、帳戶／登出等需標Proposal，依附件現況決定，不自行假設API已能完成。

---

## 使用方式

若工具有輸入長度限制，分三輪：①附件與IA/Foundations ②總覽/即時/來源/Xiaomi/通知 ③寵物/AI/統計/維護＋所有狀態。每輪沿用同一元件庫，不各頁另設色系。

Figma tokens JSON 是參考資料，並非特定token plugin可直接匯入的承諾；品牌SVG可直接作視覺素材。此交付不含`.fig`原生文件，也未向任何外部Figma專案上傳私人repository或資料。
