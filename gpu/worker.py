"""Worker loop para cada GPU"""

import subprocess
import time
import threading
import os

import config

from state.slices import slice_manager
from .bitcrack import launch_bitcrack, parse_bitcrack_output, verify_checkpoint, save_checkpoint, load_checkpoint, clear_checkpoint, cleanup_old_checkpoints
from .metrics import check_throttling
from monitoring.logs import add_log

# Eventos de pausa por GPU
paused_events = {i: threading.Event() for i in range(config.GPU_COUNT)}
for ev in paused_events.values():
    ev.set()

shutdown_event = threading.Event()

# Estadísticas globales de GPUs
stats = {
    i: {
        "speed": 0.0, "total": 0, "last_total": 0,
        "last_update": time.time(), "status": "INIT",
        "restarts": 0, "metrics": {}, "found": False,
        "current_slice": None, "slice_start_keys": 0,
        "retry_since": None, "speed_history": [], "speed_avg": 0.0,
        "paused": False, "log": [], "best_fit": 0, "best_addr": "", "best_key": "",
        "bench_params": None,
        "fit_counter": 0
    }
    for i in range(config.GPU_COUNT)
}


def get_slice_range(slice_num: int):
    from core.scheduler import get_slice_range as _get_slice_range
    return _get_slice_range(slice_num)


def update_gpu_best_fitness(gpu: int, stats_dict: dict, slice_num: int, keys_in_slice: int):
    """Actualiza el mejor fitness de una GPU específica"""
    try:
        from core.scheduler import get_slice_range
        from core.fitness import calc_fitness, privkey_to_address
        
        slice_start, slice_end = get_slice_range(slice_num)
        approx_key = slice_start + keys_in_slice
        
        if not (slice_start <= approx_key <= slice_end):
            approx_key = slice_start
        
        addr = privkey_to_address(approx_key)
        if not addr:
            return
        
        fit = calc_fitness(addr)
        
        if fit > stats_dict[gpu].get("best_fit", 0):
            stats_dict[gpu]["best_fit"] = fit
            stats_dict[gpu]["best_addr"] = addr
            stats_dict[gpu]["best_key"] = hex(approx_key)
            add_log(f"GPU {gpu}: Nuevo best fitness {fit} para slice {slice_num} @ {hex(approx_key)}", "INFO")
            
    except Exception as e:
        add_log(f"GPU {gpu}: Error actualizando fitness: {e}", "WARN")


def validate_stats(stats_dict: dict, gpu: int, slice_size: int):
    """Valida y corrige estadísticas para evitar valores negativos"""
    s = stats_dict[gpu]
    
    if s.get("total", 0) < 0:
        s["total"] = 0
    
    if s.get("slice_start_keys", 0) < 0:
        s["slice_start_keys"] = 0
    
    if s.get("total", 0) < s.get("slice_start_keys", 0):
        s["total"] = s["slice_start_keys"]
    
    searched = s.get("total", 0) - s.get("slice_start_keys", 0)
    if searched > slice_size:
        s["total"] = s["slice_start_keys"] + slice_size
        add_log(f"GPU {gpu}: Corregido total excedido, ajustado a slice_size", "WARN")


def gpu_worker(gpu: int, stats_dict: dict, update_fitness_callback):
    from config import SLICE_SIZE
    
    # DEPURACIÓN
    print(f"WORKER GPU {gpu}: SLICE_SIZE = {SLICE_SIZE} (type: {type(SLICE_SIZE)})")
    
    from config import SLICE_SIZE

    while not shutdown_event.is_set():

        paused_events[gpu].wait()
        if shutdown_event.is_set():
            break

        slice_num = slice_manager.get_assigned(gpu)
        if slice_num is None:
            time.sleep(1)
            continue

        checkpoint_keys = slice_manager.load_checkpoint(gpu, slice_num)
        
        if checkpoint_keys == 0:
            checkpoint_keys = slice_manager.get_slice_progress(slice_num)
        
        if checkpoint_keys < 0:
            checkpoint_keys = 0
        if checkpoint_keys > SLICE_SIZE:
            add_log(f"GPU {gpu}: Checkpoint {checkpoint_keys} excede SLICE_SIZE, reiniciando", "WARN")
            checkpoint_keys = 0
        
        stats_dict[gpu]["slice_start_keys"] = checkpoint_keys
        stats_dict[gpu]["total"] = checkpoint_keys
        stats_dict[gpu]["fit_counter"] = 0
        
        if checkpoint_keys > 0:
            add_log(f"GPU {gpu}: Reanudando slice {slice_num} desde {checkpoint_keys} keys procesadas", "INFO")

        if slice_manager.is_completed(slice_num):
            add_log(f"GPU {gpu}: Slice {slice_num} ya está completado, asignando nuevo", "INFO")
            continue

        bench_params = stats_dict[gpu].get("bench_params")
        proc = launch_bitcrack(gpu, slice_num, checkpoint_keys, bench_params)

        if not proc:
            time.sleep(config.RESTART_DELAY)
            continue

        last_output = time.time()
        throttle_chk = time.time()
        last_checkpoint_save = time.time()
        slice_completed = False

        try:
            while not shutdown_event.is_set():

                if not paused_events[gpu].is_set():
                    add_log(f"GPU {gpu} pausada", "WARN")
                    proc.kill()
                    total_keys = stats_dict[gpu].get("total", checkpoint_keys)
                    if total_keys > checkpoint_keys:
                        if total_keys <= SLICE_SIZE:
                            slice_manager.save_checkpoint(gpu, slice_num, total_keys)
                            slice_manager.update_slice_progress(slice_num, total_keys, gpu)
                    paused_events[gpu].wait()
                    add_log(f"GPU {gpu} reanudada", "INFO")
                    break

                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break

                if line:
                    parse_bitcrack_output(
                        gpu,
                        line.strip(),
                        stats_dict,
                        update_fitness_callback
                    )
                    last_output = time.time()
                    
                    stats_dict[gpu]["fit_counter"] = stats_dict[gpu].get("fit_counter", 0) + 1
                    if stats_dict[gpu]["fit_counter"] % 10 == 0:
                        total_keys = stats_dict[gpu].get("total", checkpoint_keys)
                        keys_in_slice = total_keys - stats_dict[gpu].get("slice_start_keys", 0)
                        if keys_in_slice > 0 and keys_in_slice <= SLICE_SIZE:
                            update_gpu_best_fitness(gpu, stats_dict, slice_num, keys_in_slice)
                            slice_manager.update_slice_progress(slice_num, total_keys, gpu)
                    
                    now = time.time()
                    if now - last_checkpoint_save > 15:
                        total_keys = stats_dict[gpu].get("total", checkpoint_keys)
                        if total_keys > checkpoint_keys and total_keys <= SLICE_SIZE:
                            slice_manager.save_checkpoint(gpu, slice_num, total_keys)
                            slice_manager.update_slice_progress(slice_num, total_keys, gpu)
                            last_checkpoint_save = now
                            add_log(f"GPU {gpu}: Checkpoint guardado en {total_keys} keys", "INFO")

                if time.time() - last_output > config.WATCHDOG_TIMEOUT:
                    add_log(f"GPU {gpu} WATCHDOG timeout — reiniciando", "WARN")
                    proc.kill()
                    stats_dict[gpu]["restarts"] += 1
                    total_keys = stats_dict[gpu].get("total", checkpoint_keys)
                    if total_keys > checkpoint_keys and total_keys <= SLICE_SIZE:
                        slice_manager.save_checkpoint(gpu, slice_num, total_keys)
                        slice_manager.update_slice_progress(slice_num, total_keys, gpu)
                    break

                if time.time() - throttle_chk > 30:
                    check_throttling(gpu, stats_dict)
                    throttle_chk = time.time()

        finally:
            if proc and proc.poll() is None:
                proc.kill()

        exit_code = proc.wait()

        if not stats_dict[gpu].get("found", False) and exit_code == 0:
            total_keys = stats_dict[gpu].get("total", checkpoint_keys)
            searched = total_keys - stats_dict[gpu].get("slice_start_keys", 0)
            
            if searched >= SLICE_SIZE * 0.99:
                slice_manager.mark_completed(slice_num, gpu)
                add_log(f"GPU {gpu} completó slice {slice_num}", "OK")
                slice_manager.clear_checkpoint(gpu, slice_num)
                slice_completed = True
            else:
                add_log(f"GPU {gpu} slice {slice_num} terminó con solo {searched}/{SLICE_SIZE} keys", "WARN")
                slice_manager.update_slice_progress(slice_num, total_keys, gpu)

            from state.global_state import get_global_state
            from core.bandit import bandit

            gs = get_global_state()

            nxt = slice_manager.next_available_slice(
                config.EXPLORE_RATE,
                bandit,
                gs["global_best_key_int"],
                gs["explore_sigma"],
                gs["elite_history"]
            )

            if nxt is not None:
                slice_manager.assign_slice(gpu, nxt)
                stats_dict[gpu]["current_slice"] = nxt
                stats_dict[gpu]["slice_start_keys"] = 0
                stats_dict[gpu]["total"] = 0
                stats_dict[gpu]["status"] = f"SLICE {nxt}"
                stats_dict[gpu]["retry_since"] = None
                stats_dict[gpu]["fit_counter"] = 0
                
                new_checkpoint = slice_manager.load_checkpoint(gpu, nxt)
                if new_checkpoint == 0:
                    new_checkpoint = slice_manager.get_slice_progress(nxt)
                
                if new_checkpoint > 0 and new_checkpoint <= SLICE_SIZE:
                    stats_dict[gpu]["total"] = new_checkpoint
                    stats_dict[gpu]["slice_start_keys"] = new_checkpoint
                    add_log(f"GPU {gpu}: Nuevo slice {nxt} tiene checkpoint de {new_checkpoint} keys", "INFO")
            else:
                add_log("No quedan slices disponibles", "WARN")
                stats_dict[gpu]["status"] = "IDLE"
                if gpu in slice_manager.assigned:
                    slice_manager.assigned.pop(gpu, None)

        else:
            add_log(
                f"GPU {gpu} interrumpido en slice {slice_num} (exit={exit_code}) — reintentando",
                "WARN"
            )
            stats_dict[gpu]["status"] = f"RETRY slice {slice_num}"
            stats_dict[gpu]["restarts"] = stats_dict[gpu].get("restarts", 0) + 1

            if stats_dict[gpu].get("retry_since") is None:
                stats_dict[gpu]["retry_since"] = time.time()
            
            total_keys = stats_dict[gpu].get("total", checkpoint_keys)
            if total_keys > checkpoint_keys and total_keys <= SLICE_SIZE:
                slice_manager.save_checkpoint(gpu, slice_num, total_keys)
                slice_manager.update_slice_progress(slice_num, total_keys, gpu)
                add_log(f"GPU {gpu}: Checkpoint guardado en {total_keys} keys", "INFO")

        time.sleep(config.RESTART_DELAY)