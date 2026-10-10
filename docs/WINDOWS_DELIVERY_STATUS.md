# Windows 試用包交付與驗證

## 麥克風啟動修正 0.3.1

- 下載：[Windows 語音 0.3.1](https://github.com/linda12392bb/our-little-days/blob/main/downloads/OurLittleDays-Windows-x64-Voice-0.3.1-preview.zip)。原 0.3.0 ZIP 保留供查核，建議改用修正版。
- 大小：58,820,491 bytes；SHA-256：`47f9ce21aff30cf45c49a1831b5dc7e1c4930a81eff191400d92f73a3c98e650`。
- EXE SHA-256：`52414b72f4e7532000c3e86624b1920d5e7dde67320c05a1f60e927a8611a9d0`。
- 原因：PySide6 6.10.2 的 QAudioSource.error() 回傳 QtAudio.Error；0.3.0 用 QAudio.Error 比對，即使兩者都是 NoError（值 0）仍不相等。同步 stateChanged 因此停止正常音源，之後存取已清空 source 又產生 AttributeError，顯示泛用「麥克風啟動失敗」。
- 修正一致使用 QtAudio.Error、處理同步停止後的返回、安全斷開收音訊號，並保留 OpenError／IOError／例外類型診斷。
- 回歸測試涵蓋真正 QtAudio 列舉、同步成功／失敗、48 kHz 雙聲道 Float 格式、後續中斷與啟動例外；模擬裝置，沒有開真實麥克風。113 passed，2 Windows-only skipped。
- Windows EXE 離線封裝、QtAudio 回傳型別、Whisper CLI 與原十三人格校驗通過（Linux + Wine）；真實 YETI X 收音及辨識仍待驗收。使用者已回報 YETI X 在 Windows 輸入測試有聲音。
- 更新到新資料夾，沿用 %LOCALAPPDATA%\OurLittleDays；不重下載模型，不重設人格／聊天／記憶／金鑰／預算。

## 自動語音輸入 0.3.0

- 檔名：`OurLittleDays-Windows-x64-Voice-preview.zip`
- 大小：58,820,442 bytes
- SHA-256：`43a05da792c0a2fb6c73be9559bc3ceaacddb1acb10d769ffe51713b157142e8`
- EXE SHA-256：`1d6e1f3f4ac59e65124adf9bf7fed3975d79d709959422d986478a198d785130`
- 109 項自動化測試通過、2 項 Windows-only 跳過；十三個角色檔及原始設定完全未改。
- 新增手動開啟後自動偵測麥克風、WebRTC VAD、音量、門檻／增益與 1.5 秒分句、文字預覽與自動送出。Whisper 本機多語 CPU 執行檔含於 ZIP，首次需明確下載免費模型。
- 不產生原始音訊檔、不上傳音訊；辨識文字預設不存聊天，住戶回覆沿用 SQLite，語音回覆與指定畫面共用原預算。
- 已驗證封裝內 VAD／OpenCC、Whisper CLI 啟動與校驗、SQLite 重開／備份、預設麥克風關閉；91 個 Windows wheel／官方 Whisper DLL/PYD 來源校驗，沒有 Wine 系統檔。
- 建置環境 Linux + Wine，**沒有 Windows 實體麥克風驗收，沒有真實模型權重辨識、小聲短句或中日混合準確度測試，也沒有真實 API 成本測試**。雲端下載 Hugging Face 模型受到網路政策限制。
- 詳細操作／後續驗收與改善順序：[VOICE_INPUT.md](VOICE_INPUT.md)。

下載：[私人 GitHub 語音試用包](https://github.com/linda12392bb/our-little-days/blob/main/downloads/OurLittleDays-Windows-x64-Voice-preview.zip)，按 View raw。Phase 1／Phase 2/3 原 ZIP 均保留，更新讀原本機 SQLite。

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
