# 技術與模組

## 可行性與交付範圍

Windows 10／11 上，獨立桌面聊天室與明確選定視窗的擷取可實作；畫面辨識與自主留言則需要視覺 API、節流、去重與角色協調，不能只靠 UI 完成。依委託書第十四節，Phase 1 僅完成桌面；使用者於 2026-10-10 授權 Phase 2/3 後已接預算與模型流程，詳見 [PHASE2_3.md](PHASE2_3.md)。人格仍以原始後台為準。

PySide6 適合獨立可縮放視窗、繁中／日英文字、非同步擷取與 Windows 打包。Python 便於後續串接模型與 SQLite；相比 web 桌面框架，此階段少一套瀏覽器與 IPC 層。依賴版本固定，平台限定 Python 3.11–3.13。

WGC 更適合 Direct3D／遊戲，應作為後續擷取 backend 的優先研究目標。Phase 1 的 PrintWindow 不要求 WinRT build chain，方便先完成一般視窗驗收；渲染相容性有限，不能視為所有遊戲已支援。

## 目錄

```text
src/our_little_days/
  app.py                  啟動、CLI 與角色設定錯誤
  ui.py                   主視窗、訊息、控制狀態與 Qt 擷取工作執行緒
  models.py               角色、視窗、影格、監看狀態、台灣時間
  capture.py              Windows／不支援平台／明確展示模式 backend
  win32.py                HWND 列舉、PrintWindow、GDI 資源生命週期
  storage.py              SQLite 交易、永久角色與關係、聊天／事件／長期記憶、備份還原
  memory_ui.py            分世界線事件／記憶管理，確認還原
  data/characters/*.json  十三份正式設定與各自保留的原文
  data/apartment.json     全域規則、初始世界線與原文雜湊
scripts/
  smoke_ui.py             真實 Qt UI 冒煙檢查、日夜截圖與報告
  windows_entry.py         支援凍結執行檔的啟動入口
tests/
  test_models.py          資料與日夜邊界
  test_ui.py              控制流程與訊息互動
  test_windows_native.py  Windows 真實視窗整合測試（Linux 跳過）
docs/
  ACCEPTANCE.md           Windows 人工驗收
  ARCHITECTURE.md         本文件
```

## 擷取與取消

`STOPPED → RUNNING → PAUSED → RUNNING`；任何狀態可停止。RUNNING／PAUSED 時固定目標，不允許選取其他視窗。只在 RUNNING 發起工作，同一時間最多一筆。

UI 的 QTimer 依使用者間隔排程；QThread 不直接更新 widget，透過 Qt signals 把影格送回主執行緒。Win32 的 PrintWindow 在 spawn 子程序中執行，父程序輪詢取消與四秒期限，逾時／取消即終止子程序。GDI 資源用 `finally` 釋放；不使用桌面 DC 作為擷取來源。

暫停／停止遞增 generation 並取消工作。回傳的 frame 必須符合目前 generation 與 RUNNING 才能顯示，避免晚到影格覆蓋敏感資訊暫停。停止與暫停清空預覽。不記錄像素到磁碟；跨程序暫存和記憶體複本由程序／物件生命週期釋放，沒有宣稱進行密碼學記憶體抹除。

視窗身分至少比對 HWND＋PID。HWND 重用於同一程序的極端情境仍需更進一步身分驗證；此限制列入 Windows 實機測試。視窗列舉是快照，選定後視窗關閉會在擷取時回報，不切換監看目標。

## Phase 2–4 的後續介面邊界

1. `Frame → ChangeDetector`：像素差異、代表性影格、限制 API 分析間隔。
2. `VisionProvider → ObservationEvent`：只回報畫面證據，保留不確定性、時間、事件 ID 與短期摘要。
3. `EventDeduplicator`：避免同一死亡／字幕畫面重複觸發。
4. `Coordinator`：使用者優先佇列，安靜／一般／熱鬧政策，挑選一至數位聲道；發言間隔、每分鐘與事件人數上限。
5. `CharacterRepository`：載入正式人格，不把目前空白欄位補成自行想像的背景。
6. `BudgetLedger`：每分鐘／每日花費估算，API 失敗停止、不自動重試；不得將截圖不斷重傳。
7. `CredentialStore`：Windows 安全憑證儲存，不在程式碼、角色 JSON、截圖或日誌放 Key。畫面傳送另有明確啟用流程。
8. `SQLiteStore` 的持久資料、明確確認刪除、備份還原已提前納入 Phase 1；Phase 4 再接上自動記憶萃取與模型上下文選取。原始影格不儲存。資料表與實際恢復測試見 [PERSISTENCE.md](PERSISTENCE.md)。

正式聲道日英回覆及翻譯應由同一事件上下文產生；測試文字只驗證 UI，不能驗證人格、關西腔、畫面理解或自主行為。

## Phase 2/3 實作現況

ai_cost.py 的 UsageLedger 集中攔截預算、用量與合併還原；ai_provider.py 限 OpenAI HTTPS / DPAPI / 可終止子程序；ai_engine.py 保留完整十三設定、畫面變化門檻、事件去重與多人交易；ai_ui.py 接本機設定、兩項真實用量門檻、文字與自主開關。完整設計與限制見 [PHASE2_3.md](PHASE2_3.md)。
