"""Scheduler adaptativo para slices"""

import random
from config import GLOBAL_START, GLOBAL_END, SLICE_SIZE, SLICE_COUNT
from .fitness import calc_fitness, privkey_to_address


def estimate_gradient_direction(key_int: int, bits: int = 8) -> int:
    """
    Finite-difference gradient en espacio discreto.
    Devuelve el delta que mejora más el fitness.
    """
    base_fit = calc_fitness(privkey_to_address(key_int))
    best_dir = 0
    best_score = base_fit

    for i in range(bits):
        delta = 1 << i
        for direction in (delta, -delta):
            neighbor = key_int + direction
            if GLOBAL_START <= neighbor <= GLOBAL_END:
                f = calc_fitness(privkey_to_address(neighbor))
                if f > best_score:
                    best_score = f
                    best_dir = direction

    return best_dir


def key_to_slice(key_int: int) -> int:
    """Convierte una clave entera al índice de su slice"""
    if key_int < GLOBAL_START:
        return 0
    if key_int > GLOBAL_END:
        return SLICE_COUNT - 1
    return (key_int - GLOBAL_START) // SLICE_SIZE


def get_slice_range(slice_num: int):
    """Obtiene rango [start, end] para un slice"""
    if slice_num is None:
        return GLOBAL_START, GLOBAL_END
    start = GLOBAL_START + slice_num * SLICE_SIZE
    end = start + SLICE_SIZE - 1
    if slice_num == SLICE_COUNT - 1:
        end = GLOBAL_END
    return start, end


def slice_to_percent(slice_num: int) -> float:
    """Porcentaje completado del keyspace"""
    # CORREGIDO: Manejar slice_num = None
    if slice_num is None:
        return 0.0
    if SLICE_COUNT is None or SLICE_COUNT == 0:
        return 0.0
    return (slice_num / SLICE_COUNT) * 100
