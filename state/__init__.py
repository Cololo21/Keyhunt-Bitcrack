"""Módulo de estado"""
from .slices import slice_manager, SliceManager
from .persistence import (
    load_completed_slices, save_completed_slices_atomic,
    load_gpu_state, save_gpu_state
)
from .global_state import (
    get_global_state, update_fitness,
    global_best_fit, global_best_addr, global_best_key, global_best_key_int
)
