---
name: raco-forecast
description: Raco AI 預測：用開源 K 線 AI 模型 Kronos 預測股票、ETF、加密貨幣未來的走勢，並產生互動網頁報告（繁體中文）。使用者提到 Raco、Raco AI 預測、Kronos、「用 AI 預測 K 線／股價走勢」「預測 0050 接下來怎麼走」「用 Raco 預測」「做一份預測報告」時使用。第一次使用會先教學，再帶使用者完成安裝、抓資料、跑預測，最後輸出可分享的 artifact 報告。Use when the user asks for a Raco (Kronos-based) candlestick forecast or report.
---

# Raco AI 預測

Raco AI 預測是課程提供的預測工具，核心是開源的 K 線 AI 模型 Kronos。帶使用者用它預測一檔標的未來的 K 線，最後交出一份互動網頁報告。對使用者稱呼這個工具時用「Raco AI 預測」；講到底層模型時照實說是 Kronos。報告包含：
- 歷史 K 線與成交量
- 未來每根 K 的中位數，以及 25–75%、10–90% 兩層預測區間
- 抽樣路徑
- 統計數字：收高的比例、站上壓力的比例、過程中跌破支撐的比例

這個 skill 需要在使用者的電腦上執行 Python，例如 Claude Code。claude.ai 網頁版的沙盒無法安裝 PyTorch、也下載不了模型；遇到這種情況，請使用者改用 Claude Code。

## 先找到腳本位置

以下用 `$SKILL` 代表本 skill 的資料夾（SKILL.md 所在處）：
- Claude Code 載入 skill 時會告訴你 Base directory，直接用那個路徑。
- 不確定路徑時執行：

```bash
SKILL=$(dirname "$(find ~/.claude -name build_report.py -path '*raco-forecast*' 2>/dev/null | head -1)")/..
```

各支腳本要用哪個 Python：
- `setup_raco.py`、`fetch_data.py`、`build_report.py` 只用標準函式庫，用系統的 `python3` 執行（Windows 用 `py`）。
- `forecast.py` 要用安裝時建好的 Python。它的路徑寫在 `~/.raco-forecast/config.json` 的 `"python"` 欄位。

## 第一次使用：先教學，再實作

符合以下任一情況，就照 `references/teaching.md` 一邊教一邊做：
- `python3 $SKILL/scripts/setup_raco.py --check` 回報尚未安裝。
- 使用者問「怎麼用」。
- 使用者看起來是第一次接觸。

教學的做法：
- 教學是穿插在操作裡的，不是先講一大段再開始。每一步開始前用 2–4 句話說明在做什麼、為什麼。等待的時間拿來講觀念。
- 已經用過的人，跳過教學，直接做。
- 使用者沒講清楚的，只確認三件事：標的、預測長度（預設 20 根）、要不要加跑 Kronos-base。能從對話推斷的就不要問。

---

## 步驟 1：檢查或安裝環境

```bash
python3 $SKILL/scripts/setup_raco.py --check
```

尚未安裝時執行下面的指令。第一次約 5–15 分鐘，要下載 PyTorch 和約 500 MB 的模型。

```bash
python3 $SKILL/scripts/setup_raco.py                  # small + base
python3 $SKILL/scripts/setup_raco.py --models small   # 只裝 small，比較快
```

執行時要注意：
- 安裝會寫入 `~/.raco-forecast/`，這個腳本可以重複執行。以前裝過舊版（`~/.kronos-forecast/`）的話，腳本會直接搬過來用，不用重新下載。
- 能在背景執行長指令的話就放背景跑，並把進度轉述給使用者。
- 失敗時看最後幾行錯誤，對照下方的「疑難排解」。

## 步驟 2：抓資料

依標的選資料來源：

| 標的 | 來源 | 指令 |
|---|---|---|
| 台股上市股票、ETF（例如 0050、2330） | 證交所日 K | `python3 $SKILL/scripts/fetch_data.py twse 0050 -o data.csv` |
| 加密貨幣（例如 BTCUSDT） | 幣安現貨 | `python3 $SKILL/scripts/fetch_data.py binance BTCUSDT --interval 4h -o data.csv` |
| 美股與其他市場（例如 AAPL、^TWII） | Yahoo 財經 | `python3 $SKILL/scripts/fetch_data.py yahoo AAPL -o data.csv` |
| 使用者提供的 CSV | 看盤軟體、券商匯出 | `python3 $SKILL/scripts/fetch_data.py csv 檔案.csv -o data.csv` |

各來源的注意事項：
- **工作資料夾**：在目前目錄下建 `raco-reports/<代號>-<日期>/`，資料、結果和報告都放這裡，並告訴使用者位置。
- **台股上櫃股票**：證交所沒有上櫃資料，請使用者從看盤軟體或券商匯出日 K 的 CSV，改用 csv 來源。
- **台股抓多久**：預設抓 24 個月，大約 480 根。每個月之間會停 3 秒，總共約 1–2 分鐘。
- **Yahoo 限流**：Yahoo 常常限流。遇到 429 錯誤就請使用者稍後再試，或改給 CSV。
- **讀摘要 JSON**：腳本最後會印出一行 JSON，看 `rows`（筆數）、`last_close`（最後收盤）、`name`（名稱）和 `warnings`（警告）。有警告就轉述給使用者，例如缺月份、偵測到股票分割。

## 步驟 3：跑預測

```bash
<config.json 裡的 python> $SKILL/scripts/forecast.py --data data.csv --pred-len 20 \
    --models small,base --samples 100,50 -o result.json
```

- **預設**：使用者沒指定時，跑 `--models small --samples 100`，大約 3–6 分鐘。使用者想比較模型時才加 base。
- **預測長度**：日 K 的 20 根約一個月。使用者說「下週」就用 5，「下個月」就用 20。
- **進度**：腳本每 10 條路徑印一次進度和預估剩餘時間。這段等待最適合講「抽樣路徑」的觀念（教學第 4 段）。
- **硬體**：預設自動選硬體，有 NVIDIA GPU 用 cuda，Apple 晶片用 mps，其他用 cpu。mps 出錯時加 `--device cpu`。

## 步驟 4：決定關鍵價位

報告會標出 2–4 條關鍵價位，「站上壓力的比例」和「過程中跌破支撐的比例」都是用這些價位算的。

決定方式：
- **使用者給的**：使用者講過的價位優先。
- **從 data.csv 找**：沒有的話，自己看 data.csv 的最後 60 根，挑出這幾類：
  - 最近的跳空缺口
  - 前一波高點、前一波低點
  - 最近一次急跌、急漲的起點
- **用 K 線型態判讀**：同時裝了 `kline-pattern-analyzer` 的話，可以用它的頸線和多空分界。
- **自動**：都沒有時，用 `--auto-levels` 自動取近 20、60 根的高低點。

格式是 `--levels "97.15:缺口上緣;91.8:頸線（7/29 低點）;87.55:11/6 低點"`。說明文字要寫出這個價位的由來。

## 步驟 5：產生並交付報告

```bash
python3 $SKILL/scripts/build_report.py --data data.csv --result result.json \
    --name 元大台灣50 --symbol 0050 --source-label 臺灣證券交易所 \
    --levels "97.15:缺口上緣;91.8:頸線" -o report.html
```

參數說明：
- **K 棒顏色**：台股代號會自動用紅漲綠跌、成交量換算成張，其他標的用綠漲紅跌。要改就用 `--conv`、`--volume-unit`。
- **標題**：標題和說明會依結果自動產生，例如「偏向上漲」「先回測、再反彈」「看不出明確方向」。想改就用 `--headline`（可用 `<em>…</em>` 強調重點）和 `--lede`。
- **統計 JSON**：腳本會印出一段統計 JSON，回覆時的數字一律從這裡引用，不要自己估。

交付報告：
1. **有可以發布 artifact 的工具時**（例如 Artifact 工具）：用它發布 `report.html`，標題用「<代號> Raco AI 預測」，圖示用 `chart`，然後把連結給使用者，並提醒要從頁面右上角 Share 開啟公開連結，別人才看得到。
2. **沒有的話**：告訴使用者 `report.html` 的完整路徑，請他用瀏覽器打開（雙擊檔案即可）。檔案是單一 HTML，可以直接傳給別人。
3. **如果你能看圖**：發布前截一次圖確認圖表正常，有明顯問題修正一次就好。

## 步驟 6：回覆格式

```
**Raco AI 預測：<名稱>（<代號>）未來 <N> 根 <K 線週期>**
**結論：<偏向上漲／偏向下跌／先回測、再反彈／看不出明確方向>**

| | 第 5 根 | 第 10 根 | 第 N 根 | 第 N 根的八成區間 |
|---|---|---|---|---|
| Kronos-small（100 條） | … | … | … | …–… |
| Kronos-base（50 條） | … | … | … | …–… |

- 收高於最新收盤（xx.xx）的比例：xx%
- 站上 <壓力價位>：xx%｜過程中曾跌破 <支撐價位>：xx%
- 路徑最低點的中位數出現在第 x 根，約 xx.xx：<解讀是否先回測>
- 報告：<artifact 連結或檔案路徑>

**怎麼解讀**：2–3 句，用使用者的數字講（參考 teaching.md 第 5 段）。
**模型的限制**：看不到新聞、機率是抽樣比例、沒有對這檔做過回測驗證。
**下一步可以做**：1–2 個延伸建議。

以上為 AI 模型的研究結果，不構成投資建議。
```

---

## 原則

- **數字只從腳本輸出引用**：不要自己估算或美化結果。模型兩個版本意見不一致時，照實寫。
- **不把比例說成真實機率**：「100 條路徑裡有 84 條收高」不等於「有 84% 機率上漲」。
- **不做投資決定**：不建議買賣、金額、槓桿。使用者要進出場計畫時，提醒要搭配停損，並說明模型的限制。
- **尊重資料來源**：不要繞過網站的流量限制，例如偽裝瀏覽器或大量重試。下載失敗就請使用者稍後再試，或改用 CSV。
- **每次都附上限制說明和免責聲明。**

## 疑難排解

| 狀況 | 處理方式 |
|---|---|
| `Python 3.x … 需要 3.9–3.13` | 請使用者安裝 Python 3.12（python.org），再重跑安裝 |
| PyTorch 安裝失敗或很慢 | 確認網路；公司網路可能擋 download.pytorch.org，腳本會自動改用 PyPI |
| 模型下載失敗（Hugging Face） | 確認能開 huggingface.co；有防火牆或 proxy 時請使用者處理網路設定 |
| 證交所某些月份下載失敗 | 腳本會只用最近連續的月份並發出警告；稍後重跑可補齊 |
| Yahoo 回 429 | 流量限制，稍後再試，或請使用者匯出 CSV |
| `mps` 相關錯誤（Mac） | `forecast.py` 加 `--device cpu` |
| 預測太慢 | 減少路徑數（`--samples 50`）、只跑 small，或縮短 `--pred-len` |

## 檔案

- `scripts/setup_raco.py`：安裝執行環境（Kronos 原始碼、PyTorch 與模型），並做測試預測
- `scripts/fetch_data.py`：下載或整理 K 線資料，輸出標準 CSV；台股會自動還原股票分割
- `scripts/forecast.py`：用 Kronos 產生抽樣路徑
- `scripts/build_report.py`：把結果做成互動 HTML 報告，並輸出統計 JSON
- `assets/report_template.html`：報告樣板
- `references/teaching.md`：第一次使用的教學腳本
