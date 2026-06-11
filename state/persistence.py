"""Persistencia JSON"""

import os
import json
from config import PROGRESS_DIR, BACKUP_DIR, SLICES_FILE, GPU_STATE_FILE
from monitoring.logs import add_log


def load_completed_slices():
    if os.path.exists(SLICES_FILE):
        try:
            with open(SLICES_FILE) as f:
                return set(json.load(f))
        except Exception:
            add_log("JSON corrupto slices", "WARN")
    return set()


def save_completed_slices_atomic(slices: set):
    tmp = SLICES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(list(slices), f)
    os.replace(tmp, SLICES_FILE)


def load_gpu_state():
    if os.path.exists(GPU_STATE_FILE):
        try:
            with open(GPU_STATE_FILE) as f:
                return {int(k): v for k, v in json.load(f).items()}
        except Exception:
            pass
    return {}


def save_gpu_state():
    from state.slices import slice_manager
    from config import GPU_COUNT

    state = {
        str(i): slice_manager.get_assigned(i)
        for i in range(GPU_COUNT)
    }

    tmp = GPU_STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, GPU_STATE_FILE)