# MVP Phase 3B 工作紀錄

本紀錄是可恢復的 repository-local 進度，不代表尚未驗證的外部硬體功能已完成。

## 進度

| 階段 | 狀態 | 證據／下一步 |
|---|---|---|
| launcher_reproduction | completed | `reports/phase3b/launcher-before.stdout.log` 已重現編碼與 CMD 解析錯誤 |
| launcher_rebuild | completed | ASCII-only CRLF CMD → UTF-8 BOM PowerShell → `tools/launcher.py` |
| runtime_bootstrap | completed | `.venv` import probe 通過；只在缺少時執行 `.[runtime,web,tray]` |
| host_start | completed | launcher／Uvicorn 實際 `/healthz` 200，含 port 8131／8132 smoke |
| browser_open | completed | Edge headless 實際產生 setup/login/dashboard/events/mobile PNG |
| dashboard_interaction | completed | FastAPI route、HTML、static 與實際 Edge 截圖通過 |
| tray_smoke | completed | pythonw PID 36088；真實 pystray backend 8 秒 smoke；退出後 Host 8132 仍 200 |
| media_timeline_fix | completed | session 順序時鐘修正，2 秒／5 秒本地快速 ingest 測試通過 |
| full_verification | completed | 115 unittest、Ruff、compileall、JSON／UTF-8、CMD bytes、官方 go2rtc process smoke 通過 |
| delivery | completed | Phase 3B 報告、進度 JSON 與限制清單已保存 |

## 追加整合階段

| 階段 | 狀態 | 說明 |
|---|---|---|
| tray_assets | completed | `assets/tray/meowkeeper-{ready,waiting,error}.ico`，16～256 多尺寸 |
| tray_runtime | completed | pythonw 入口、動態 tooltip／圖示與診斷選單 |
| tray_visible | blocked | 需互動式 Windows desktop session 才能保存真實截圖 |
| go2rtc_download | completed | 官方 `AlexxIT/go2rtc` `v1.9.14` Windows x64 release 已取得 |
| go2rtc_verify | completed | ZIP／EXE SHA-256 已寫入 manifest 並啟動前驗證 |
| go2rtc_start | completed | 真實程序 localhost API 1984／RTSP 8554 start/health/stop |
| xiaomi_setup_ui | completed | Dashboard `/cameras/xiaomi/setup`、CSRF API 與可恢復狀態檔 |
| xiaomi_waiting_login | blocked | `WAITING_FOR_USER_XIAOMI_LOGIN`；不得以 Mock 取代真實登入 |
| xiaomi_device_discovery | pending | 登入後才可取得真實裝置 |
| xiaomi_profile_probe | pending | 需真實裝置串流才能驗證 profile |
| xiaomi_source_activation | pending | 需選定真實裝置與 profile |
| xiaomi_live_preview | pending | 不以 Mock 取代真實畫面驗收 |
| xiaomi_event_clip | pending | 依唯一 ingest 的實機畫面驗證 |
| portable_build | blocked | 缺少建置者提供的官方 embedded Python `runtime/python`；`build_portable.py --dry-run` 已安全拒絕產出不完整包 |

## 工作原則

- 每個階段需保存最後成功命令、錯誤及下一步到 `reports/phase3b-progress.json`。
- `not_checked` 只用於缺少真人秘密、外部硬體或確實沒有桌面 session 的項目。
- 不因 Xiaomi 尚未登入而跳過可獨立完成的 launcher、Host、Tray、go2rtc 與媒體驗收。
