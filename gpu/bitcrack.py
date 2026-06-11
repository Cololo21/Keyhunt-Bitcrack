"""Wrapper para ejecutar y parsear output de cuBitCrack"""

import subprocess
import re
import os
import time
import json
import threading

import config

from monitoring.logs import add_log

speed_re = re.compile(r'([\d.]+)\s*MKey/s\s*\(([\d,]+)\s*total\)')

# Directorio para checkpoints
CHECKPOINT_DIR = os.path.join(config.BASE_DIR, "checkpoints")
CHECKPOINT_LOCK = threading.Lock()


def get_slice_range(slice_num: int):
    """Obtiene rango de keys para un slice"""
    from core.scheduler import get_slice_range as _get_slice_range
    return _get_slice_range(slice_num)


def save_checkpoint(gpu: int, slice_num: int, keys_processed: int):
    """Guarda el progreso de un slice (thread-safe)"""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    checkpoint_file = os.path.join(CHECKPOINT_DIR, f"gpu_{gpu}_slice_{slice_num}.json")
    
    with CHECKPOINT_LOCK:
        try:
            # Guardar en archivo temporal primero
            temp_file = checkpoint_file + ".tmp"
            with open(temp_file, "w") as f:
                json.dump({
                    "slice": slice_num,
                    "keys": keys_processed,
                    "gpu": gpu,
                    "timestamp": time.time(),
                    "version": 2
                }, f)
            
            # Renombrar para hacer atómico
            os.replace(temp_file, checkpoint_file)
            
        except Exception as e:
            add_log(f"Error guardando checkpoint GPU {gpu}: {e}", "WARN")


def load_checkpoint(gpu: int, slice_num: int) -> int:
    """Carga el progreso guardado de un slice"""
    checkpoint_file = os.path.join(CHECKPOINT_DIR, f"gpu_{gpu}_slice_{slice_num}.json")
    
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r") as f:
                data = json.load(f)
                keys = data.get("keys", 0)
                version = data.get("version", 1)
                
                # Validar que los keys no sean negativos
                if keys < 0:
                    keys = 0
                
                # Validar que no exceda el tamaño del slice
                from config import SLICE_SIZE
                if keys > SLICE_SIZE:
                    add_log(f"GPU {gpu}: Checkpoint {keys} excede SLICE_SIZE, corrigiendo", "WARN")
                    keys = SLICE_SIZE
                
                if keys > 0:
                    add_log(f"GPU {gpu}: Cargando checkpoint slice {slice_num} desde {keys} keys (v{version})", "INFO")
                return keys
        except Exception as e:
            add_log(f"Error cargando checkpoint GPU {gpu}: {e}", "WARN")
    
    return 0


def clear_checkpoint(gpu: int, slice_num: int):
    """Elimina el checkpoint cuando el slice se completa"""
    checkpoint_file = os.path.join(CHECKPOINT_DIR, f"gpu_{gpu}_slice_{slice_num}.json")
    
    with CHECKPOINT_LOCK:
        if os.path.exists(checkpoint_file):
            try:
                os.remove(checkpoint_file)
                add_log(f"GPU {gpu}: Checkpoint de slice {slice_num} eliminado", "INFO")
            except Exception as e:
                add_log(f"Error eliminando checkpoint: {e}", "WARN")


def cleanup_old_checkpoints(max_age_days: int = 7, gpu: int = None):
    """Limpia checkpoints antiguos o de slices completados"""
    if not os.path.exists(CHECKPOINT_DIR):
        return
    
    now = time.time()
    max_age = max_age_days * 24 * 3600
    
    # Obtener slices completados (evitar circular import)
    try:
        from state.slices import slice_manager
        completed_slices = slice_manager.get_completed_set()
    except:
        completed_slices = set()
    
    cleaned = 0
    for filename in os.listdir(CHECKPOINT_DIR):
        if not filename.endswith(".json"):
            continue
        
        # Filtrar por GPU si se especifica
        if gpu is not None and not filename.startswith(f"gpu_{gpu}_"):
            continue
        
        filepath = os.path.join(CHECKPOINT_DIR, filename)
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
                slice_num = data.get("slice")
                file_gpu = data.get("gpu", int(filename.split("_")[1]))
                file_age = now - data.get("timestamp", os.path.getmtime(filepath))
            
            # Eliminar si:
            # 1. El slice está completado
            # 2. El checkpoint es muy antiguo (> max_age_days)
            # 3. Los keys son inválidos
            should_remove = False
            
            if slice_num in completed_slices:
                should_remove = True
                reason = "slice completado"
            elif file_age > max_age:
                should_remove = True
                reason = f"antiguo ({file_age/86400:.1f} días)"
            elif data.get("keys", 0) < 0:
                should_remove = True
                reason = "keys inválidos"
            
            if should_remove:
                os.remove(filepath)
                cleaned += 1
                if cleaned % 10 == 0:
                    add_log(f"Limpieza: eliminado checkpoint {filename} ({reason})", "INFO")
                
        except Exception as e:
            # Si el archivo está corrupto, eliminarlo
            try:
                os.remove(filepath)
                cleaned += 1
            except:
                pass
    
    if cleaned > 0:
        add_log(f"Limpieza completada: {cleaned} checkpoints eliminados", "INFO")


def launch_bitcrack(gpu: int, slice_num: int, checkpoint_keys: int = 0, bench_params=None):
    """Lanza el proceso de cuBitCrack para un slice específico"""

    start, end = get_slice_range(slice_num)
    
    # Validar checkpoint
    if checkpoint_keys < 0:
        checkpoint_keys = 0
    if checkpoint_keys > (end - start):
        add_log(f"GPU {gpu}: Checkpoint {checkpoint_keys} excede rango, reiniciando", "WARN")
        checkpoint_keys = 0
    
    # Ajustar inicio si hay checkpoint
    if checkpoint_keys > 0:
        start = start + checkpoint_keys
        add_log(f"GPU {gpu}: Reanudando slice {slice_num} desde {hex(start)} (keys: {checkpoint_keys})", "INFO")

    params = bench_params if bench_params else config.BITCRACK_ARGS
    result_file = os.path.join(config.RESULTS_DIR, f"gpu{gpu}.txt")

    cmd = [
    config.BITCRACK_BIN,
    "-d", str(gpu),
    *params,
    "--keyspace", f"{start:x}:{end:x}",
    "--out", result_file,
    config.ADDRESS  # ← Esto está bien, sin --address
]

    try:
        add_log(f"GPU {gpu}: Lanzando {config.BITCRACK_BIN} slice {slice_num}", "INFO")
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
    except FileNotFoundError:
        add_log(f"GPU {gpu}: {config.BITCRACK_BIN} no encontrado", "ERROR")
        return None
    except Exception as e:
        add_log(f"GPU {gpu}: error al lanzar: {e}", "ERROR")
        return None


def parse_bitcrack_output(gpu: int, line: str, stats: dict, update_fitness_callback):
    """Parsea salida de cuBitCrack"""

    m = speed_re.search(line)
    if m:
        speed = float(m.group(1))
        total = int(m.group(2).replace(",", ""))

        s = stats[gpu]
        s["speed"] = speed
        s["total"] = total
        s["keys_per_sec"] = max(0, total - s.get("last_total", 0))
        s["last_total"] = total
        s["last_update"] = time.time()

        s.setdefault("speed_history", [])
        s["speed_history"].append(round(speed, 2))
        if len(s["speed_history"]) > 60:
            s["speed_history"].pop(0)

        if len(s["speed_history"]) >= 5:
            s["speed_avg"] = sum(s["speed_history"]) / len(s["speed_history"])

        s["fit_counter"] = s.get("fit_counter", 0) + 1

        # =========================================================
        # ACTUALIZAR FITNESS CADA 10 ITERACIONES (más frecuente)
        # Y GUARDAR CHECKPOINT
        # =========================================================
        if s["fit_counter"] % 10 == 0:
            slice_num = s.get("current_slice")
            keys_in_slice = s["total"] - s.get("slice_start_keys", 0)
            
            if slice_num is not None and keys_in_slice > 0:
                # Guardar checkpoint (validar que no exceda SLICE_SIZE)
                from config import SLICE_SIZE
                save_total = min(s["total"], SLICE_SIZE)
                save_checkpoint(gpu, slice_num, save_total)
                
                # Actualizar fitness (esto llama a global_state.update_fitness)
                update_fitness_callback(gpu, slice_num, max(0, min(keys_in_slice, SLICE_SIZE)))
                
                # =====================================================
                # ACTUALIZAR BEST_FIT LOCAL DE LA GPU
                # =====================================================
                from core.scheduler import get_slice_range
                from core.fitness import calc_fitness, privkey_to_address
                
                try:
                    slice_start, slice_end = get_slice_range(slice_num)
                    approx_key = slice_start + keys_in_slice
                    if not (slice_start <= approx_key <= slice_end):
                        approx_key = slice_start
                    
                    addr = privkey_to_address(approx_key)
                    if addr:
                        fit = calc_fitness(addr)
                        if fit > s.get("best_fit", 0):
                            s["best_fit"] = fit
                            s["best_addr"] = addr
                            s["best_key"] = hex(approx_key)
                            add_log(f"GPU {gpu}: Mejor fitness local: {fit} @ {hex(approx_key)}", "INFO")
                except Exception as e:
                    pass

    elif "Private key found" in line or "Key found" in line:
        add_log(f"🎉 GPU {gpu}: ¡CLAVE ENCONTRADA!", "OK")
        stats[gpu]["found"] = True
        
        # Guardar la clave encontrada
        key_match = re.search(r'[0-9a-f]{64}', line.lower())
        if key_match:
            found_key = key_match.group(0)
            add_log(f"🔑 CLAVE: {found_key}", "OK")
            # Guardar en archivo de resultados
            os.makedirs(config.RESULTS_DIR, exist_ok=True)
            with open(os.path.join(config.RESULTS_DIR, "found_keys.txt"), "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - GPU {gpu}: {found_key}\n")


def verify_checkpoint(checkpoint_path: str) -> bool:
    """Verifica checkpoint"""
    if not os.path.exists(checkpoint_path):
        return True

    if os.path.getsize(checkpoint_path) == 0:
        add_log(f"Checkpoint corrupto: {checkpoint_path}", "WARN")
        os.remove(checkpoint_path)
        return False

    # Verificar contenido JSON
    try:
        with open(checkpoint_path, "r") as f:
            json.load(f)
        return True
    except:
        add_log(f"Checkpoint corrupto (JSON inválido): {checkpoint_path}", "WARN")
        os.remove(checkpoint_path)
        return False


def get_completed_slices_from_checkpoints() -> set:
    """Obtiene slices completados basados en checkpoints (útil para recuperación)"""
    completed = set()
    if not os.path.exists(CHECKPOINT_DIR):
        return completed
    
    from config import SLICE_SIZE
    
    for filename in os.listdir(CHECKPOINT_DIR):
        if filename.endswith(".json"):
            try:
                filepath = os.path.join(CHECKPOINT_DIR, filename)
                with open(filepath, "r") as f:
                    data = json.load(f)
                    keys = data.get("keys", 0)
                    # Si el checkpoint tiene más del 99% del slice, considerarlo completado
                    if keys >= SLICE_SIZE * 0.99:
                        completed.add(data.get("slice"))
            except:
                pass
    return completed


def get_all_checkpoints() -> dict:
    """Obtiene todos los checkpoints para diagnóstico"""
    checkpoints = {}
    if not os.path.exists(CHECKPOINT_DIR):
        return checkpoints
    
    for filename in os.listdir(CHECKPOINT_DIR):
        if filename.endswith(".json"):
            try:
                filepath = os.path.join(CHECKPOINT_DIR, filename)
                with open(filepath, "r") as f:
                    data = json.load(f)
                    checkpoints[filename] = data
            except:
                pass
    return checkpoints