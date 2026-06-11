"""Auto-benchmark para encontrar parámetros óptimos por GPU"""

import subprocess
import json
import time
import os
from datetime import datetime
import config (
    BITCRACK_BIN, BITCRACK_ARGS, ADDRESS, GLOBAL_START,
    BASE_DIR, GPU_COUNT
)
from monitoring.logs import add_log

BENCH_SECS = 15
BENCH_RESULTS_FILE = os.path.join(BASE_DIR, "benchmark_results.json")

BENCH_CANDIDATES = [
    ["-b", "56", "-t", "256", "-p", "1024"],
    ["-b", "96", "-t", "256", "-p", "1024"],
    ["-b", "112", "-t", "256", "-p", "1024"],
    ["-b", "128", "-t", "256", "-p", "1024"],
    ["-b", "96", "-t", "256", "-p", "2048"],
    ["-b", "112", "-t", "256", "-p", "2048"],
    ["-b", "128", "-t", "256", "-p", "2048"],
    ["-b", "64", "-t", "256", "-p", "4096"],
    ["-b", "96", "-t", "256", "-p", "4096"],
]

speed_re = None


def _get_speed_re():
    global speed_re
    if speed_re is None:
        import re
        speed_re = re.compile(r'([\d.]+)\s*MKey/s\s*\(([\d,]+)\s*total\)')
    return speed_re


def _bench_one(gpu: int, params: list, secs: int) -> float:
    """Prueba una combinación de parámetros y devuelve MKey/s medio"""
    import re
    bench_start = GLOBAL_START
    bench_end = bench_start + 0xFFFFFFFFFF

    cmd = [
        BITCRACK_BIN, "-d", str(gpu),
        *params,
        "--keyspace", f"{bench_start:x}:{bench_end:x}",
        ADDRESS
    ]

    speeds = []
    speed_re = _get_speed_re()

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        deadline = time.time() + secs

        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                m = speed_re.search(line)
                if m:
                    speeds.append(float(m.group(1)))

    except Exception as e:
        print(f"  [BENCH] Error GPU {gpu}: {e}")

    finally:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass

    valid = speeds[2:] if len(speeds) > 2 else speeds
    return round(sum(valid) / len(valid), 2) if valid else 0.0


def run_benchmark(gpu: int, force: bool = False) -> list:
    """Ejecuta benchmark en una GPU y devuelve mejores parámetros"""
    results_all = {}

    if os.path.exists(BENCH_RESULTS_FILE) and not force:
        try:
            with open(BENCH_RESULTS_FILE) as f:
                results_all = json.load(f)
        except Exception:
            pass

    key = str(gpu)
    if key in results_all and not force:
        best = results_all[key]["best_params"]
        speed = results_all[key]["best_speed"]
        print(f"  [BENCH] GPU {gpu}: usando caché → {best} ({speed} MKey/s)")
        return best

    print(f"\n[BENCH] GPU {gpu}: probando {len(BENCH_CANDIDATES)} combinaciones...")

    best_speed = 0.0
    best_params = BENCH_CANDIDATES[1]

    for params in BENCH_CANDIDATES:
        label = " ".join(params)
        print(f"  Probando: {label[:40]}...", end="", flush=True)
        speed = _bench_one(gpu, params, BENCH_SECS)
        print(f" {speed} MKey/s")

        if speed > best_speed:
            best_speed = speed
            best_params = params

    print(f"  [BENCH] GPU {gpu}: MEJOR → {best_params} ({best_speed} MKey/s)")

    results_all[key] = {
        "best_params": best_params,
        "best_speed": best_speed,
        "candidates": [{"params": p, "speed": _bench_one(gpu, p, 5)} for p in BENCH_CANDIDATES[:3]],
        "ts": datetime.now().isoformat()
    }

    try:
        with open(BENCH_RESULTS_FILE, "w") as f:
            json.dump(results_all, f, indent=2)
    except Exception as e:
        print(f"  [BENCH] No se pudo guardar: {e}")

    return best_params


def run_benchmark_if_needed(force: bool = False):
    """Ejecuta benchmark en todas las GPUs si es necesario"""
    import config GPU_COUNT, BITCRACK_BIN
    import os

    if not os.path.isfile(BITCRACK_BIN):
        add_log("Benchmark saltado: binario no encontrado", "WARN")
        return

    print("\n" + "=" * 55)
    print("  AUTO-BENCHMARK — buscando parámetros óptimos por GPU")
    if force:
        print("  (modo forzado — ignorando caché)")
    print("=" * 55)

    results = {}
    for i in range(GPU_COUNT):
        best = run_benchmark(i, force=force)
        results[i] = best

    print("=" * 55 + "\n")
    return results
   