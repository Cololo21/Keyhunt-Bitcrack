"""Punto de entrada principal"""

import time
import threading
import signal
import sys
import os
import traceback

# ==========================================================
# PATH FIX
# ==========================================================
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ==========================================================
# CONFIG PRIMERO
# ==========================================================
import config

# ==========================================================
# GLOBAL ERROR HANDLER
# ==========================================================
def global_excepthook(exc_type, exc_value, exc_traceback):
    print("=" * 60)
    print("ERROR NO CAPTURADO:")
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    print("=" * 60)
    sys.exit(1)

sys.excepthook = global_excepthook


# ==========================================================
# IMPORTS CORE (SIN BANDIT AQUI)
# ==========================================================
print("1. config OK")

print("2. slice_manager")
from state.slices import slice_manager

print("3. persistence")
from state.persistence import load_gpu_state, save_gpu_state

print("4. global_state")
from state.global_state import get_global_state, update_fitness

print("5. gpu_worker")
from gpu.worker import gpu_worker, shutdown_event, stats

print("6. logs")
from monitoring.logs import add_log

print("7. backup")
from monitoring.backup import start_backup_loop

print("8. routes")
from api.routes import router

print("9. websocket")
from api.websocket import websocket_endpoint

print("10. fastapi")
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


# ==========================================================
# FASTAPI INIT
# ==========================================================
app = FastAPI(title="BitCrack Monitor", version="4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(router)
app.add_api_websocket_route("/ws", websocket_endpoint)


# ==========================================================
# GLOBAL STATE
# ==========================================================
start_time = time.time()

config.start_time = start_time
config.stats = stats


# ==========================================================
# FUNCIÓN DE GUARDADO PERIÓDICO
# ==========================================================
def periodic_save():
    """Guarda estado periódicamente (cada 60 segundos)"""
    last_save = 0
    while not shutdown_event.is_set():
        try:
            current_time = time.time()
            # Guardar cada 60 segundos
            if current_time - last_save >= 60:
                add_log("Guardado automático de estado...", "INFO")
                
                # Guardar slices completados
                slice_manager.save_state()
                
                # Guardar estado de GPUs
                save_gpu_state()
                
                # Guardar estado del bandit
                from core.bandit import bandit
                bandit.save_state()
                
                last_save = current_time
                add_log("Guardado automático completado", "OK")
        except Exception as e:
            add_log(f"Error en guardado periódico: {e}", "ERROR")
        
        # Esperar 30 segundos antes de la siguiente verificación
        time.sleep(30)


# ==========================================================
# FUNCIÓN DE LIMPIEZA PERIÓDICA DE CHECKPOINTS
# ==========================================================
def periodic_cleanup():
    """Limpia checkpoints antiguos periódicamente (cada hora)"""
    while not shutdown_event.is_set():
        try:
            time.sleep(3600)  # 1 hora
            from gpu.bitcrack import cleanup_old_checkpoints
            cleanup_old_checkpoints(max_age_days=7)
            add_log("Limpieza automática de checkpoints completada", "INFO")
        except Exception as e:
            add_log(f"Error en limpieza de checkpoints: {e}", "ERROR")


# ==========================================================
# SHUTDOWN HANDLER
# ==========================================================
def signal_handler(sig, frame):
    print("\n[SHUTDOWN] Guardando estado final...")
    
    try:
        # Guardar slices completados
        slice_manager.save_state()
        
        # Guardar estado de GPUs
        save_gpu_state()
        
        # Guardar estado del bandit
        from core.bandit import bandit
        bandit.save_state()
        
        # Guardar checkpoints finales
        from gpu.bitcrack import save_checkpoint
        for gpu_id, s in stats.items():
            slice_num = s.get("current_slice")
            total_keys = s.get("total", 0)
            if slice_num is not None and total_keys > 0:
                save_checkpoint(gpu_id, slice_num, total_keys)
        
        add_log("Estado guardado correctamente", "OK")
    except Exception as e:
        add_log(f"Error en guardado final: {e}", "ERROR")
    
    shutdown_event.set()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ==========================================================
# MAIN
# ==========================================================
def main():
    print("14. Iniciando main...")

    # 🔥 IMPORT TARDÍO CRÍTICO (EVITA CIRCULAR IMPORTS)
    from core.bandit import bandit

    add_log("BitCrack Monitor iniciando...", "INFO")
    add_log(f"Target: {config.ADDRESS}", "INFO")

    from core.fitness import validate_address
    if not validate_address(config.ADDRESS):
        add_log("⚠️ DIRECCIÓN INVÁLIDA", "ERROR")

    print("15. Cargando estado GPUs...")

    saved = load_gpu_state()
    gs = get_global_state()

    # Limpiar checkpoints antiguos al inicio
    try:
        from gpu.bitcrack import cleanup_old_checkpoints
        cleanup_old_checkpoints(max_age_days=7)
    except:
        pass

    for i in range(config.GPU_COUNT):
        # Usar get_completed_set() en lugar de completed_set
        if i in saved and saved[i] not in slice_manager.get_completed_set():
            slice_manager.assign_slice(i, saved[i])
            stats[i]["current_slice"] = saved[i]
            
            # Cargar checkpoint del slice guardado
            from gpu.bitcrack import load_checkpoint
            checkpoint = load_checkpoint(i, saved[i])
            if checkpoint > 0:
                stats[i]["total"] = checkpoint
                stats[i]["slice_start_keys"] = checkpoint
                add_log(f"GPU {i}: Reanudando slice {saved[i]} desde checkpoint {checkpoint}", "INFO")

        else:
            nxt = slice_manager.next_available_slice(
                config.EXPLORE_RATE,
                bandit,
                gs["global_best_key_int"],
                gs["explore_sigma"],
                gs["elite_history"]
            )

            if nxt is not None:
                slice_manager.assign_slice(i, nxt)
                stats[i]["current_slice"] = nxt
                
                # Cargar checkpoint si existe
                from gpu.bitcrack import load_checkpoint
                checkpoint = load_checkpoint(i, nxt)
                if checkpoint > 0:
                    stats[i]["total"] = checkpoint
                    stats[i]["slice_start_keys"] = checkpoint

    # ======================================================
    # WORKERS
    # ======================================================
    print("16. Iniciando workers...")

    for i in range(config.GPU_COUNT):
        threading.Thread(
            target=gpu_worker,
            args=(i, stats, update_fitness),
            daemon=True
        ).start()

    # ======================================================
    # BACKUP (slices y progreso)
    # ======================================================
    print("17. Backup loop...")
    start_backup_loop()
    
    # ======================================================
    # GUARDADO PERIÓDICO
    # ======================================================
    print("18. Iniciando guardado periódico...")
    save_thread = threading.Thread(target=periodic_save, daemon=True)
    save_thread.start()
    
    # ======================================================
    # LIMPIEZA PERIÓDICA DE CHECKPOINTS
    # ======================================================
    print("19. Iniciando limpieza periódica...")
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()

    print(f"20. Server ready -> http://localhost:{config.PORT}")
    print(f"📱 Desde el móvil (mismo WiFi): http://{get_local_ip()}:{config.PORT}")
    add_log("🚀 Sistema iniciado correctamente", "OK")
    add_log("✅ Guardado automático cada 60 segundos", "INFO")
    add_log("✅ Limpieza automática de checkpoints cada hora", "INFO")


# ==========================================================
# OBTENER IP LOCAL PARA EL MÓVIL
# ==========================================================
def get_local_ip():
    """Obtiene la IP local de la PC para conectar desde el móvil"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "192.168.2.254"


# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    main()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
