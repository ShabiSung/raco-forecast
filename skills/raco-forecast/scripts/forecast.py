#!/usr/bin/env python3
"""產生未來 K 線的抽樣路徑（Raco 的市場預測 AI）。

請用 setup_raco.py 建好的 Python 執行（路徑記在 ~/.raco-forecast/config.json 的 "python"）：
  <config 裡的 python> forecast.py --data data.csv --pred-len 20 --models small,base --samples 100,50 -o result.json

每條路徑都是模型「擲一次骰子」得到的一種可能走勢；路徑越多，區間越穩定，但也越久。
CPU 參考時間（225 根日 K、預測 20 根）：小模型 100 條約 3–6 分鐘，大模型 50 條約 8–15 分鐘。
"""
import argparse
import json
import os
import sys
import time

CONFIG = os.path.join(os.path.expanduser("~"), ".raco-forecast", "config.json")
if not os.path.exists(CONFIG):
    sys.exit("找不到 Raco 的市場預測 AI 的安裝設定。請先執行 setup_raco.py。")
CFG = json.load(open(CONFIG, encoding="utf-8"))
sys.path.insert(0, CFG.get("engine_dir") or CFG["kronos_dir"])   # kronos_dir：舊版設定

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import torch  # noqa: E402
from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

REPOS = {"small": "NeoQuasar/Kronos-small", "base": "NeoQuasar/Kronos-base"}
TOKENIZER = "NeoQuasar/Kronos-Tokenizer-base"
LABELS = {"small": "小模型", "base": "大模型"}


def future_times(ts, n):
    diffs = ts.diff().dropna()
    med = diffs.median()
    last = ts.iloc[-1]
    if med >= pd.Timedelta(hours=20):
        if (ts.dt.day_of_week >= 5).any():          # 加密貨幣等週末也交易
            return pd.Series(pd.date_range(last + pd.Timedelta(days=1), periods=n, freq="D")), "1d-7"
        return pd.Series(pd.bdate_range(last + pd.Timedelta(days=1), periods=n)), "1d"
    return pd.Series([last + med * (i + 1) for i in range(n)]), f"{int(med.total_seconds() // 60)}m"


def pick_device(name):
    if name != "auto":
        return name
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def sig(x, n=6):
    return float(f"{x:.{n}g}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="fetch_data.py 產生的 CSV")
    ap.add_argument("--pred-len", type=int, default=20, help="要預測幾根 K（預設 20）")
    ap.add_argument("--models", default="small", help="small、base，逗號分隔（預設 small）")
    ap.add_argument("--samples", default="100", help="每個模型幾條路徑，可用逗號分別指定，例如 100,50")
    ap.add_argument("--lookback", type=int, default=400, help="最多讀入幾根歷史 K（上限 512）")
    ap.add_argument("--device", default="auto", help="auto / cpu / cuda / mps")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=0.9)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--chunk", type=int, default=10, help="每批同時產生幾條路徑")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    counts = [int(s) for s in a.samples.split(",")]
    counts = (counts * len(models))[:len(models)] if len(counts) == 1 else counts
    if len(counts) != len(models):
        sys.exit("--samples 的數量要和 --models 一樣多，或只給一個數字")
    missing = [m for m in models if m not in CFG.get("models", [])]
    if missing:
        sys.exit(f"還沒下載模型：{missing}。請執行 setup_raco.py --models {','.join(models)}")

    df = pd.read_csv(a.data, parse_dates=["timestamps"])
    lookback = min(a.lookback, 512, len(df))
    ctx = df.tail(lookback).reset_index(drop=True)
    fut, interval = future_times(ctx["timestamps"], a.pred_len)
    x = ctx[["open", "high", "low", "close", "volume", "amount"]].astype(float)
    device = pick_device(a.device)
    tok = KronosTokenizer.from_pretrained(TOKENIZER)

    result = {
        "meta": {
            "data_file": os.path.basename(a.data), "bars_used": lookback, "bars_total": len(df),
            "first": ctx["timestamps"].iloc[0].strftime("%Y-%m-%d %H:%M"),
            "last": ctx["timestamps"].iloc[-1].strftime("%Y-%m-%d %H:%M"),
            "last_close": float(ctx["close"].iloc[-1]), "interval": interval, "pred_len": a.pred_len,
            "temperature": a.temperature, "top_p": a.top_p, "device": device,
            "engine_commit": CFG.get("engine_commit") or CFG.get("kronos_commit", ""), "created": time.strftime("%Y-%m-%d %H:%M"),
        },
        "dates": [t.strftime("%Y-%m-%d %H:%M") for t in fut],
        "models": {},
    }
    for mi, (name, n) in enumerate(zip(models, counts)):
        torch.manual_seed(a.seed + mi)
        np.random.seed(a.seed + mi)
        model = Kronos.from_pretrained(REPOS[name])
        pred = KronosPredictor(model, tok, device=device, max_context=512)
        paths, t0 = [], time.time()
        while len(paths) < n:
            k = min(a.chunk, n - len(paths))
            outs = pred.predict_batch([x] * k, [ctx["timestamps"]] * k, [fut] * k, pred_len=a.pred_len,
                                      T=a.temperature, top_k=0, top_p=a.top_p, sample_count=1, verbose=False)
            paths += [[sig(v) for v in o["close"].tolist()] for o in outs]
            el = time.time() - t0
            eta = el / len(paths) * (n - len(paths))
            print(f"{LABELS[name]}：{len(paths)}/{n} 條（已用 {el:.0f} 秒，約剩 {eta:.0f} 秒）", flush=True)
        result["models"][name] = {"samples": n, "seconds": round(time.time() - t0), "close": paths}
        with open(a.out, "w", encoding="utf-8") as f:       # 每個模型跑完就先存檔
            json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
        end = sorted(p[-1] for p in paths)
        print(f"{LABELS[name]}完成：第 {a.pred_len} 根中位數 {end[len(end) // 2]:.4g}", flush=True)
    print(json.dumps({"out": a.out, "models": {m: v["samples"] for m, v in result["models"].items()},
                      "last_close": result["meta"]["last_close"], "interval": interval}, ensure_ascii=False))


if __name__ == "__main__":
    main()
