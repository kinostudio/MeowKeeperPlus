# V4.3 分析／通知效能與分母（部分完成）

## 送出證據與時序

本機HTTP helper使用標準HTTP／HTTPS connection，完整request寫出後只在stdout輸出不含秘密的NDJSON marker。無redirect、無proxy，HTTPS沿用標準TLS驗證；Key與圖片仍只走stdin，不進參數或檔案。父程序在逾時／非正常退出時可保留marker證據；沒有marker不能推定未送出，保存unknown。這代表本機完成送出，不是遠端已分析、收費確認或使用者已讀。

`ProviderReply`是本機adapter建立的dict子類別，送出／HTTP／純HTTP耗時放在屬性，不接受模型JSON自己宣告成功。`VisionFailure`保存同樣的安全 metadata；明確的本機preflight／skip可標not dispatched。模型回傳transport欄位不會因此被相信。

`SemanticResult` payload增加可選transport證據；歷史沒有證據保留未知，不補造。`provider_call_ms`與純HTTP `api_response_ms`分開：後者在helper中量測open／request／完整response讀取，不包含本機金鑰讀取、額度交易、子程序啟動與模型JSON驗證。response_received_at仍是worker收回結果的UTC時間；精準HTTP回應UTC與完整跨job瀑布圖尚待接線，不能把目前時間欄位當成全部階段已完成。

`SemanticFuture`在排隊時即有opaque job ID，同ID寫入語意revision與通知trace；可查明本機文案是否未採用該次有效候選，而不是把後來人工重跑的結果算到舊通知。

## 合併統計與限制

新增登入保護的 `GET /api/v1/analytics/semantics?limit=100`。最多查最近200筆model-origin cat_detected事件，排除simulated/test；最多讀2000筆分析及500筆通知trace。資料截斷、格式損壞時明確標partial，不產生完整成功百分比。沒有紀錄顯示null，不補0秒或100%。

- API有效成功率：completed且可確認request dispatched／已確認request dispatched。未知證據與確認未送出另列，skip不是已發送失敗。
- HTTP等待時間：只以可確認已送出的、具有HTTP elapsed證據的回覆計算。沒有完整回覆的timeout不補一筆假的response latency。
- 候選文案未採用：同job且有效非空候選可對應通知時，本機文案次數／可對應次數。另列late、缺少時間、未對應紀錄，不推定全是API慢。
- Telegram接受率：有效message_id的上傳嘗試／已記錄上傳嘗試；有省略明細或資料截斷時不稱完整率。不是所有收件人的到達率或手機播放率。
- 平均／p50／p95：非負有限有效樣本，nearest-rank百分位；附sample_count，時間重疊不可相加。

目前跨服務／模型合併，只反映已保存工作，不能當成單一模型對照或全部請求的計費帳單。未持久化的terminal／worker crash、完整queued job恢復、per-provider/model分組、窗口回放與真實性能仍待完成。

## 等待後不再讀取身分資料

通知在排隊前先準備本機文案。AI grace結束後以純文字合併候選，不重讀SQLite／寵物照片檔案，不為每個收件者重等；原影片metadata和reply_markup路徑不變。若未提前完成的Future返回已超過grace，只用本機文案，保留實測超時值，不截成設定上限。OS排程延遲不等於程式能保證硬即時；本版仍需Windows負載下的實機驗收。

## 驗證

100筆合成SQLite：90確認已送出、70有效成功、5未送出、5未知；API成功分母70/90，HTTP樣本80筆、平均41.75ms、p50=40ms、p95=86ms。grace樣本100筆、平均495ms、p50=490ms、p95=940ms；有效候選對應70筆、未採用35、可確認晚到35。全是fixture，不是真實Provider效能。

實際loopback HTTP子程序驗證200／401／403／429／500／400、逾時、redirect與無效答案；沒有外部API Key或家庭影像。模型JSON不能偽造dispatched證據，Future job ID與revision一致。純HTTP延遲與worker呼叫分開；0／5／10秒與晚到丟棄、post-grace不讀身分資料有可控時鐘測試。

隔離analytics頁確認未知分母不顯示成功率、少量／缺少耗時的文字正確。390px：page375、統計內容client/scroll305；360px：page345、統計內容client/scroll275，無水平溢出，browser error log為空。原行為查詢的JavaScript UTC Z字尾不相容Python3.10已做最小正規化，實際頁面回到「尚無行為紀錄」，沒有放寬時區與31天限制。
