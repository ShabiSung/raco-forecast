#!/usr/bin/env python3
"""把 Raco AI 預測（Kronos 模型）的結果做成互動網頁報告（單一 HTML 檔，可直接發布成 artifact 或用瀏覽器打開）。

只用 Python 標準函式庫：
  python3 build_report.py --data data.csv --result result.json --name 元大台灣50 --symbol 0050 \
      --source-label 臺灣證券交易所 --levels "97.15:缺口上緣;91.8:頸線" -o report.html

--levels 用「價位:說明」，多個用分號隔開；不給的話可用 --auto-levels 自動抓近期高低點。
執行完會印出一段 JSON 統計，回覆使用者時直接引用裡面的數字。
"""
import argparse
import csv
import datetime as dt
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "report_template.html")


def pct(sorted_vals, q):
    pos = (len(sorted_vals) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def stats(paths, last, levels):
    n = len(paths[0])
    end = sorted(p[-1] for p in paths)
    col = lambda d: sorted(p[d] for p in paths)
    mins = [min(p) for p in paths]
    argmin = sorted(p.index(min(p)) + 1 for p in paths)
    out = {
        "paths": len(paths),
        "median": {f"第{d}根": round(pct(col(d - 1), 0.5), 6) for d in sorted({min(5, n), min(10, n), n})},
        "end_p10_p90": [round(pct(end, 0.1), 6), round(pct(end, 0.9), 6)],
        "end_p25_p75": [round(pct(end, 0.25), 6), round(pct(end, 0.75), 6)],
        "prob_end_above_last": round(sum(v > last for v in end) / len(end), 3),
        "median_change_pct": round((pct(end, 0.5) / last - 1) * 100, 2),
        "median_low_close": round(pct(sorted(mins), 0.5), 6),
        "median_day_of_low": argmin[len(argmin) // 2],
        "levels": {},
    }
    for p, label in levels:
        key = f"{p} {label}"
        if p > last:
            out["levels"][key] = {"prob_end_above": round(sum(v > p for v in end) / len(end), 3)}
        else:
            out["levels"][key] = {"prob_any_close_below": round(sum(m < p for m in mins) / len(mins), 3)}
    return out


def parse_levels(s):
    out = []
    for part in [x for x in s.replace("；", ";").split(";") if x.strip()]:
        price, _, label = part.partition(":")
        out.append((float(price.strip().replace(",", "")), label.strip() or "關鍵價位"))
    return out


def auto_levels(rows, last):
    hi20 = max(r[2] for r in rows[-20:])
    lo20 = min(r[3] for r in rows[-20:])
    lo60 = min(r[3] for r in rows[-60:])
    lv = [(hi20, "近 20 根最高點"), (lo20, "近 20 根最低點")]
    if lo60 < lo20 * 0.98:
        lv.append((lo60, "近 60 根最低點"))
    return [(round(p, 6), t) for p, t in lv if abs(p / last - 1) > 0.002]


def interval_words(interval):
    if interval == "1d":
        return "日 K", "個交易日"
    if interval == "1d-7":
        return "日 K", "天"
    mins = int(interval.rstrip("m")) if interval.rstrip("m").isdigit() else 0
    if mins and mins % 60 == 0:
        return f"{mins // 60} 小時 K", "根 K"
    return (f"{mins} 分鐘 K" if mins else "K 線"), "根 K"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--name", required=True, help="標的名稱，例如 元大台灣50、比特幣")
    ap.add_argument("--symbol", required=True, help="代號，例如 0050、BTCUSDT")
    ap.add_argument("--source-label", default="使用者提供的資料", help="資料來源，例如 臺灣證券交易所、Binance")
    ap.add_argument("--levels", default="", help='關鍵價位，例如 "97.15:缺口上緣;91.8:頸線"')
    ap.add_argument("--auto-levels", action="store_true", help="沒給 --levels 時，自動用近期高低點")
    ap.add_argument("--conv", choices=["tw", "us"], default=None, help="K 棒顏色：tw 紅漲綠跌、us 綠漲紅跌")
    ap.add_argument("--volume-unit", choices=["lots", "raw"], default=None, help="lots：股數換算成張（台股）")
    ap.add_argument("--hist-bars", type=int, default=60, help="報告裡顯示幾根歷史 K（預設 60）")
    ap.add_argument("--headline", default=None, help="自訂標題（可用 <em>重點</em> 標示強調）")
    ap.add_argument("--lede", default=None, help="自訂標題下的說明")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    rows = []
    with open(a.data, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([r["timestamps"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"] or 0)])
    res = json.load(open(a.result, encoding="utf-8"))
    meta = res["meta"]
    last = rows[-1][4]
    levels = parse_levels(a.levels) if a.levels else (auto_levels(rows, last) if a.auto_levels else [])
    tw_like = a.symbol.isdigit() or a.symbol.upper().endswith(".TW")
    conv = a.conv or ("tw" if tw_like else "us")
    vol_unit = a.volume_unit or ("lots" if tw_like else "raw")
    interval_label, unit_word = interval_words(meta["interval"])
    n = meta["pred_len"]

    paths = {k: v["close"] for k, v in res["models"].items()}
    pooled = [p for v in paths.values() for p in v]
    st = {"pooled": stats(pooled, last, levels)}
    for k, v in paths.items():
        st[k] = stats(v, last, levels)

    s = st["pooled"]
    up, chg = s["prob_end_above_last"], s["median_change_pct"]
    if up >= 0.65 and chg >= 2:
        dip_first = s["median_day_of_low"] <= 3 and s["median_low_close"] < last * 0.98
        lead, phrase = "偏向", ("先回測、再反彈" if dip_first else "上漲")
    elif up <= 0.35 and chg <= -2:
        lead, phrase = "偏向", "下跌"
    else:
        lead, phrase = "", "看不出明確方向"
    headline = a.headline or f"未來 {n} {unit_word}，模型{lead}<em>{phrase}</em>"
    sp = " " if interval_label[0].isdigit() else ""
    lede = a.lede or (f"Raco AI 預測使用專門讀 K 線的開源模型 Kronos。它看完{a.name}（{a.symbol}）最近 {meta['bars_used']} 根{sp}{interval_label}，"
                      f"產生 {len(pooled)} 條「可能的未來」，下面的圖把這些路徑整理成區間。每條路徑都只是一種可能，不是預言。")
    hist = rows[-a.hist_bars:]
    first_d, last_d = meta["first"][:10].replace("-", "/"), meta["last"][:10].replace("-", "/")
    cfg = {
        "name": a.name, "symbol": a.symbol, "conv": conv, "volUnit": vol_unit, "interval": meta["interval"],
        "intervalLabel": interval_label, "sourceLabel": a.source_label, "dataRange": f"{first_d}–{last_d}",
        "bars": meta["bars_used"], "levels": [{"p": p, "label": t} for p, t in levels],
        "headline": headline, "lede": lede, "temperature": meta["temperature"], "top_p": meta["top_p"],
        "created": meta["created"],
        "eyebrow": f"{a.name}（{a.symbol}）‧Raco AI 預測‧{interval_label}‧資料截至 {meta['last'][:16] if 'd' not in meta['interval'] else last_d}",
    }
    data = {"hist": [[r[0][:16]] + [round(x, 6) for x in r[1:]] for r in hist], "dates": res["dates"], "paths": paths}
    dump = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = open(TEMPLATE, encoding="utf-8").read()
    page = page.replace("__TITLE__", html.escape(f"{a.symbol} Raco AI 預測"), 1)
    page = page.replace("__CFG__", dump(cfg), 1).replace("__DATA__", dump(data), 1)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(json.dumps({"out": os.path.abspath(a.out), "last_close": last, "pred_len": n, "unit": unit_word,
                      "headline_phrase": phrase, "levels": levels, "stats": st}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
