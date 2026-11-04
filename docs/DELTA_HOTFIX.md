# 真正的檔案差量更新

## 契約

- `schema=2 / kind=file_delta` 的 Ed25519 簽章清單綁定官方 repository、基準版本、基準 build manifest SHA-256、目標版本、差量 ZIP 雜湊及逐檔新增／修改／刪除清單。
- 下載 ZIP **之前**，核對已安裝基準與每個檔案的大小、SHA-256、路徑及重新解析點。版本或內容不同即停止；沒有自動完整包回退，也不以忽略差異方式更新。
- ZIP 只包含實際有變更的檔案及目標 build manifest。runtime、CUDA、FFmpeg、模型未變時，不傳送這些檔案。這是檔案差量，不是二進位區塊差量；大型依賴本身變更時仍需下載該檔案，發布摘要必須列出。
- 本機從既有 App 重用未變檔案，建立完整候選目錄，再驗證目標 manifest；同磁碟優先硬連結，不能連結才複製。不就地修改執行中的 App，也不把 Data 放進更新包。
- 網路下載量縮小不代表磁碟／驗證時間歸零；仍需完整候選、舊 App 與持久 Data 快照的空間。換版需要短暫停止監控，不是零停機。

## r116 的一次性過渡

舊版更新器不認識新協定，因此提供小型 `MeowKeeperPlus-r118-delta-bootstrap.pyz`。使用者在主機本機管理員 PowerShell 核對其固定 SHA-256，使用**現有內附 Python**執行；不需要安裝 Python、pip 或重新下載 runtime。

過渡工具只接受固定官方版本網址，使用已安裝的官方公鑰驗證差量簽章；先驗基準，再下載 ZIP。已驗證的新 worker／驗證器置於 Administrators／SYSTEM 保護的 Updater 目錄，只有使用者明確 `--apply` 才呼叫受控換版流程。不自行觸發 UAC。

本次 r116→r118 包的基準為官方 r116 加上一份已知、已公開的 `src/meowkeeper/app/web/state.py` 修正；該檔案的確切大小與 SHA-256 另列於簽章 `base_overrides`。這不是允許任意修改：其他內容或未包含該修正的 r116 會拒絕套用。基準不符需另外製作對應的簽章差量，不自動下載完整包。

後續版本由既有固定 SYSTEM 更新工作處理新協定；Dashboard 仍須本機登入、CSRF 與明確中斷確認。不接受任意下載網址、命令或 verifier 路徑。

## 失敗回復

重新使用安全更新交易：停止已驗證的舊程序、備份 Data（含 SQLite 一致性快照）、保存 Core／Tray 工作定義與舊 App，再切換及檢查健康。失敗嘗試恢復 App、Data 與排程；保留失敗現場，不能只恢復程式卻宣稱資料已回復。

受控更新明確跳過選用測溫驅動安裝提示，不在 SYSTEM Session 0 顯示對話框，也不重新安裝驅動；既有測溫背景工作保留。

## 驗收邊界

`tests/test_delta_updates.py` 驗證僅變更檔案、簽章／基準／路徑／ZIP 防護、已知修正基準、無完整包回退、下載前拒絕及 worker 重建；`tests/test_hotfix_transaction.py` 驗證隔離檔案與 SQLite 回復。發布前另做真實候選包重建、逐檔完整性與包內 HTTP smoke。

上述不等於正式 Host 的 SYSTEM 排程、UAC、真實停止／啟動、斷電恢復或正式資料回滾驗收。正式安裝由使用者執行，未實測項目必須標記 `not_checked`。
