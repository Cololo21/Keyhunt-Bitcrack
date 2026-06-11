"""Endpoints REST API"""

import threading
import time
import os

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

import config

from state.slices import slice_manager
from state.global_state import get_global_state
from core.bandit import bandit
from core.scheduler import slice_to_percent
from gpu.worker import paused_events, stats
from gpu.metrics import get_metrics
from monitoring.logs import logs_global, add_log
from monitoring.heatmap import get_cached_heatmap, rebuild_heatmap_cache, get_heatmap_zoom_detail
from api.middleware import verify_token, log_access

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


# =========================================================
# STATE BUILDER
# =========================================================
def build_state():
    import time
    from config import (
        GPU_COUNT,
        SLICE_COUNT,
        SLICE_SIZE,
        ADDRESS,
        GLOBAL_START,
        GLOBAL_END,
        EXPLORE_RATE,
        GUIDED_SIGMA
    )

    # Detectar si coincurve está disponible
    try:
        import coincurve
        COINCURVE_OK = True
    except ImportError:
        COINCURVE_OK = False

    gs = get_global_state()

    uptime = int(time.time() - getattr(config, "start_time", time.time()))

    total_speed = sum(s.get("speed", 0) for s in stats.values())
    completed_count = slice_manager.get_completed_count()
    global_pct = (completed_count / SLICE_COUNT) * 100 if SLICE_COUNT else 0
    pending = SLICE_COUNT - completed_count

    eta_seconds = None
    if total_speed > 0 and SLICE_SIZE:
        eta_seconds = int((pending * SLICE_SIZE) / (total_speed * 1_000_000))

    gpus = []
    for i in range(GPU_COUNT):
        s = stats[i]

        slice_num = s.get("current_slice")
        slice_start_keys = s.get("slice_start_keys", 0)
        total = s.get("total", 0)
        
        searched = max(0, total - slice_start_keys)
        
        if slice_num is not None and SLICE_SIZE and searched > SLICE_SIZE:
            searched = SLICE_SIZE
        
        if SLICE_SIZE and SLICE_SIZE > 0 and searched >= 0:
            progress = (searched / SLICE_SIZE) * 100
            progress = max(0, min(100, progress))
        else:
            progress = 0
        
        remaining = max(0, SLICE_SIZE - searched) if SLICE_SIZE else 0

        retry_mins = (
            int((time.time() - s.get("retry_since", 0)) / 60)
            if s.get("retry_since") else None
        )

        gpus.append({
            "id": i,
            "slice": slice_num,
            "slice_pct": slice_to_percent(slice_num) if slice_num is not None else 0,
            "speed": max(0, s.get("speed", 0)),
            "total": max(0, total),
            "searched": searched,
            "progress": round(progress, 2),
            "remaining": remaining,
            "status": s.get("status", "UNKNOWN"),
            "restarts": max(0, s.get("restarts", 0)),
            "found": s.get("found", False),
            "metrics": get_metrics(i),
            "retry_mins": retry_mins if retry_mins is not None else 0,
            "speed_history": s.get("speed_history", [])[-30:],
            "paused": not paused_events[i].is_set(),
            "best_fit": max(0, s.get("best_fit", 0)),
            "best_addr": s.get("best_addr", "") or "",
            "best_key": s.get("best_key", "") or "",
            "target_len": 34,
        })

    blocks, heatmap = get_cached_heatmap(slice_manager)

    # =========================================================
    # MEJOR FITNESS (desde GPUs o global)
    # =========================================================
    best_fit = 0
    best_addr = ""
    best_key = ""
    
    for g in gpus:
        if g.get("best_fit", 0) > best_fit:
            best_fit = g.get("best_fit", 0)
            best_addr = g.get("best_addr", "") or ""
            best_key = g.get("best_key", "") or ""
    
    if best_fit == 0:
        best_fit = gs["global_best_fit"]
        best_addr = gs["global_best_addr"] or ""
        best_key = gs["global_best_key"] or ""

    return {
        "uptime": uptime,
        "total_speed": max(0, total_speed),
        "global_pct": max(0, min(100, global_pct)),
        "completed_slices": max(0, completed_count),
        "total_slices": SLICE_COUNT,
        "pending_slices": max(0, pending),
        "eta_seconds": eta_seconds if eta_seconds and eta_seconds > 0 else None,

        "gpus": gpus,
        "blocks": blocks,
        "heatmap": heatmap,
        "logs": logs_global[-50:],

        "address": ADDRESS,
        "global_start": hex(GLOBAL_START),
        "global_end": hex(GLOBAL_END),

        "best_fit": max(0, best_fit),
        "best_addr": best_addr,
        "best_key": best_key,
        "target_len": 34,
        "fitness_ema": round(max(0, gs["fitness_ema"]), 3),
        "explore_sigma": max(0, gs["explore_sigma"]),

        "explore_rate": max(0, min(1, EXPLORE_RATE)),

        "elite_top5": [
            {"key": hex(k), "fit": max(0, f)} 
            for k, f in sorted(gs["elite_history"], key=lambda x: x[1], reverse=True)[:5]
        ],

        "bandit_heatmap": bandit.get_heatmap(),
        "bandit_top": bandit.get_top_regions(10),
        
        "config": {
            "coincurve": COINCURVE_OK,
            "addr_valid": True,
            "guided_sigma": GUIDED_SIGMA
        }
    }


# =========================================================
# INDEX / DASHBOARD
# =========================================================
@router.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard.html")
    try:
        with open(html_path, "r", encoding="utf-8", errors="replace") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>dashboard.html no encontrado</h1>", status_code=404)


# =========================================================
# STATE API
# =========================================================
@router.get("/api/state")
@limiter.limit("60/minute")
async def get_state(request: Request):
    return build_state()


# =========================================================
# HEATMAP
# =========================================================
@router.get("/api/heatmap_zoom/{cell}")
async def get_heatmap_zoom(cell: int):
    return get_heatmap_zoom_detail(cell, slice_manager)


# =========================================================
# ASSIGN GPU
# =========================================================
@router.post("/api/assign/{gpu}/{slice_num}")
@limiter.limit("30/minute")
async def api_assign(request: Request, gpu: int, slice_num: int, _=Depends(verify_token)):

    from config import GPU_COUNT, SLICE_COUNT

    if gpu < 0 or gpu >= GPU_COUNT:
        raise HTTPException(404, "GPU no encontrada")

    if slice_num < 0 or slice_num >= SLICE_COUNT:
        return {"ok": False, "msg": "Slice fuera de rango"}

    if slice_num in slice_manager.get_completed_set():
        return {"ok": False, "msg": "Slice ya completado"}

    slice_manager.assign_slice(gpu, slice_num)
    stats[gpu]["current_slice"] = slice_num
    stats[gpu]["status"] = f"SLICE {slice_num}"
    stats[gpu]["slice_start_keys"] = max(0, stats[gpu].get("total", 0))
    stats[gpu]["retry_since"] = None

    add_log(f"API: GPU {gpu} → Slice {slice_num}", "OK")
    return {"ok": True}


# =========================================================
# NEXT SLICE
# =========================================================
@router.post("/api/assign/{gpu}/next")
@limiter.limit("30/minute")
async def api_assign_next(request: Request, gpu: int, _=Depends(verify_token)):

    from config import EXPLORE_RATE, GUIDED_SIGMA

    if gpu < 0 or gpu >= len(stats):
        raise HTTPException(404, "GPU no encontrada")

    gs = get_global_state()

    nxt = slice_manager.next_available_slice(
        EXPLORE_RATE,
        bandit,
        gs["global_best_key_int"],
        gs["explore_sigma"],
        gs["elite_history"]
    )

    if nxt is None:
        return {"ok": False, "msg": "No hay slices disponibles"}

    slice_manager.assign_slice(gpu, nxt)
    stats[gpu]["current_slice"] = nxt
    stats[gpu]["status"] = f"SLICE {nxt}"
    stats[gpu]["slice_start_keys"] = max(0, stats[gpu].get("total", 0))
    stats[gpu]["retry_since"] = None

    return {"ok": True, "slice": nxt}


# =========================================================
# PAUSE / RESUME
# =========================================================
@router.post("/api/pause/{gpu}")
@limiter.limit("20/minute")
async def api_pause(request: Request, gpu: int, _=Depends(verify_token)):
    paused_events[gpu].clear()
    stats[gpu]["paused"] = True
    stats[gpu]["status"] = "PAUSED"
    add_log(f"GPU {gpu} pausada", "WARN")
    return {"ok": True}


@router.post("/api/resume/{gpu}")
@limiter.limit("20/minute")
async def api_resume(request: Request, gpu: int, _=Depends(verify_token)):
    paused_events[gpu].set()
    stats[gpu]["paused"] = False
    add_log(f"GPU {gpu} reanudada", "INFO")
    return {"ok": True}


# =========================================================
# MARK SLICE AS DONE / PENDING
# =========================================================
@router.post("/api/mark_done/{slice_num}")
@limiter.limit("30/minute")
async def api_mark_done(request: Request, slice_num: int, _=Depends(verify_token)):
    from config import SLICE_COUNT
    if slice_num < 0 or slice_num >= SLICE_COUNT:
        return {"ok": False, "msg": "Slice fuera de rango"}
    if slice_num not in slice_manager.get_completed_set():
        slice_manager.mark_completed(slice_num)
        add_log(f"API: Slice {slice_num} marcado como completado", "OK")
    return {"ok": True}


@router.post("/api/mark_pending/{slice_num}")
@limiter.limit("30/minute")
async def api_mark_pending(request: Request, slice_num: int, _=Depends(verify_token)):
    from config import SLICE_COUNT
    if slice_num < 0 or slice_num >= SLICE_COUNT:
        return {"ok": False, "msg": "Slice fuera de rango"}
    if slice_num in slice_manager.get_completed_set():
        slice_manager.mark_pending(slice_num)
        add_log(f"API: Slice {slice_num} marcado como pendiente", "WARN")
    return {"ok": True}


# =========================================================
# LOGS EXPORT
# =========================================================
@router.get("/api/logs/export")
async def export_logs(_=Depends(verify_token)):
    from fastapi.responses import PlainTextResponse
    log_text = "\n".join([f"[{l['ts']}] [{l['level']}] {l['msg']}" for l in logs_global])
    return PlainTextResponse(
        content=log_text,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=bitcrack_log.txt"}
    )