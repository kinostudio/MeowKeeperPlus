# r91 運算裝置選擇與 Tray 401 修正

日期：2026-10-12。

## 運算設定

AI 頁新增自動／CPU／Intel OpenVINO 內顯／NVIDIA ONNX CUDA 外顯。保存後重新啟動服務套用，設定持久化，下次啟動沿用；基本找貓與 V0.2 tracker 都送入同一設定。實際 backend 與設定選項分開顯示，指定 GPU 無法啟用仍安全降級 CPU，不把選項當作成功證據。

不安裝或變更驅動、系統 Python、CUDA；不新增模型／攝影機連線。只有 AI worker 的 OpenCV 前處理執行緒限制為1，避免過度平行；torch 與 ORT 原有單執行緒策略保留。此修正不是整機 CPU 必定降低的保證，解碼、預覽與錄影仍使用 CPU；MX250 與 Intel 同設定長測需在目標筆電確認。

## Tray 安全狀態

根因：Session-only Host 的 API token 不會提供給另一個 Tray 程序，瀏覽器登入 Cookie 也不由 Tray 沿用。因此舊 Tray 每2秒 health成功後仍查 protected status，持續401。

修正採最小權限：沒有 token 時只使用現有 health 的 core_status，明確標「詳細狀態未授權」，不再呼叫 status。start/stop/restart/診斷改引導登入控制台，明確說尚未執行；不是自動授予管理員權限。有已授權 token 保留完整 API 流程。完整關閉仍為明確確認後的 OS scoped helper，未降低其權限範圍。

`/api/v1/status`、影片、設定及控制仍需 auth／CSRF；不新增公開詳細狀態端點，不共享或保存瀏覽器 Cookie。不宣稱此修正讓未授權 Tray 直接控制核心。

## 磁碟語意

analytics 新增說明：程式尺寸包含內附 GPU 依賴，選 CPU 不會縮小包；總占用＝程式＋Data，不能再把總線與兩條分項相加。r85約1.95GiB、r90約4.19GiB是建置 manifest 比較，並非遠端電腦的實際 Data 量。

## 驗收與發布

需驗證所有選項持久化、Host接線、非法值拒絕、無token不查status、有token完整狀態、登入保護未降低、前端選項可見。完整測試／Ruff／compileall及包校驗另保留reports/r91結果。

本輪不安裝、不重啟正式 Host。目標筆電 CPU改善、MX250／Intel切換後長時間穩定、手機UI仍需部署後實機驗收。
