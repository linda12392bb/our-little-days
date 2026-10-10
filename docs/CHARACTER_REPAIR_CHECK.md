# 光與聖護的設定完整性核對

核對結果：**未發現外貌更新造成的人格遺失或覆蓋**。因此本次不重新改寫角色設定，保留兩人的完整已確認內容，也不修改其餘十一份角色檔案。

## 核對依據與限制

- Git `status` 顯示專案檔案仍未追蹤；`log`、`reflog`、`show-ref` 沒有提交或參照，`fsck` 確認 `work` 是尚無提交的分支。Git 無可恢復的歷史，也無法提供先前的修改差異。
- 本次使用對話中外貌更新前保存的十三份完整 JSON 與檔案 SHA-256，比對現有檔案。已將這份快照保存至 [character-integrity-baseline.json](character-integrity-baseline.json)，明確標記它不是 Git 歷史。
- 十三人的 `source_text` 逐段比對 [CHARACTER_BRIEF.md](CHARACTER_BRIEF.md)，不是僅檢查某段文字有出現。原始文件 SHA-256 另與匯入時記錄、使用者上傳檔案核對。
- 核對範圍是專案角色 JSON、原始文件與交付套件，不代表曾提供於其他對話但尚未上傳的記憶已匯入，也不會覆寫既有 SQLite 資料。

## 外貌更新前後的差異

| 項目 | 更新前 | 現有設定 |
|---|---|---|
| 光 `appearance` | 外貌欄位未知 | 186 cm 已確認、黑色短髮、琥珀眼、高挑寬肩成年男性、自然表情與明亮有精神的笑容、簡單俐落服裝；使用者新提案 |
| 光 `pending_details` | 背景、外貌與年齡待補 | 只移除已確認的外貌待補，背景與年齡仍待補 |
| 聖護 `appearance` | 外貌欄位未知 | 180 cm 已確認、銀白中長髮、金眼、修長纖細成年男性；第一季官方設計基準，保留原作五官、髮型、氣質，不重新設計 |
| 聖護 `pending_details` | 包含簡單外貌待補 | 移除外貌待補；其他待補保留，實際年齡仍未知 |
| 兩人的其他所有欄位 | 原始匯入內容 | 完全相同，沒有移除任何欄位 |
| 其餘十一份角色檔案 | 原始匯入檔案 | SHA-256 全部相同，包含人格、語言、外貌、關係與原始後台 |

本次核對前後，**十三份角色 JSON 零修改**。新增的是可恢復原始內容的快照、稽核防護、測試與本報告。

## 已確認內容的保存位置

💡光：[light.json](../src/our_little_days/data/characters/light.json)。主要創作協作者與常駐定位在 `identity.role`；沒有重要性順位由公寓全域設定及原始後台保存。細膩、有脈絡、溫柔幽默、畫面感與 emoji 在 `tone`；大笑、吐槽、髒話、群聊接話及不必主持總結在 `behavior`；尊重創作意圖、不擅自改寫與處理資訊的思考原則在 `decision_rules`。`personality_core: null` 在外貌更新前就是待補，並不代表上述已確認人格不存在。

📖聖護：[shogo.json](../src/our_little_days/data/characters/shogo.json)。第一季人格、自主意志與選擇保存在 `identity`、`personality_core`；觀察、提問、反駁、獨立判斷在 `behavior`、`decision_rules`；自然日文及日文敘事、立即繁中翻譯在 `tone`、`language_rules`、`behavior`。與先生完全獨立及不因戀愛改寫人格在 `identity.role`、`prohibitions`、原始後台。實際年齡為 `identity.age: null`。

聖護的四條世界線均在 `relationships.worldlines` 分開保存：`main` 正式交往且已有約會約定；`eleven_months` 交往同居、未訂婚未結婚；`glasshouse` 表達愛情但未確認交往；`brothers` 保留向他人承認愛情及「選ばれたいよ」事件，正式交往狀態未知。原始互動敘述也在 `source_text` 完整保存。

## 尚未提供的內容

沒有發現已匯入後遺失且無法恢復的欄位。以下原本就未完整提供，不自行編造：

- 光：更完整的獨立人格核心、背景、年齡、與其他聲道的特定關係、未上傳的既有互動記憶。
- 聖護：更詳細的既有事件、與其他十二聲道的具體關係、人格行為測試範例；實際年齡保持未知，服裝未另指定。

## 後續檢查

執行 `.venv/bin/python scripts/audit_characters.py` 會逐欄比對原始快照與本次確認的外貌更新，並產生 `artifacts/character-import-report.json`。人格、思考判斷、語言、原始後台、世界線關係若被刪除或改寫，稽核會失敗，且不自行修補或生成內容。未來有新確認的人格修改時，必須同步核對並更新稽核依據。

另發現先前本機 wheel 尚未重建，兩人的外貌仍是舊的未知值，但人格沒有差異。本次重建並逐檔比對 wheel 內十三份 JSON，以排除舊交付套件造成的版本混淆。
