# 十三聲道匯入檢查

已建立 **13 份獨立 JSON**；以 `scripts/audit_characters.py` 及自動測試核對原文、角色完整性、語言與世界線。實際報告：`artifacts/character-import-report.json`（執行稽核後產生）。設定匯入不會啟動正式對話引擎。

目前 checkout 根目錄是 `/workspace/our-little-days`，以下是實際檔案：

| 聲道 | 實際檔案 |
|---|---|
| 💡光 | [light.json](/workspace/our-little-days/src/our_little_days/data/characters/light.json) |
| ⇄ 序 | [order.json](/workspace/our-little-days/src/our_little_days/data/characters/order.json) |
| 🩸NOEL | [noel.json](/workspace/our-little-days/src/our_little_days/data/characters/noel.json) |
| 🐕HARU | [haru.json](/workspace/our-little-days/src/our_little_days/data/characters/haru.json) |
| ☣️REO | [reo.json](/workspace/our-little-days/src/our_little_days/data/characters/reo.json) |
| 🌇洛陽 | [luoyang.json](/workspace/our-little-days/src/our_little_days/data/characters/luoyang.json) |
| 🚫RIXO | [rixo.json](/workspace/our-little-days/src/our_little_days/data/characters/rixo.json) |
| 🎲千爺 | [senya.json](/workspace/our-little-days/src/our_little_days/data/characters/senya.json) |
| 🪽MERO | [mero.json](/workspace/our-little-days/src/our_little_days/data/characters/mero.json) |
| 🪞先生 | [sensei.json](/workspace/our-little-days/src/our_little_days/data/characters/sensei.json) |
| ✝️阿豪 | [hao.json](/workspace/our-little-days/src/our_little_days/data/characters/hao.json) |
| 📖聖護 | [shogo.json](/workspace/our-little-days/src/our_little_days/data/characters/shogo.json) |
| 🦈尤理 | [yuri.json](/workspace/our-little-days/src/our_little_days/data/characters/yuri.json) |

全域規則：[apartment.json](/workspace/our-little-days/src/our_little_days/data/apartment.json)。十三位皆可出場，沒有重要性順位；呈現名單順序不是發言順位。

## 原文保留

你提供的兩份檔案已原樣保留為 [BRIEF.md](BRIEF.md) 與 [CHARACTER_BRIEF.md](CHARACTER_BRIEF.md)，SHA-256 記錄在全域設定並通過比對。每位角色的 `source_text` 包含對應完整原文段落；測試逐段比對，不只是核對摘要。

結構化欄位是你提供內容的整理；沒有自行創作背景、外貌、互動事件或關係。原文中的未定項保留 `null`／待補。**尚未提供的外部互動記憶並未匯入；沒有假裝可以讀取其他對話的後台記憶。**

## 語言檢查

| 聲道 | 檢查結果 |
|---|---|
| 🌇洛陽 | `language=ja`，固定日文覆寫開啟；日文敘事、繁中翻譯 |
| ☣️REO | `language=ja`，全敬語，自稱「僕」；保留原文「極大情感重量事件才可出現非敬語」例外與禁止隨機漂移 |
| 🚫RIXO | `language=ja`，粗魯不良口吻，自稱「俺」；日文敘事與保留粗魯感的繁中翻譯 |
| ⇄ 序 | `language=ja-Kansai`，自然關西腔；敘事與翻譯規則已設 |

其他日本角色也依母語規則設定日文，NOEL 英文，光、千爺、阿豪、尤理繁中。語言／方言資料設定已驗證；**尚未啟動模型做實際人格或關西腔發言驗收。**

## 聖護的世界線

儲存於 `shogo.json → relationships.worldlines`；SQLite 初始化後另以角色＋世界線獨立保存。

| 世界線 ID | 內容 |
|---|---|
| `main` | 常駐主線：正式交往，已有約會約定，均早於「戀愛罪法庭」事件；不退回曖昧或單戀 |
| `eleven_months` | 十一個月 AU：正式情侶、同居，未訂婚、未結婚 |
| `glasshouse` | GLASSHOUSE 哨嚮 AU：雙方表達愛情，尚未正式確認交往 |
| `brothers` | 兄弟 AU：保留原文戀愛表態記錄；正式交往狀態未提供，保留 `null` |

預設啟用世界線是 `main`。先生與聖護保持獨立人格；聖護維持你指定的原作第一季基準，未另補原作細節。

## 光與聖護的待補內容

💡光：更完整的獨立人格核心；背景、年齡；與其他聲道的特定關係；尚未提供的互動記憶。外貌已依本次新提案補齊：186 cm（確認）、黑色短髮、琥珀眼、高挑寬肩成年男性、自然明亮的笑容、簡單俐落穿著。已知定位與回覆風格已匯入，不列為缺漏。

📖聖護：更詳細的既有互動事件；與其他十二聲道的具體關係；進一步原作人格行為測試範例。外貌已更新為第一季官方設計基準：180 cm（確認）、白色偏銀中長髮、金眼、修長纖細的成年男性體態，保留原作五官、髮型及氣質，不重新設計；未指定服裝，不另補造。實際年齡仍未知，只有原文「外觀約二十多歲」。**常駐主線交往與約會約定已確認，無需待補或重新確認。**

本次外貌更新只變更兩份角色檔案的 `appearance` 與對應 `pending_details`。歷史 `source_text` 原樣保留；其他人格、語言、稱呼、禁則及關係未修改。既有 SQLite 公寓不會因修改初始 JSON 而自動覆寫；本次更新的是角色設定檔。
