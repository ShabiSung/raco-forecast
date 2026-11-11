#!/usr/bin/env python3
"""下載或整理 K 線資料，輸出 Raco AI 預測（Kronos 模型）用的標準 CSV（只用 Python 標準函式庫）。

輸出欄位：timestamps,open,high,low,close,volume,amount

用法：
  python3 fetch_data.py twse 0050 -o data.csv                # 台股上市（證交所日 K，預設抓 24 個月）
  python3 fetch_data.py twse 2330 --months 30 -o data.csv
  python3 fetch_data.py binance BTCUSDT --interval 4h -o data.csv   # 加密貨幣（幣安現貨）
  python3 fetch_data.py yahoo AAPL -o data.csv               # 美股等（Yahoo 財經，可能被限流）
  python3 fetch_data.py csv 我的資料.csv -o data.csv          # 自己下載的 CSV（看盤軟體、券商匯出）

執行完會印出一行 JSON 摘要（筆數、期間、最後收盤、名稱、警告）。
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "raco-forecast-skill/1.1 (+https://github.com/ShabiSung/raco-forecast)"}
warnings = []


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def num(s):
    s = str(s).replace(",", "").strip()
    if s in ("", "--", "---", "X", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- TWSE
def months_back(n):
    today = dt.date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def fetch_twse(code, months):
    rows, name, got = [], "", []
    for i, (y, m) in enumerate(months_back(months)):
        url = (f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={y}{m:02d}01"
               f"&stockNo={code}&response=json")
        data = None
        for attempt in range(2):
            try:
                d = get_json(url)
                if d.get("stat") == "OK":
                    data = d
                elif "沒有符合條件" in str(d.get("stat", "")):
                    data = {"data": []}
                break
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
                time.sleep(8)          # 證交所有流量限制，等一下再試一次
        if data is None:
            got.append(None)
            print(f"  {y}-{m:02d} 下載失敗", file=sys.stderr)
        else:
            got.append((y, m))
            if not name and data.get("title"):
                t = re.sub(r"\s+", " ", data["title"])
                mt = re.search(re.escape(code) + r" (\S+)", t)
                name = mt.group(1) if mt else ""
            for r in data.get("data", []):
                yy, mm, dd = (int(x) for x in r[0].split("/"))
                o, h, lo, c = num(r[3]), num(r[4]), num(r[5]), num(r[6])
                if None in (o, h, lo, c):
                    continue
                rows.append([dt.datetime(yy + 1911, mm, dd), o, h, lo, c, num(r[1]) or 0.0, num(r[2]) or 0.0])
        if i < months - 1:
            time.sleep(3)              # 對證交所禮貌一點，每次間隔 3 秒
    # 只保留「最後一段連續沒缺月」的資料，避免中間斷層
    if None in got:
        last_gap = max(i for i, g in enumerate(got) if g is None)
        keep = [g for g in got[last_gap + 1:] if g]
        missing = sum(1 for g in got if g is None)
        warnings.append(f"有 {missing} 個月份下載失敗，只使用最近連續的 {len(keep)} 個月")
        keep_set = set(keep)
        rows = [r for r in rows if (r[0].year, r[0].month) in keep_set]
    rows.sort(key=lambda r: r[0])
    return adjust_splits(rows), name


def adjust_splits(rows):
    """台股漲跌幅限制 10%，前收和今開相差超過 1.8 倍只可能是分割或合併，自動還原。"""
    for i in range(1, len(rows)):
        prev_c, o = rows[i - 1][4], rows[i][1]
        ratio = prev_c / o if o else 1
        for k in (ratio, 1 / ratio if ratio else 1):
            if k >= 1.8 and abs(k - round(k)) / k < 0.06:
                f = round(k) if ratio > 1 else 1 / round(k)     # 用整數倍還原（例如 1 拆 4）
                for r in rows[:i]:
                    r[1], r[2], r[3], r[4] = r[1] / f, r[2] / f, r[3] / f, r[4] / f
                    r[5] = r[5] * f
                warnings.append(f"{rows[i][0]:%Y-%m-%d} 偵測到股票分割或合併（約 {k:.0f} 倍），之前的價格已自動還原")
                break
    return rows


# ---------------------------------------------------------------- Binance
def fetch_binance(symbol, interval, limit):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    data = get_json(url)
    now_ms = time.time() * 1000
    rows = []
    for k in data:
        if k[6] > now_ms:              # 最後一根還沒收完，不用
            continue
        t = dt.datetime.fromtimestamp(k[0] / 1000, dt.timezone.utc).replace(tzinfo=None)
        rows.append([t, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[7])])
    warnings.append("時間為 UTC")
    return rows, symbol.upper()


# ---------------------------------------------------------------- Yahoo
def fetch_yahoo(symbol, interval, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval={interval}"
    try:
        d = get_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            sys.exit("Yahoo 財經拒絕了這次請求（流量限制）。請稍後再試，或從看盤軟體匯出 CSV，改用 csv 來源。")
        raise
    res = d["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
    rows = []
    for i, ts in enumerate(res.get("timestamp", [])):
        o, h, lo, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, lo, c):
            continue
        f = (adj[i] / c) if adj and adj[i] and c else 1.0       # 用還原收盤價一起調整開高低
        t = dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(tzinfo=None)
        if interval in ("1d", "1wk", "1mo"):
            t = dt.datetime(t.year, t.month, t.day)
        rows.append([t, o * f, h * f, lo * f, c * f, float(v or 0), float(v or 0) * c])
    name = res.get("meta", {}).get("longName") or res.get("meta", {}).get("shortName") or symbol
    return rows, name


# ---------------------------------------------------------------- CSV
ALIASES = {
    "timestamps": ["timestamps", "timestamp", "time", "date", "datetime", "日期", "時間", "交易日期"],
    "open": ["open", "開盤", "開盤價", "開"],
    "high": ["high", "最高", "最高價", "高"],
    "low": ["low", "最低", "最低價", "低"],
    "close": ["close", "收盤", "收盤價", "收", "price", "adj close"],
    "volume": ["volume", "vol", "成交量", "成交股數", "量"],
    "amount": ["amount", "turnover", "成交金額", "成交值", "quote volume"],
}


def parse_time(s):
    s = str(s).strip()
    if re.fullmatch(r"\d{10}(\.\d+)?", s):
        return dt.datetime.fromtimestamp(float(s), dt.timezone.utc).replace(tzinfo=None)
    if re.fullmatch(r"\d{13}", s):
        return dt.datetime.fromtimestamp(int(s) / 1000, dt.timezone.utc).replace(tzinfo=None)
    m = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", s)          # 民國年 115/11/06
    if m and int(m.group(1)) < 200:
        return dt.datetime(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
    s2 = s.replace("/", "-").replace("T", " ").rstrip("Z")
    s2 = re.sub(r"([+-]\d{2}:?\d{2})$", "", s2).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s2[:19], fmt)
        except ValueError:
            pass
    raise ValueError(f"看不懂的時間格式：{s}")


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample else csv.excel
        reader = csv.reader(f, dialect)
        header = [h.strip() for h in next(reader)]
        low = [h.lower() for h in header]
        idx = {}
        for key, names in ALIASES.items():
            for n in names:
                if n.lower() in low:
                    idx[key] = low.index(n.lower())
                    break
        need = ["timestamps", "open", "high", "low", "close"]
        miss = [k for k in need if k not in idx]
        if miss:
            sys.exit(f"CSV 缺少欄位：{miss}。目前的欄位是 {header}")
        rows = []
        for r in reader:
            if not r or len(r) <= max(idx.values()):
                continue
            vals = [num(r[idx[k]]) for k in ["open", "high", "low", "close"]]
            if None in vals:
                continue
            v = num(r[idx["volume"]]) if "volume" in idx else 0.0
            a = num(r[idx["amount"]]) if "amount" in idx else None
            rows.append([parse_time(r[idx["timestamps"]])] + vals + [v or 0.0, a if a is not None else (v or 0.0) * vals[3]])
    if "volume" not in idx:
        warnings.append("CSV 沒有成交量欄位，以 0 代替（預測品質會稍差）")
    rows.sort(key=lambda r: r[0])
    return rows, ""


# ---------------------------------------------------------------- main
def guess_interval(rows):
    if len(rows) < 3:
        return "unknown"
    diffs = sorted((rows[i][0] - rows[i - 1][0]).total_seconds() for i in range(1, len(rows)))
    med = diffs[len(diffs) // 2]
    if med >= 20 * 3600:
        has_weekend = any(r[0].weekday() >= 5 for r in rows)
        return "1d-7" if has_weekend else "1d"
    return f"{int(round(med / 60))}m"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["twse", "binance", "yahoo", "csv"])
    ap.add_argument("symbol", help="代號（0050、BTCUSDT、AAPL）或 CSV 檔案路徑")
    ap.add_argument("-o", "--out", required=True, help="輸出的 CSV 路徑")
    ap.add_argument("--months", type=int, default=24, help="twse：要抓幾個月（預設 24）")
    ap.add_argument("--interval", default=None, help="binance：1h/4h/1d（預設 1d）；yahoo：1d/1h（預設 1d）")
    ap.add_argument("--limit", type=int, default=500, help="binance：抓幾根 K（最多 1000，預設 500）")
    ap.add_argument("--range", default="2y", help="yahoo：期間，例如 1y、2y、5y（預設 2y）")
    a = ap.parse_args()

    if a.source == "twse":
        rows, name = fetch_twse(a.symbol, a.months)
    elif a.source == "binance":
        rows, name = fetch_binance(a.symbol, a.interval or "1d", min(a.limit, 1000))
    elif a.source == "yahoo":
        rows, name = fetch_yahoo(a.symbol, a.interval or "1d", a.range)
    else:
        rows, name = read_csv(a.symbol)

    dedup = {}
    for r in rows:
        dedup[r[0]] = r
    rows = [dedup[k] for k in sorted(dedup)]
    if len(rows) < 60:
        sys.exit(f"只有 {len(rows)} 根 K 線，太少了（至少要 60 根，建議 200 根以上）。")
    if len(rows) < 200:
        warnings.append(f"只有 {len(rows)} 根 K 線，建議 200 根以上預測會比較穩定")

    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamps", "open", "high", "low", "close", "volume", "amount"])
        for r in rows:
            w.writerow([r[0].strftime("%Y-%m-%d %H:%M:%S")] + [f"{x:.10g}" for x in r[1:]])
    summary = {
        "out": a.out, "rows": len(rows), "first": rows[0][0].strftime("%Y-%m-%d %H:%M"),
        "last": rows[-1][0].strftime("%Y-%m-%d %H:%M"), "last_close": rows[-1][4],
        "interval": guess_interval(rows), "name": name, "source": a.source, "warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
