# V4.3 原生影片與逐軌跡多選（施工中）

## 原影片與候選文字

`EventClipService` 保留原 send_video 路徑、相容格式、width/height/duration與fallback。候選語意使用「剛剛可能在…」；不新增查看影片主CTA、不重傳晚到AI的影片。影片sendable後的5秒／0–10秒等待維持原接線；完整fake-clock時序與真實Telegram仍待驗收。

## 已接入逐Track三步

1. 身分：依寵物registry動態產生名字、其他貓、不是貓與無法確認；未知不批准特徵學習。新的逐Track入口只保存身分草稿，最後確認提交才寫入人工identity／cat_presence修訂。不是貓略過動作題，但仍需確認。已發出的舊be:回報保留相容處理，不重新解讀舊歷史。
2. 動作：checkbox狀態多選，可清空；「無法判斷」與其他動作互斥。不是貓不繼續問。此未知選項依最新V4.3正式規格加入，取代早期要求沒有未知選項的測試假設。
3. 確認：更新同一則回報文字，列出動態貓名與所有已選動作，說明整片級、不是精準起迄。必須按確認提交才將動作草稿標submitted；可返回或取消。舊editing選單的done第一次只進confirming，不偷偷直接提交。

文字更新使用官方 [editMessageText](https://core.telegram.org/bots/api#editmessagetext)，保留reply_markup，不重新傳影片。按鈕更新失敗不撤回已落庫資料，也不謊稱更新成功。

## 授權與歷史

沿用poller的私人chat、白名單、feedback opt-in與recipient receipt。草稿綁定reviewer/message/event，保存本次identity/name快照；途中身分更改拒絕提交。預設24小時有效，過期不提交；callback/update重送不重複勾選或新增history。所有操作歷史保留；取消／過期不算submitted。

schema21新增staged_identity、baseline修訂摘要與原通知message/update識別。新第一題、動作與確認共用一份草稿及同一則文字訊息；可返回身分，重新選身分會清除原動作，防止把一隻貓的答案轉給另一隻。取消／過期不寫人工annotation revision。相同原update重送不重開選單；途中出現其他身分修訂會拒絕舊草稿提交。新提交將身分與tag history放在同一BEGIN IMMEDIATE交易中，無隱含學習同意。

第一題TTL沿用原通知收件時間，預設24小時，進下一題不重置。原生影片過期不能建立新的behavior receipt繞過TTL；所有已建立第一題均再驗證期限。schema20草稿保留原快照與歷史；未綁定身分的舊editing/confirming標superseded，不推造原身分。

Dashboard同一media event透過media_event_tracks讀取已submitted的Telegram tags歷史；不把草稿當確認，也不填精準時間。兩隻貓各有獨立Track草稿，名字不寫死。

原生影片的 reply_markup 現在優先列出逐Track回報入口。只有同 source、同 track、與事件影片時間窗相交且唯一的 behavior event 才能加入；有歧義則保留影片級回報，不能猜選一筆。點擊時以已保存的原影片 recipient/message receipt 再核對映射，才建立 behavior receipt 並開啟身分題。不重傳影片。局部測試涵蓋兩條軌跡從原影片receipt進入既有多選確認流程，以及不同來源／時間、重複事件、錯誤訊息／收件者、重複綁定拒絕或冪等。編號與真實影像框一致性仍待驗收。

## 待完成的真實驗收

本輪使用FakeTelegram／SQLite，不是真實Bot或本版部署。原生影片入口、第一題TTL、從confirm返回身分、原通知重送冪等與按鈕revision token已通過局部測試；完整多軌跡順序導航、影像框編號一致性、程序中斷時的送達回條恢復與真實Telegram點擊仍待完成。不得把本輪三步局部測試當完整端到端完成。

## 2026-10-15：選單版本與過期點擊

schema23對新三步草稿加入callback_revision；每次接受的操作在BEGIN IMMEDIATE交易內遞增版本，並連同操作存入追加歷史。按鈕攜帶顯示當下的版本，雙擊或延遲的舊按鈕不切換選取、不提交、不新增人工標籤，而是以同一則訊息回顯最新狀態。「相同update/callback＋相同payload」仍先按冪等重送處理；同一識別改帶不同版本則拒絕。

舊非staged流程維持相容；舊staged無版本按鈕第一次點擊只刷新，不把舊草稿丟掉。22→23 migration可重跑，保留歷史；被新選單取代的舊選單不重新顯示可操作按鈕。版本到999999後過期，避免超過Telegram 64-byte callback限制。版本是防誤操作資料，不替代收件者、訊息、TTL及opt-in授權。

19項逐軌跡測試涵蓋雙擊、新update使用舊版本、重啟後重送、舊身分/提交、兩個真實SQLite連線競爭、schema22欄位遷移與最大按鈕長度。另19項poller與8項Dashboard review測試passed；沒有重傳原影片，也沒有執行真實Bot操作。UI網路延遲可能暫時呈現較舊選單，舊版本點擊仍不會改資料，會刷新。
