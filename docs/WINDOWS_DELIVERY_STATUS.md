# Windows 試用包交付與驗證

## Phase 2/3 0.2.0

- 檔名：`OurLittleDays-Windows-x64-Phase2-3-preview.zip`
- 大小：47,719,306 bytes
- SHA-256：`4d9204abbf6fc59d16e88af98633e1ffedb3a7d80bd9cde4ae65348640ac9537`
- EXE SHA-256：`a12ead3da2b76da40deecc43d91b1808e571e573f673b7d34d81730ca6146d84`
- 官方 Python／Windows wheel DLL/PYD 來源雜湊通過：75 個；沒有打包 Wine 系統相容檔。
- 封裝後離線介面、完整十三人格、SQLite 重開／備份、預設離線費用控制、測試金鑰 DPAPI 加密往返、可終止 transport 子程序與 TLS 初始化通過。transport 使用模擬回應，沒有付費或傳送真實圖片。
- Linux 自動化：83 passed / 2 Windows-only skipped。十小時靜態畫面模擬不新增分析，單次多人接話保存，所有請求共用月預算。
- 本次 Windows Python / PyInstaller 於 Linux + Wine 建置。**真正 Windows 11 Phase 2/3、真實 API usage／費用及模型語氣尚待使用者驗收。**

下載頁：<https://github.com/linda12392bb/our-little-days/blob/main/downloads/OurLittleDays-Windows-x64-Phase2-3-preview.zip>。登入自己的私人儲存庫，按中間 View raw 或向下下載箭頭。不要下載原始碼 ZIP。步驟見 [WINDOWS_USER_GUIDE.md](WINDOWS_USER_GUIDE.md)。

## Phase 1 保留

原 ZIP `OurLittleDays-Windows-x64-Phase1-preview.zip` 原檔保留，SHA-256 `deaff1ad5a83e9ac35a13b1aae2f1841d7fd88b7e82d72da1932437849328ac8`。原交付 commit `57ef85b6840d2c58536560623ae6816d769d4532`。

使用者已回報 Windows 11：成功開啟 OurLittleDays.exe、正常桌面／十三名單與氣泡、選定 Chrome 並擷取預覽、6 秒擷取間隔。未回報的其他 Phase 1 項目不宣稱通過。

## 交付方式

使用者回報 Codex workspace ZIP 與小檔均「無法下載成品」。停止使用同類連結；沿用已授權的私人 GitHub main / downloads 交付完整 EXE ZIP，保留隱私設定，不部署雲端網站。GitHub API 遭 403，改走已可用的 Git 連線。原始碼與文件也在同一儲存庫，使用者不需下載或編譯它們。

新版遠端取回檢查結果另保留在開發環境 artifacts/github-phase2-3-delivery-verification.json。Git 取回與 ZIP／SHA 驗證不等於使用者瀏覽器或 Windows 原生驗收。
