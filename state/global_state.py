"""Estado global de fitness y estadísticas"""

import threading
from collections import deque
import config

from monitoring.logs import add_log
from core.bandit import bandit

# =========================
# ESTADO GLOBAL
# =========================
global_best_fit = 0
global_best_addr = ""
global_best_key = ""
global_best_key_int = 0
fitness_ema = 0.0

elite_history: deque = deque(maxlen=config.ELITE_WINDOW)
explore_sigma = config.GUIDED_SIGMA

_fitness_lock = threading.Lock()


def get_global_state() -> dict:
    """Devuelve el estado global actual"""
    with _fitness_lock:
        return {
            "global_best_fit": global_best_fit,
            "global_best_addr": global_best_addr,
            "global_best_key": global_best_key,
            "global_best_key_int": global_best_key_int,
            "fitness_ema": fitness_ema,
            "elite_history": list(elite_history),
            "explore_sigma": explore_sigma,
        }


def update_fitness(gpu: int, slice_num: int, keys_in_slice: int):
    """Calcula fitness para la clave aproximada y actualiza estado global"""

    global global_best_fit
    global global_best_addr
    global global_best_key
    global global_best_key_int
    global fitness_ema
    global explore_sigma

    if slice_num is None:
        return

    # Import local para evitar circular imports
    from core.scheduler import get_slice_range
    from core.fitness import calc_fitness, privkey_to_address
    from gpu.worker import stats

    slice_start, slice_end = get_slice_range(slice_num)

    approx_key = slice_start + keys_in_slice
    if not (slice_start <= approx_key <= slice_end):
        approx_key = slice_start

    addr = privkey_to_address(approx_key)
    if not addr:
        return

    fit = calc_fitness(addr)

    with _fitness_lock:
        # =========================================================
        # ACTUALIZAR EMA (Exponential Moving Average)
        # =========================================================
        if fitness_ema == 0:
            fitness_ema = fit
        else:
            fitness_ema = (
                config.FITNESS_EMA_ALPHA * fit +
                (1 - config.FITNESS_EMA_ALPHA) * fitness_ema
            )

        # =========================================================
        # ACTUALIZAR ELITE HISTORY
        # =========================================================
        elite_history.append((approx_key, fit))

        # =========================================================
        # ACTUALIZAR BEST FITNESS GLOBAL
        # =========================================================
        if fit > global_best_fit:
            global_best_fit = fit
            global_best_addr = addr
            global_best_key = hex(approx_key)
            global_best_key_int = approx_key

            # Reducir sigma cuando encontramos mejor fitness
            explore_sigma = max(10, int(explore_sigma * 0.85))

            add_log(
                f"🏆 NUEVO BEST FITNESS GLOBAL: {fit} @ {hex(approx_key)}",
                "OK"
            )

        # =========================================================
        # ACTUALIZAR BEST FITNESS DE LA GPU
        # =========================================================
        if gpu in stats and fit > stats[gpu].get("best_fit", 0):
            stats[gpu]["best_fit"] = fit
            stats[gpu]["best_addr"] = addr
            stats[gpu]["best_key"] = hex(approx_key)
            add_log(
                f"GPU {gpu}: Nuevo best fitness {fit} para slice {slice_num}",
                "INFO"
            )

    # Actualizar bandit con este fitness
    bandit.update_from_fitness(slice_num, fit, approx_key)