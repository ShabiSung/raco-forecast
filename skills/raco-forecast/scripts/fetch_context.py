#!/usr/bin/env python3
"""收集模型看不到的資訊，輸出 context.json 給 build_report.py 放進報告。

收集的內容：
  - 大盤走勢：台股用加權指數，美股預設用 S&P 500 和那斯達克，可用 --index 換
  - 三大法人買賣超（只有台股）：這檔最近 10 個交易日的外資、投信、自營商，以及整體市場的金額
  - 近期事件：台股會自動查除權除息預告；財報、法說會這支腳本抓不到，請先用網路搜尋確認，
    再用 --events 傳進來

只用 Python 標準函式庫：
  python3 fetch_context.py --data data.csv --symbol 0050 -o context.json
  python3 fetch_context.py --data data.csv --symbol NVDA --index ^GSPC,^IXIC,^SOX \\
      --events "2026-11-17:財報（美股盤後）" -o context.json

--events 用「日期:說明」，多個用分號隔開，日期可以寫 2026-11-17 或 11/17。
說明太長時，可以用「日期:說明|短標」指定圖上顯示的短標（最多 6 個字），例如 "11/18:輝達財報後首個交易日|輝達財報"。
執行完會印出一段 JSON 摘要，寫綜合判讀時直接引用裡面的數字。
"""
import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.error

from fetch_data import get_json, num

TWSE = "https://www.twse.com.tw/rwd/zh"
INDEX_NAMES = {"TAIEX": "加權指數", "^TWII": "加權指數", "^GSPC": "S&P 500", "^IXIC": "那斯達克",
               "^SOX": "費城半導體", "^DJI": "道瓊工業", "^NDX": "那斯達克 100", "BTC-USD": "比特幣"}
warnings = []


def polite_get(url, delay=3, retry_wait=8):
    """證交所有流量限制：每次請求前等幾秒，失敗只重試一次。"""
    time.sleep(delay)
    for attempt in range(2):
        try:
            return get_json(url)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            if attempt == 0:
                time.sleep(retry_wait)
    return None


def roc_date(s):
    y, m, d = (int(x) for x in s.strip().split("/"))
    return dt.date(y + 1911 if y < 1911 else y, m, d)


def read_stock(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((dt.date.fromisoformat(r["timestamps"][:10]), float(r["close"])))
    return rows


def market_of(symbol):
    s = symbol.upper()
    if s.isdigit() or s.endswith(".TW") or s.endswith(".TWO"):
        return "tw"
    if s.endswith(("USDT", "USDC", "BUSD")) or s.endswith("-USD"):
        return "crypto"
    return "us"


def pct_change(closes, n):
    return round((closes[-1] / closes[-1 - n] - 1) * 100, 2) if len(closes) > n else None


# ---------------------------------------------------------------- 大盤
def fetch_yahoo_index(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('^', '%5E')}?range=6mo&interval=1d"
    try:
        d = get_json(url)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        warnings.append(f"{INDEX_NAMES.get(sym, sym)} 下載失敗（{e}）")
        return None
    res = d["chart"]["result"][0]
    out = []
    for ts, c in zip(res.get("timestamp", []), res["indicators"]["quote"][0]["close"]):
        if c is not None:
            t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
            out.append((t.date(), float(c)))
    return out


def fetch_taiex(months=4):
    today = dt.date.today()
    ym = []
    y, m = today.year, today.month
    for _ in range(months):
        ym.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    out = []
    for y, m in reversed(ym):
        d = polite_get(f"{TWSE}/TAIEX/MI_5MINS_HIST?date={y}{m:02d}01&response=json")
        if not d or d.get("stat") != "OK":
            warnings.append(f"加權指數 {y}-{m:02d} 下載失敗")
            continue
        for r in d.get("data", []):
            c = num(r[4])
            if c is not None:
                out.append((roc_date(r[0]), c))
    out.sort()
    if len(out) < 60:
        warnings.append("證交所的加權指數資料不足，改用 Yahoo 財經")
        return fetch_yahoo_index("^TWII")
    return out


def summarize_index(sym, series, stock):
    closes = [c for _, c in series]
    if len(closes) < 25:
        return None
    ma = lambda n: round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None
    ma20, ma60, last = ma(20), ma(60), closes[-1]
    if ma60 and last > ma20 > ma60:
        trend = "多頭排列"
    elif ma60 and last < ma20 < ma60:
        trend = "空頭排列"
    else:
        trend = "盤整"
    chg20 = pct_change(closes, 20)
    s_closes = [c for _, c in stock]
    s_chg20 = pct_change(s_closes, 20)
    return {
        "symbol": sym, "name": INDEX_NAMES.get(sym, sym), "asof": series[-1][0].isoformat(),
        "last": round(last, 2), "chg1": pct_change(closes, 1), "chg5": pct_change(closes, 5), "chg20": chg20,
        "ma20": ma20, "ma60": ma60, "above_ma20": last > ma20, "above_ma60": (last > ma60) if ma60 else None,
        "trend": trend,
        "rel20": round(s_chg20 - chg20, 2) if (s_chg20 is not None and chg20 is not None) else None,
        "spark": {"dates": [d.isoformat() for d, _ in series[-60:]], "close": [round(c, 2) for c in closes[-60:]]},
    }


# ---------------------------------------------------------------- 三大法人（台股）
def find_col(fields, *names):
    for n in names:
        for i, f in enumerate(fields):
            if f.replace(" ", "") == n:
                return i
    return None


def fetch_institutional(code, dates):
    days = []
    for d in dates:
        j = polite_get(f"{TWSE}/fund/T86?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json")
        if not j or j.get("stat") != "OK":
            if j and d == dates[-1]:
                warnings.append(f"{d:%m/%d} 的三大法人資料證交所還沒公布（通常下午 3 點到 4 點半之間），先用到前一個交易日")
            else:
                warnings.append(f"三大法人 {d:%m/%d} 下載失敗")
            continue
        fs = j["fields"]
        ci = find_col(fs, "證券代號")
        cf1 = find_col(fs, "外陸資買賣超股數(不含外資自營商)")
        cf2 = find_col(fs, "外資自營商買賣超股數")
        ct = find_col(fs, "投信買賣超股數")
        cd = find_col(fs, "自營商買賣超股數")
        call = find_col(fs, "三大法人買賣超股數")
        if None in (ci, cf1, ct, cd, call):
            warnings.append("三大法人資料欄位和預期不同，略過")
            return None
        row = next((r for r in j["data"] if r[ci].strip() == code), None)
        if row is None:
            days.append({"date": d.isoformat(), "foreign": 0, "trust": 0, "dealer": 0, "total": 0})
            continue
        lots = lambda i: round((num(row[i]) or 0) / 1000) if i is not None else 0
        days.append({"date": d.isoformat(), "foreign": lots(cf1) + lots(cf2), "trust": lots(ct),
                     "dealer": lots(cd), "total": lots(call)})
    if not days:
        return None
    def total(k, n):
        return sum(x[k] for x in days[-n:])
    keys = ("foreign", "trust", "dealer", "total")
    sums = {"sum5": {k: total(k, 5) for k in keys}, "sum_all": {k: total(k, len(days)) for k in keys}}
    streak_key = days[-1]["foreign"] > 0
    streak = 0
    for x in reversed(days):
        if (x["foreign"] > 0) == streak_key and x["foreign"] != 0:
            streak += 1
        else:
            break
    return {"unit": "張", "days": days, **sums, "n": len(days),
            "foreign_streak": {"direction": "買超" if streak_key else "賣超", "days": streak}}


def fetch_market_flows(dates):
    j, d = None, None
    for d in reversed(dates[-3:]):          # 當天還沒公布時，往前找最近一天
        j = polite_get(f"{TWSE}/fund/BFI82U?dayDate={d:%Y%m%d}&type=day&response=json")
        if j and j.get("stat") == "OK":
            break
    if not j or j.get("stat") != "OK":
        warnings.append("整體市場的三大法人金額下載失敗")
        return None
    out = {"date": d.isoformat(), "unit": "億元"}
    for r in j["data"]:
        name, diff = r[0].replace(" ", ""), num(r[3])
        if diff is None:
            continue
        yi = round(diff / 1e8, 1)
        if name.startswith("外資及陸資"):
            out["foreign"] = out.get("foreign", 0) + yi
        elif name == "外資自營商":
            out["foreign"] = round(out.get("foreign", 0) + yi, 1)
        elif name == "投信":
            out["trust"] = yi
        elif name.startswith("自營商"):
            out["dealer"] = round(out.get("dealer", 0) + yi, 1)
        elif name == "合計":
            out["total"] = yi
    return out


def fetch_exright(code):
    j = polite_get(f"{TWSE}/exRight/TWT48U?response=json")
    if not j or j.get("stat") != "OK":
        warnings.append("除權除息預告下載失敗")
        return []
    fs = j["fields"]
    cdate, ccode, ckind = find_col(fs, "除權除息日期"), find_col(fs, "股票代號"), find_col(fs, "除權息")
    ccash = next((i for i, f in enumerate(fs) if "現金股利" in f), None)
    if None in (cdate, ccode, ckind):
        return []
    out = []
    for r in j["data"]:
        if r[ccode].strip() != code:
            continue
        label = r[ckind].strip()
        if label == "息":
            label = "除息"
        elif label == "權":
            label = "除權"
        elif label in ("權息", "除權息"):
            label = "除權息"
        cash = num(r[ccash]) if ccash is not None else None
        if cash:
            label += f"（每股配 {cash:g} 元）"
        out.append({"date": roc_date(r[cdate]).isoformat(), "label": label, "source": "證交所除權除息預告"})
    return out


# ---------------------------------------------------------------- 事件
def parse_events(s, ref):
    out = []
    for part in [p for p in s.split(";") if p.strip()]:
        when, _, label = part.partition(":")
        when = when.strip()
        try:
            if "-" in when:
                d = dt.date.fromisoformat(when)
            else:
                m, dd = (int(x) for x in when.split("/"))
                d = dt.date(ref.year, m, dd)
                if d < ref - dt.timedelta(days=60):
                    d = dt.date(ref.year + 1, m, dd)
        except ValueError:
            sys.exit(f"看不懂的事件日期：{when}（請用 2026-11-17 或 11/17）")
        label, _, short = label.partition("|")
        e = {"date": d.isoformat(), "label": label.strip() or "事件", "source": "使用者／搜尋"}
        if short.strip():
            e["short"] = short.strip()
        out.append(e)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="fetch_data.py 產生的 CSV")
    ap.add_argument("--symbol", required=True, help="代號，例如 0050、NVDA、BTCUSDT")
    ap.add_argument("--index", default=None, help="大盤指數（Yahoo 代號，逗號分隔），例如 ^GSPC,^IXIC,^SOX；台股預設 TAIEX")
    ap.add_argument("--events", default="", help='近期事件，例如 "2026-11-17:財報（美股盤後）;11/20:法說會"')
    ap.add_argument("--inst-days", type=int, default=10, help="三大法人要看幾個交易日（預設 10）")
    ap.add_argument("--no-institutional", action="store_true", help="台股不抓三大法人")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    stock = read_stock(a.data)
    code = a.symbol.upper().replace(".TWO", "").replace(".TW", "")
    market = market_of(a.symbol)
    last_date = stock[-1][0]
    ctx = {"symbol": a.symbol, "market": market, "asof": last_date.isoformat(),
           "indices": [], "institutional": None, "market_flows": None, "events": []}

    if a.index:
        idx = [s.strip() for s in a.index.split(",") if s.strip()]
    else:
        idx = {"tw": ["TAIEX"], "us": ["^GSPC", "^IXIC"]}.get(market, [] if code.startswith("BTC") else ["BTC-USD"])
    for sym in idx:
        series = fetch_taiex() if sym == "TAIEX" else fetch_yahoo_index(sym)
        summary = summarize_index(sym, series, stock) if series else None
        if summary:
            ctx["indices"].append(summary)

    if market == "tw" and not a.no_institutional:
        dates = [d for d, _ in stock[-a.inst_days:]]
        ctx["institutional"] = fetch_institutional(code, dates)
        ctx["market_flows"] = fetch_market_flows(dates)
        ctx["events"] += fetch_exright(code)

    ctx["events"] += parse_events(a.events, last_date)
    ctx["events"].sort(key=lambda e: e["date"])
    ctx["warnings"] = warnings

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, separators=(",", ":"))

    brief = {k: v for k, v in ctx.items() if k not in ("indices", "institutional")}
    brief["indices"] = [{k: v for k, v in i.items() if k != "spark"} for i in ctx["indices"]]
    if ctx["institutional"]:
        inst = ctx["institutional"]
        brief["institutional"] = {k: v for k, v in inst.items() if k != "days"}
        brief["institutional"]["last_day"] = inst["days"][-1]
    print(json.dumps(brief, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
