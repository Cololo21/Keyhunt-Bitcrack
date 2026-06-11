"""Caché de heatmap y bloques para el dashboard"""

import time
import threading
from config import SLICE_COUNT

_heatmap_cache = []
_blocks_cache = []
_heatmap_cache_ts = 0.0
_heatmap_cache_lock = threading.Lock()
HEATMAP_TTL = 5.0


def rebuild_heatmap_cache(slice_manager):
    """Reconstruye la caché de heatmap y bloques"""
    global _heatmap_cache, _blocks_cache, _heatmap_cache_ts

    # Validar SLICE_COUNT
    _SLICE_COUNT = SLICE_COUNT if SLICE_COUNT else 100000000
    
    completed = slice_manager.get_completed_set()
    assignments = slice_manager.get_all_assignments()

    # Bloques 10%
    blocks = []
    for b in range(10):
        s_sl = b * (_SLICE_COUNT // 10)
        e_sl = (b + 1) * (_SLICE_COUNT // 10)
        done = sum(1 for x in completed if s_sl <= x < e_sl)
        total_in = e_sl - s_sl
        # CORREGIDO: filtrar None
        active = [g for g, sl in assignments.items() if sl is not None and s_sl <= sl < e_sl]
        blocks.append({
            "label": f"{b*10}-{(b+1)*10}%",
            "done": done,
            "total": total_in,
            "pct": (done / total_in * 100) if total_in else 0,
            "active": len(active)
        })

    # Heatmap 100 celdas
    cell_size = max(1, (_SLICE_COUNT + 99) // 100)
    heatmap = []
    for c in range(100):
        s_sl = c * cell_size
        e_sl = min(s_sl + cell_size, _SLICE_COUNT)
        done = sum(1 for x in completed if s_sl <= x < e_sl)
        # CORREGIDO: filtrar None
        active = any(s_sl <= sl < e_sl for sl in assignments.values() if sl is not None)
        heatmap.append({"done": done, "total": e_sl - s_sl, "active": active})

    with _heatmap_cache_lock:
        _heatmap_cache = heatmap
        _blocks_cache = blocks
        _heatmap_cache_ts = time.time()


def get_cached_heatmap(slice_manager):
    """Devuelve heatmap y bloques desde caché"""
    if time.time() - _heatmap_cache_ts > HEATMAP_TTL or not _heatmap_cache:
        rebuild_heatmap_cache(slice_manager)
    with _heatmap_cache_lock:
        return list(_blocks_cache), list(_heatmap_cache)


def get_heatmap_zoom_detail(cell: int, slice_manager):
    """Devuelve detalle de zoom para una celda"""
    from config import SLICE_COUNT
    
    _SLICE_COUNT = SLICE_COUNT if SLICE_COUNT else 100000000

    MAIN_CELL_SIZE = _SLICE_COUNT // 100
    SUB_CELLS = 1000
    SUB_CELL_SIZE = max(1, MAIN_CELL_SIZE // SUB_CELLS)

    block_start = cell * MAIN_CELL_SIZE
    assigned = slice_manager.get_all_assignments()
    completed = slice_manager.get_completed_set()

    subcells = []
    for s in range(SUB_CELLS):
        s_sl = block_start + s * SUB_CELL_SIZE
        e_sl = min(s_sl + SUB_CELL_SIZE, _SLICE_COUNT)
        done = sum(1 for x in completed if s_sl <= x < e_sl)
        # CORREGIDO: filtrar None
        active = any(s_sl <= sl < e_sl for sl in assigned.values() if sl is not None)
        subcells.append({
            "index": s,
            "s_slice": s_sl,
            "e_slice": e_sl - 1,
            "done": done,
            "total": e_sl - s_sl,
            "active": active
        })

    return {
        "cell": cell,
        "block_start": block_start,
        "block_end": block_start + MAIN_CELL_SIZE - 1,
        "sub_cell_size": SUB_CELL_SIZE,
        "subcells": subcells
    }