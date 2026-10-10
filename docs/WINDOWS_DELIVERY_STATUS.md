# Windows 試用包交付與驗證

## 同批接話修正 0.4.2

- 下載：[Windows 自主陪伴 0.4.2](https://github.com/linda12392bb/our-little-days/blob/main/downloads/OurLittleDays-Windows-x64-Companion-0.4.2-preview.zip)，登入私人 GitHub，按 View raw。原七個 ZIP 保留。
- 大小：59,091,340 bytes；SHA-256：`36c91735bc7ee8cb0105d008334ea6d258c91cd2783266ee07118c36d84ea2f0`。
- EXE SHA-256：`b59a7e2f080ef5b883d97f9961eadb48d0b4b1de8992f91d42f769c9459ddf7a`。
- 使用者確認 0.4.1 畫面與所選角色兩項成本測試成功；安靜群聊顯示「第 1 則訊息的同批接話參照無效」，15759 輸入／346 輸出，估算 NT$0.2263。原因是原 schema 允許任意整數同批索引，第一則沒有可引用的較早訊息，本機正確拒絕不合法參照，導致群聊無法顯示且 AI 停用。
- 新版所有群聊 API 輸出固定 reply_to_index=null，模型只指定 recipient_ids。同批較早且符合明確收話對象的最近一則由本機建立 reply_to；第一則向尚未發言角色開話題不建立虛構連結。跨批 reply_to_message_id 仍採同世界線實際 ID 且優先，不猜測其他批次或 AU，不改寫任何發言、翻譯、人格或關係。舊版合法索引仍可讀取；不合法索引仍拒絕，整批原子回滾。
- 安靜開關代表使用者允許空閒自主群聊；每次啟動後勾選即可保持開啟，使用者不用自行判斷是否安靜，也不用說話前關閉。公寓輸入、語音／待處理回覆、事件優先、三分鐘本機檢查、既有冷卻／去重／預算不變。AI 錯誤停用後仍需手動重新啟用，不偷偷恢復付費工作。
- 已成功的畫面／角色成本測試標記、SQLite、原模型、Key、NT$500 月額與使用者自訂冷卻／每日額度保留；先前失敗費用照常計入，不重試、不退款、不重置預算、不付款或加值。升級後只需手動啟用文字 AI，再做一次安靜群聊測試，成功後啟用安靜排程。
- 全套 **150 passed、2 Windows-only skipped**，十三人格原文稽核、Qt UI smoke、依賴檢查通過。新增所有群聊請求的 null 格式、四則同批接話與最近符合對象、SQLite 重開、開場不誤連舊訊息、跨批明確 ID 優先、五類非法第一則索引原子拒絕，原預算／世界線／長期記憶測試保留。
- Windows frozen EXE 在 Linux+Wine 直接點擊安靜測試按鈕，以單次模擬 API 產生光→序→REO 的接話並保存正確 reply_to；以截圖相同 token 驗證估算 NT$0.2263、成功視窗與排程預設 OFF。原 DPAPI、SQLite 備份／重開、原生官方二進位來源與公開 tiny 語音參考亦通過。**未執行 0.4.2 Windows 實機或付費正式模型驗收**。

## 單次 AI 角色測試格式與失敗提示 0.4.1

- 下載：[Windows 自主陪伴 0.4.1](https://github.com/linda12392bb/our-little-days/blob/main/downloads/OurLittleDays-Windows-x64-Companion-0.4.1-preview.zip)，登入私人 GitHub，按 View raw。原六個 ZIP 完整保留。
- 大小：59,087,933 bytes；SHA-256：`f4f68173768db596c58c00af1f51a02ad486b6ded405cce9864d4cc58c94b29a`。
- EXE SHA-256：`80c6d60e3dd8530164765921ca95b70a6d6cc83be5f89762a1b76ef8106def9a`。
- 使用者 Windows 11 截圖確認 0.4.0 曾成功產生序的正式 API 回覆與用量視窗，之後顯示泛用「AI 回應結構、角色或翻譯不完整」，無失敗視窗。可確認請求有回應且未通過本機驗證；舊版未保存原始失敗回應，無法僅憑截圖確定具體欄位，不宣稱已重現該次正式模型回應。
- 角色／對象 ID、單次測試的一則所選角色回覆、空接話參照、可引用真實同世界線對話 ID 直接約束於每次 strict JSON schema。所有十三份完整人格仍送出，不容忍錯誤世界線、缺翻譯或語言漂移。
- 單次失敗也跳視窗，逐項區分角色／對象 ID、回覆者、翻譯、語言、參照、JSON 及輸出截斷；測試按鈕旁新增狀態。已回報 token 與估算費用仍保存並顯示；未送出明示無新費用，未知費用保留預留額。失敗不算測試通過、不自動重試。
- SQLite、角色檔、世界線關係、接話錨點、原模型與 DPAPI 金鑰、所有既有用量／成本測試標記與自訂預算均沿用；本次未修改任何十三位角色資料或預算計算。所有請求仍共用月預算，沒有付款、儲值或付款設定操作。
- 全套 **139 passed、2 Windows-only skipped**；十三人格稽核、Qt UI smoke 與依賴檢查通過。新增檢查真實按鈕點擊、所選十三人格式、跨 AU ID 隔離、八類失敗精確提示與 15054 輸入／161 輸出成本回報、無付費前置阻擋、無重試及原資料保留；API 全為模擬，未發送付費請求。
- 重建 frozen Windows EXE，在 Linux + Wine 直接點擊打包後的角色測試按鈕：AI 暫停也能執行，失敗提示／已結算費用／成功訊息保存通過。DPAPI 測試金鑰、SQLite 重開／備份、跨批次接話、官方 Windows 二進位來源及公開已訓練 tiny 辨識範例亦通過。**0.4.1 Windows 實機與正式模型回應尚待使用者驗收**。
- 更新方式：關閉舊程式，全部解壓到新資料夾，雙擊 EXE。已成功的目前設定畫面成本測試不需重做；只做一次角色測試。若再失敗，回報新視窗具體原因，不連續重試。

## 安靜自主聊天、跨批次接話 0.4.0

- 下載：[Windows 自主陪伴 0.4.0](https://github.com/linda12392bb/our-little-days/blob/main/downloads/OurLittleDays-Windows-x64-Companion-0.4.0-preview.zip)，登入私人 GitHub，按 View raw。五個原 ZIP 全數保留。
- 大小：58,835,880 bytes；SHA-256：`4036241a89a82b3bc1512b3a37be156d8bc6bd3e30598792bfa6b16f2fabb53b`。
- EXE SHA-256：`ca3ae29a336c6faf531a04fed815b4d041466e7395436a5bd117ef1b8967d052`。
- 每 3 分鐘只做本機資格檢查；預設冷卻 6 分鐘，可調到 3 分鐘。說話、辨識、文字預覽、輸入中、AI 忙碌或有待處理畫面時延後；冷卻未到／額度不足不呼叫 API。獨立開關每次啟動預設關閉，安靜模式不需持續擷取視窗。
- 每批同一次請求產生通常 2–3、最多 4 則訊息；母語、敘事、逐段繁中翻譯與 JSON 共用最大 2000 tokens。完整十三人格與 source_text 不刪減。原設定高於 2000 時降至 2000；新上限需重做原成本測試，不清空費用紀錄。
- 自主聊天與單次安靜成本測試共用每日估算 NT$10；**全部 API 請求**仍共用每月估算 NT$500，發送前集中原子預留。日額不足只停止新的安靜聊天，月額不足停止全部新請求並提示。未實作背景付費摘要／自動重試；異常與取消保留可能費用。
- 使用者自行管理 API 預付額度且已關閉自動儲值。公寓只呼叫對話 API，**不付款、不加值、不開啟自動儲值、不變更付款設定**。NT$500 是 API 使用上限，非每月充值金額；與平台帳單分開。
- SQLite `settings` 的 `conversation:<世界線>` 保存接話錨點、參與者、跨批次訊息 ID、去重及冷卻，聊天原文仍在 messages。起始引用離開近期 60 則後仍保留，重開／備份／還原保留，AU 獨立；不把閒聊、猜測寫成正式事件或長期記憶。重複整批／模型選擇安靜會延長冷卻，該次分析仍計費。
- 新增「單次安靜群聊 · 測量費用」，正式排程前由使用者本機先測真實輸入／輸出量與估算。
- 全套 129 passed、2 Windows-only skipped；最終核心 75 項再驗證通過。十三份完整人格與原始後台稽核通過且檔案未改。涵蓋十小時三分鐘檢查、說話／打字／畫面優先、跨批次連續接話、世界線隔離、去重／持久冷卻、日額不足仍可語音、月額阻擋所有請求、原子預算、取消不重試、SQLite 重開與備份。
- Windows frozen EXE 在 Linux + Wine：新開關預設關閉、三分鐘 timer、2000 上限、NT$10 日額、跨批次回覆與重開保存、91 個官方二進位來源及原本公開已訓練 tiny 語音範例均通過。**未執行新版 Windows 實機自主聊天、正式模型語氣／連續性及真實 API 費用驗收**。使用者已回報 0.3.2 有語音辨識文字。
- 更新沿用已下載模型、SQLite、金鑰與記憶；詳細操作與限制見 [QUIET_CHAT.md](QUIET_CHAT.md)。

## 辨識文字空白修正 0.3.2

- 下載：[Windows 語音 0.3.2](https://github.com/linda12392bb/our-little-days/blob/main/downloads/OurLittleDays-Windows-x64-Voice-0.3.2-preview.zip)，登入私人 GitHub，按 View raw。舊版 ZIP 全數保留。
- 大小：58,824,154 bytes；SHA-256：`46c0891854aa16112d80ca3457db957a9288c12513aebf9f96dc0518feccebc2`。
- EXE SHA-256：`914f561d82e5a70ed062de8ecfa9a0d93ebc9a34745f921262a157aa7944b5a8`。
- 原因：whisper.cpp 1.8.2 的 stdin 輸入 `-f -` 會預設把輸出名稱設為 `-`，停用正常文字回呼。0.3.1 因此能正常結束但 stdout 沒有文字。修正設定非 `-` 的輸出名稱，保持不啟用任何輸出檔案格式，語音仍只在 RAM／stdin。
- 新增驗證模型／辨識階段、等待秒數、語音長度、待處理段數；佇列滿時略過新段並明確提示，不取消正在辨識的段落。small 逾時上限 180 秒，其他模型 90 秒。
- 116 項測試通過、2 項 Windows-only 跳過；完整十三角色與原始 source_text 稽核通過，設定檔未改。
- 真實辨識驗證：官方公開 jfk.wav、與官方 SHA1 相符的已訓練 tiny 多語權重、官方 Windows Whisper CLI 1.8.2；相同公開語音在修正前為空白、修正後有文字。封裝後的 0.3.2 EXE 也經自動語言模式呼叫同一辨識路徑成功。無私人音訊、麥克風擷取或付費 API 請求。
- 封裝後 SQLite 重開／備份、預設麥克風／AI 關閉、Qt／VAD／OpenCC、91 個官方二進位檔來源校驗均通過；建置與驗證環境為 Linux + Wine。**尚未完成新版在 Windows 實體 YETI X、小聲短句、中日混合及辨識速度驗收。** 使用者已回報 0.3.1 麥克風有音量且能進入辨識，預覽空白。
- 在公寓下載成功的 small 已自動安裝，不用再匯入；只有瀏覽器另外下載的模型檔才需匯入。更新到新資料夾沿用 `%LOCALAPPDATA%\OurLittleDays`，不重下載模型或重置聊天／人格／關係／記憶／金鑰／預算。
- 先保持 AI 關閉，選 YETI X 與已下載的 small，說一句話並停語超過 2.5 秒，觀察新辨識狀態與文字預覽。停語判定與模型運算是兩個等待階段，不能把 2.5 秒當作辨識完成保證。

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
