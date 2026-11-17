"""台股、美股的休市日，讓預測日期跳過不開盤的日子（只用 Python 標準函式庫）。

- 台股：從證交所的「市場開休市日期」抓，存在 ~/.raco-forecast/holidays/ 七天內不重抓；
  抓不到時用內建的 2026、2027 年資料。颱風等臨時休市沒辦法事先知道。
- 美股：照紐約證交所的規則自動算（元旦、金恩紀念日、總統日、耶穌受難日、陣亡將士紀念日、
  六月節、獨立紀念日、勞動節、感恩節、聖誕節），遇到週末會照規則補假。
- 加密貨幣：每天都交易，沒有休市日。
"""
import datetime as dt
import json
import os
import re
import time
import urllib.request

TWSE_URL = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?date={year}0101&response=json"
UA = {"User-Agent": "raco-forecast-skill/1.3 (+https://github.com/ShabiSung/raco-forecast)"}
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".raco-forecast", "holidays")
CACHE_DAYS = 7

# 證交所公告的休市日（2026-11 取得），網路抓不到時使用
BUILTIN_TW = {
    2026: {"01-01": "開國紀念日", "02-12": "春節前封關（無交易）", "02-13": "春節前封關（無交易）",
           "02-15": "農曆除夕及春節", "02-16": "農曆除夕及春節", "02-17": "農曆除夕及春節", "02-18": "農曆除夕及春節",
           "02-19": "農曆除夕及春節", "02-20": "農曆除夕及春節", "02-27": "和平紀念日", "02-28": "和平紀念日",
           "04-03": "兒童節及民族掃墓節", "04-04": "兒童節及民族掃墓節", "04-05": "兒童節及民族掃墓節",
           "04-06": "兒童節及民族掃墓節", "05-01": "勞動節", "06-19": "端午節", "09-25": "中秋節", "09-28": "教師節",
           "10-09": "國慶日", "10-10": "國慶日", "10-25": "臺灣光復紀念日", "10-26": "臺灣光復紀念日", "12-25": "行憲紀念日"},
    2027: {"01-01": "開國紀念日", "02-02": "春節前封關（無交易）", "02-03": "春節前封關（無交易）",
           "02-04": "農曆除夕及春節", "02-05": "農曆除夕及春節", "02-06": "農曆除夕及春節", "02-07": "農曆除夕及春節",
           "02-08": "農曆除夕及春節", "02-09": "農曆除夕及春節", "02-10": "農曆除夕及春節", "02-28": "和平紀念日",
           "03-01": "和平紀念日", "04-04": "兒童節及民族掃墓節", "04-05": "兒童節及民族掃墓節", "04-06": "兒童節及民族掃墓節",
           "04-30": "勞動節", "05-01": "勞動節", "06-09": "端午節", "09-15": "中秋節", "09-28": "教師節",
           "10-10": "國慶日", "10-11": "國慶日", "10-25": "臺灣光復紀念日", "12-24": "行憲紀念日", "12-25": "行憲紀念日",
           "12-31": "開國紀念日（補假）"},
}

notes = []          # 給呼叫端顯示的提醒，例如「證交所抓不到，改用內建資料」


def _clean_tw_name(name):
    name = name.strip()
    if name.startswith("市場無交易"):
        return "春節前封關（無交易）"
    if "開國紀念日" in name:
        return "開國紀念日" if not re.match(r"^\d+年", name) else "開國紀念日（補假）"
    if "教師節" in name:
        return "教師節"
    if name.startswith("臺灣光復"):
        return "臺灣光復紀念日"
    return name


def _fetch_tw(year):
    time.sleep(1.5)                          # 證交所有流量限制，連續抓好幾年時要間隔一下
    req = urllib.request.Request(TWSE_URL.format(year=year), headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    rows = d.get("data") or []
    if d.get("stat", "").lower() != "ok" or not rows or not rows[0][0].startswith(str(year)):
        return None
    out = {}
    for day, name, *_ in rows:
        if "開始交易" in name or "最後交易" in name:      # 這兩種是有開盤的日子
            continue
        out[day[5:10]] = _clean_tw_name(name)
    return out


def tw_holidays(year):
    """回傳 {date: 名稱}；完全取不到時回傳 None。"""
    path = os.path.join(CACHE_DIR, f"tw_{year}.json")
    data = None
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_DAYS * 86400:
        data = json.load(open(path, encoding="utf-8"))
    if data is None:
        try:
            data = _fetch_tw(year)
            if data:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
        except Exception:
            data = None
        if data is None and os.path.exists(path):          # 過期的快取也比沒有好
            data = json.load(open(path, encoding="utf-8"))
        if data is None and year in BUILTIN_TW:
            data = BUILTIN_TW[year]
            notes.append(f"證交所 {year} 年休市日抓不到，改用內建資料")
    if data is None:
        notes.append(f"沒有 {year} 年的台股休市日資料，只排除週末")
        return None
    return {dt.date(year, int(k[:2]), int(k[3:])): v for k, v in data.items()}


# ---------------------------------------------------------------- 美股（紐約證交所）
def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(y, month, day)


def _nth_weekday(y, month, weekday, n):
    d = dt.date(y, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(y, month, weekday):
    d = dt.date(y, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(y, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def us_holidays(year):
    h = {}
    ny = dt.date(year, 1, 1)
    if ny.weekday() != 5:                    # 元旦遇到週六時，紐約證交所不在前一天補假
        h[_observed(ny)] = "元旦"
    h[_nth_weekday(year, 1, 0, 3)] = "金恩紀念日"
    h[_nth_weekday(year, 2, 0, 3)] = "總統日"
    h[_easter(year) - dt.timedelta(days=2)] = "耶穌受難日"
    h[_last_weekday(year, 5, 0)] = "陣亡將士紀念日"
    if year >= 2022:
        h[_observed(dt.date(year, 6, 19))] = "六月節"
    h[_observed(dt.date(year, 7, 4))] = "獨立紀念日"
    h[_nth_weekday(year, 9, 0, 1)] = "勞動節"
    h[_nth_weekday(year, 11, 3, 4)] = "感恩節"
    h[_observed(dt.date(year, 12, 25))] = "聖誕節"
    return h


def holidays(market, years):
    out = {}
    for y in sorted(set(years)):
        if market == "tw":
            out.update(tw_holidays(y) or {})
        elif market == "us":
            out.update(us_holidays(y))
    return out


def next_trading_days(last, n, market):
    """從 last 的下一天開始，回傳 n 個交易日，以及中間跳過的休市日 [(日期, 名稱)]。"""
    hol = holidays(market, range(last.year, last.year + 2)) if market in ("tw", "us") else {}
    days, skipped, d = [], [], last
    while len(days) < n:
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:
            continue
        if d in hol:
            skipped.append((d, hol[d]))
            continue
        days.append(d)
    return days, skipped


def guess_market(dates):
    """從歷史資料「哪些平日沒有交易」猜是台股還是美股；猜不出來回傳 None。"""
    dates = sorted(set(dates))
    if len(dates) < 60:
        return None
    have, missing, d = set(dates), [], dates[0]
    while d <= dates[-1]:
        if d.weekday() < 5 and d not in have:
            missing.append(d)
        d += dt.timedelta(days=1)
    if not missing:
        return None
    years = range(dates[0].year, dates[-1].year + 1)
    n_notes = len(notes)
    us = holidays("us", years)
    tw = holidays("tw", years)
    del notes[n_notes:]                      # 猜市場時查不到的年份不用提醒
    us_hits = sum(1 for m in missing if m in us)
    tw_hits = sum(1 for m in missing if m in tw)
    if tw_hits >= 3 and tw_hits > us_hits:
        return "tw"
    if us_hits >= 2 and us_hits > tw_hits:
        return "us"
    return None
