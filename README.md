# 月色不打烊｜Windows Phase 1 試用包

這裡提供已製作完成的 Windows 本機桌面試用包。使用者不需要安裝 Python、下載原始碼或自行編譯；也不需要改成公開儲存庫或部署網站。

## 下載與啟動

1. 登入擁有此私人儲存庫權限的 GitHub 帳號。
2. 開啟 [Windows 試用包](downloads/OurLittleDays-Windows-x64-Phase1-preview.zip)。
3. GitHub 無法預覽 ZIP 是正常的。在檔案頁面右上角按下載箭頭（Download raw file）；若看到 `Raw` 或 `View raw`，也可以按它取得檔案。
4. 在 Windows「檔案總管」找到下載的 ZIP，按右鍵「全部解壓縮」。
5. 打開解壓縮後的 `OurLittleDays` 資料夾，雙擊 `OurLittleDays.exe`。

保留整個資料夾，包括 `_internal`。請建立桌面捷徑，勿單獨搬走 `.exe`。

## 驗證範圍

已完成 Windows x64 `.exe` 建置及 Linux＋Wine 相容環境中的離線封裝檢查。真正 Windows 電腦上的啟動、指定視窗擷取與人工操作驗收尚未完成。本版不會自主聊天或呼叫模型 API，角色訊息會標示為測試。

- 檔案大小：47,672,210 bytes（約 46 MB）。
- SHA-256：`deaff1ad5a83e9ac35a13b1aae2f1841d7fd88b7e82d72da1932437849328ac8`。
- [完整 Windows 操作說明](downloads/WINDOWS_USER_GUIDE.md)
- [Windows 實測紀錄表](downloads/WINDOWS_TEST_CHECKLIST.md)

聊天與角色資料會儲存於 Windows 使用者的本機資料目錄；GitHub 只用來下載程式，執行與保存均在本機。
