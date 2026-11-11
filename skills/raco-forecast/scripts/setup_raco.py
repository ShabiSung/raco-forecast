#!/usr/bin/env python3
"""安裝 Raco AI 預測的執行環境（可重複執行，已完成的步驟會自動跳過）。

做的事：
  1. 在 ~/.raco-forecast 建立工作資料夾（以前裝過 ~/.kronos-forecast 的話直接搬過來）
  2. 下載 Kronos 原始碼（固定在測試過的版本）
  3. 建立 Python 虛擬環境，安裝 PyTorch 等套件
  4. 從 Hugging Face 下載模型權重
  5. 跑一次小測試，確認能正常預測

用法：
  python3 setup_raco.py                 # 安裝 Kronos-small 與 Kronos-base
  python3 setup_raco.py --models small  # 只裝 small（約 100 MB，比較快）
  python3 setup_raco.py --check         # 只檢查目前狀態

只用 Python 標準函式庫，Windows 請用 `py setup_raco.py`。
"""
import argparse
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile

KRONOS_REPO = "https://github.com/shiyu-coder/Kronos.git"
KRONOS_COMMIT = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
HOME = os.path.join(os.path.expanduser("~"), ".raco-forecast")
OLD_HOME = os.path.join(os.path.expanduser("~"), ".kronos-forecast")   # 舊版名稱
KRONOS_DIR = os.path.join(HOME, "Kronos")
VENV_DIR = os.path.join(HOME, "venv")
CONFIG = os.path.join(HOME, "config.json")
MODELS = {
    "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
    "small": "NeoQuasar/Kronos-small",
    "base": "NeoQuasar/Kronos-base",
}
PACKAGES = ["numpy", "pandas>=2.2.3,<4", "einops==0.8.1", "huggingface_hub>=0.33,<1.0", "safetensors", "tqdm"]


def step(msg):
    print(f"\n==> {msg}", flush=True)


def run(cmd, **kw):
    shown = [c if len(c) < 120 else "<內嵌腳本>" for c in cmd]
    print("    $ " + " ".join(shown), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def venv_python():
    if os.name == "nt":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def check_python():
    v = sys.version_info
    if v < (3, 9) or v >= (3, 14):
        sys.exit(f"目前的 Python 是 {v.major}.{v.minor}。PyTorch 需要 Python 3.9–3.13，請安裝其中一個版本後再執行。")


def get_kronos():
    if os.path.isdir(os.path.join(KRONOS_DIR, "model")):
        print("    已存在，跳過")
        return
    os.makedirs(HOME, exist_ok=True)
    if shutil.which("git"):
        try:
            run(["git", "clone", "--quiet", KRONOS_REPO, KRONOS_DIR])
            run(["git", "-C", KRONOS_DIR, "checkout", "--quiet", KRONOS_COMMIT])
            return
        except subprocess.CalledProcessError:
            shutil.rmtree(KRONOS_DIR, ignore_errors=True)
            print("    git 下載失敗，改用 zip 下載")
    url = f"https://codeload.github.com/shiyu-coder/Kronos/zip/{KRONOS_COMMIT}"
    print(f"    下載 {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = z.namelist()[0].split("/")[0]
        z.extractall(HOME)
    os.replace(os.path.join(HOME, root), KRONOS_DIR)


def has_modules(py, mods):
    code = "import " + ", ".join(mods)
    return subprocess.run([py, "-c", code], capture_output=True).returncode == 0


def make_venv():
    py = venv_python()
    if not os.path.exists(py):
        run([sys.executable, "-m", "venv", VENV_DIR])
        run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    if not has_modules(py, ["torch"]):
        step("安裝 PyTorch（第一次會下載數百 MB，請耐心等候）")
        installed = False
        if platform.system() == "Linux":
            # Linux 的預設 torch 會連 CUDA 一起下載（數 GB），先試 CPU 版
            try:
                run([py, "-m", "pip", "install", "--quiet", "torch", "--index-url",
                     "https://download.pytorch.org/whl/cpu"])
                installed = True
            except subprocess.CalledProcessError:
                print("    CPU 版下載失敗，改用一般版本")
        if not installed:
            run([py, "-m", "pip", "install", "--quiet", "torch"])
    if not has_modules(py, ["pandas", "einops", "huggingface_hub", "safetensors", "tqdm"]):
        step("安裝其他套件")
        run([py, "-m", "pip", "install", "--quiet"] + PACKAGES)
    else:
        print("    套件已安裝，跳過")
    return py


def download_models(py, wanted):
    repos = [MODELS["tokenizer"]] + [MODELS[m] for m in wanted]
    code = (
        "import sys\n"
        "from huggingface_hub import snapshot_download\n"
        "for r in sys.argv[1:]:\n"
        "    print('    ', r, '->', snapshot_download(r), flush=True)\n"
    )
    run([py, "-c", code] + repos)


def smoke_test(py, model):
    code = f"""
import sys
sys.path.insert(0, {KRONOS_DIR!r})
import numpy as np, pandas as pd
from model import Kronos, KronosTokenizer, KronosPredictor
rng = np.random.default_rng(0)
close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 120)))
df = pd.DataFrame({{"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                   "volume": 1e6, "amount": close * 1e6}})
ts = pd.Series(pd.bdate_range("2025-01-01", periods=120))
fut = pd.Series(pd.bdate_range(ts.iloc[-1] + pd.Timedelta(days=1), periods=3))
tok = KronosTokenizer.from_pretrained({MODELS['tokenizer']!r})
m = Kronos.from_pretrained({MODELS[model]!r})
p = KronosPredictor(m, tok, device="cpu", max_context=512)
out = p.predict(df, ts, fut, pred_len=3, sample_count=1, verbose=False)
print("    測試預測 OK：", [round(v, 2) for v in out["close"].tolist()])
"""
    run([py, "-c", code])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="small,base", help="要下載的模型：small、base，逗號分隔（預設兩個都裝）")
    ap.add_argument("--check", action="store_true", help="只檢查狀態，不安裝")
    a = ap.parse_args()
    wanted = [m.strip() for m in a.models.split(",") if m.strip()]
    for m in wanted:
        if m not in ("small", "base"):
            sys.exit(f"不認得的模型：{m}（只能是 small 或 base）")

    if a.check:
        if not os.path.exists(CONFIG) and os.path.isdir(OLD_HOME):
            print(f"找到舊版安裝 {OLD_HOME}。執行 python3 setup_raco.py 就會搬過來，不用重新下載。")
            sys.exit(1)
        ok = os.path.exists(CONFIG)
        print(json.dumps(json.load(open(CONFIG, encoding="utf-8")), ensure_ascii=False, indent=2) if ok
              else "尚未安裝。請執行：python3 setup_raco.py")
        sys.exit(0 if ok else 1)

    check_python()
    if not os.path.exists(HOME) and os.path.isdir(OLD_HOME):
        step(f"沿用舊版安裝：{OLD_HOME} → {HOME}")
        os.rename(OLD_HOME, HOME)
        if os.path.exists(CONFIG):
            os.remove(CONFIG)          # 路徑已經變了，最後會重寫
    step(f"準備工作資料夾 {HOME}")
    os.makedirs(HOME, exist_ok=True)
    step("下載 Kronos 原始碼")
    get_kronos()
    step("建立 Python 虛擬環境")
    py = make_venv()
    step("下載模型權重（Hugging Face）")
    download_models(py, wanted)
    step("執行測試預測")
    smoke_test(py, wanted[0])

    old = json.load(open(CONFIG, encoding="utf-8")) if os.path.exists(CONFIG) else {}
    models = sorted(set(old.get("models", [])) | set(wanted), key=["small", "base"].index)
    cfg = {"python": py, "kronos_dir": KRONOS_DIR, "kronos_commit": KRONOS_COMMIT, "models": models}
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    step("安裝完成")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print("\n之後執行預測請用這個 Python：\n    " + py)


if __name__ == "__main__":
    main()
