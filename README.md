# ☾ 月色不打烊｜公寓還是一樣很吵

OUR LITTLE DAYS · Windows 本機十三聲道陪伴室 · Phase 2/3 最小試用版 0.2.0

[下載 Windows 試用包](https://github.com/linda12392bb/our-little-days/blob/main/downloads/OurLittleDays-Windows-x64-Phase2-3-preview.zip)：在 GitHub 檔案頁按 **View raw**。全部解壓後雙擊 OurLittleDays.exe，保留 _internal；不需 Python、原始碼、指令或雲端網站。

[新手啟動與 API 設定](docs/WINDOWS_USER_GUIDE.md) · [Windows 實測清單](docs/WINDOWS_TEST_CHECKLIST.md) · [Phase 2/3 設計與費用界線](docs/PHASE2_3.md)

使用者已確認 Phase 1 在 Windows 11 開啟與 Chrome 指定擷取成功。**新版尚待 Windows 實機與正式 API 成本驗收**；封裝使用 Windows Python / PyInstaller 在 Linux + Wine 建置，不假稱原生 Windows 測試通過。原 Phase 1 ZIP 保留於 downloads。

## 本次功能

- 沿用桌面、主題、十三名單、Win32 指定視窗擷取與 SQLite 永久資料。
- 預設 gpt-4.1-mini 圖片理解；Windows DPAPI 金鑰加密，本機明確啟用後才付費。
- NT$500 月預算、集中預留／結算、每日／每月用量、剩餘預算與待確認費用。估算與平台實際帳單分開。
- 六秒擷取不等於 API 排程；明顯且穩定的新畫面才分析，最短 120 秒間隔，只是限速。靜態畫面可一直安靜。
- 每次完整十三份原始人格進入同一請求，0–4 人自然回應，支援彼此接話與 SQLite 回覆關聯。
- 外語發言／敘事逐段附繁中翻譯；世界線隔離、事件持久化，不擅改人格、未知欄位或戀愛關係。
- 必須先完成單次畫面／單次角色的真實用量測試，才能手動啟用文字 AI 與自主陪伴。
- 關閉／暫停／異常恢復、完整聊天備份與用量合併還原。沒有付費自動摘要或自動重試。

模型參考價格與匯率未即時核對，設定中要求確認。預算是本機依設定價格估算的上限，不是平台實際帳單硬上限。PrintWindow 仍不保證所有遊戲／影片可擷取；WGC 與自主品質改善留待後續。讀取長期記憶與重要事件，近期聊天最多 60 則；不刪減人格。

## 開發環境（一般使用者不需做）

Python 3.11–3.13，建議 3.12。依賴固定於 uv.lock / requirements-lock.txt。Windows：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m our_little_days
```

Linux 開發（不能真實擷取 Windows，不能安全保存 Key；測試不呼叫 API）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-lock.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/smoke_ui.py
```

已有的雲端環境仅用於開發／測試，不是產品部署。使用隔離工作目錄，不需建立新 worktree。原生擷取與 DPAPI 測試需 Windows；模擬 API 測試不證明真正模型品質。

Windows 打包：開發者雙擊「製作Windows執行檔.cmd」，或在 Windows x64 Python 跑 scripts/build_windows.py。產物：artifacts/OurLittleDays-Windows-x64-Phase2-3-preview.zip。

正式原始角色與稽核：[角色匯入](docs/CHARACTER_IMPORT.md)、[完整性修復](docs/CHARACTER_REPAIR_CHECK.md)、[已確認補充](docs/CHARACTER_UPDATES.md)。十三 source_text 保留，種子只初始化；唯一已授權資料補充是序調戲／♡，合併前備份。SQLite 設計：[PERSISTENCE.md](docs/PERSISTENCE.md)。
