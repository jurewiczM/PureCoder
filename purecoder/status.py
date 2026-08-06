"""
purecoder/status.py

Live system status: is the server up, which model, is the GPU present, are
the pipeline modules importable. Used by `cli.py status` and handy standalone.
"""

import importlib
import os
import shutil
import subprocess

from .client import GRAMMARS_DIR


def _check_server(pc):
    import requests
    try:
        r = requests.get(f"{pc.base_url}/health", timeout=3)
        up = r.status_code == 200
    except Exception:
        return False, None
    model = None
    try:
        props = requests.get(f"{pc.base_url}/props", timeout=3).json()
        model = os.path.basename(props.get("default_generation_settings", {})
                                 .get("model", "") or props.get("model_path", ""))
    except Exception:
        pass
    return up, model


def _check_gpu():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        name, used, total = [x.strip() for x in out.split(",")]
        return f"{name}  {used}/{total} MiB used"
    except Exception:
        return "present (query failed)"


def _check_modules():
    ok = {}
    for m in ["client", "validate", "execute", "scaffold", "rag"]:
        try:
            importlib.import_module(f".{m}", package=__package__)
            ok[m] = True
        except Exception as e:
            ok[m] = f"FAIL: {e}"
    return ok


def _check_grammars():
    d = GRAMMARS_DIR
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.suffix == ".gbnf")


def print_status(pc):
    print("=" * 56)
    print(" PureCoder — system status")
    print("=" * 56)

    up, model = _check_server(pc)
    print(f" server    : {'UP' if up else 'DOWN'}  ({pc.base_url})")
    if model:
        print(f" model     : {model}")

    gpu = _check_gpu()
    print(f" gpu       : {gpu or 'no nvidia-smi found'}")

    grams = _check_grammars()
    print(f" grammars  : {', '.join(grams) if grams else 'none found in grammars/'}")

    print(" modules   :")
    for m, v in _check_modules().items():
        print(f"     {'ok  ' if v is True else 'FAIL'} {m}"
              + ("" if v is True else f"  ({v})"))

    print("=" * 56)
    if not up:
        print(" ! server down — start it with:")
        # Measured on a 6 GB card, Q5_K_M: 24 of 29 layers and a q8_0 KV cache
        # hold 16k of context in 4.7 GB at 23 tok/s, and leave room for the
        # embedder (~275 MB) that a doc-grounded run needs on the same card.
        # Full offload is faster (35 tok/s) and takes 5.5 GB, which makes every
        # retrieval OOM -- see docs/STATUS.md.
        print("   llama-server -hf Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
              ":Q4_K_M -ngl 24 -c 16384 -fa on -ctk q8_0 -ctv q8_0 "
              "--port 8080")
    print()
