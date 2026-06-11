"""Métricas de GPU via nvidia-smi"""

import subprocess
import config

from monitoring.logs import add_log


# stats se inyecta desde app.py
stats = {}


def get_metrics(gpu: int) -> dict:
    """Obtiene métricas de GPU via nvidia-smi"""
    try:
        out = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=temperature.gpu,utilization.gpu,memory.used,power.draw",
            "--format=csv,noheader,nounits",
            "-i", str(gpu)
        ]).decode().strip()

        t, u, m, p = out.split(", ")

        return {
            "temp": int(t),
            "util": int(u),
            "mem": int(m),
            "power": float(p)
        }

    except Exception:
        return {"temp": 0, "util": 0, "mem": 0, "power": 0.0}


def check_throttling(gpu: int, stats_dict: dict):
    """Detecta posibles throttlings"""

    s = stats_dict.get(gpu, {})

    avg = s.get("speed_avg", 0)
    cur = s.get("speed", 0)

    if avg > 0 and cur > 0 and len(s.get("speed_history", [])) >= 10:

        drop_pct = ((avg - cur) / avg) * 100

        if drop_pct >= config.THROTTLE_SPEED_PCT:
            add_log(
                f"⚠️ GPU {gpu} posible throttling: "
                f"{cur:.1f} MK/s vs media {avg:.1f} MK/s ({drop_pct:.0f}% bajada)",
                "WARN"
            )