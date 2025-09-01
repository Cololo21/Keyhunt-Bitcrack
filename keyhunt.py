import subprocess
import os
import json
import time
import threading
import colorama
colorama.init()

# ----------------------
# CONFIGURACIÓN
# ----------------------
BITCRACK_PATH = r"  EDIT THIS YOUR ROUTE  ~~~   \bitcrack\cuBitcrack.exe "
NUM_GPUS = 2
RANGE_START = int("400000000000000000", 16)
RANGE_END   = int("7fffffffffffffffff", 16)
TARGET_ADDRESS = "1PWo3JeB9jrGwfHDNpdGK54CRas7fsVzXU"
RESULTS_FILE = r"  EDIT THIS YOUR ROUTE"
PROGRESS_FILE = r"  EDIT THIS YOUR ROUTE"

    BAR_LENGTH = 30
AUTO_SAVE_INTERVAL = 20
SUBRANGE_SIZE = 0x1000000

# ----------------------
# Funciones auxiliares
# ----------------------
def save_progress(gpu_id, current_hex):
    data = {}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            data = json.load(f)
    data[str(gpu_id)] = f"{current_hex:x}"
    with open(PROGRESS_FILE, "w") as f:
        json.dump(data, f)

def load_progress(gpu_id, default_start):
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            data = json.load(f)
            return int(data.get(str(gpu_id), f"{default_start:x}"), 16)
    return default_start

# ----------------------
# Barra de progreso con ETA, velocidad, claves probadas y restantes
# ----------------------
def print_dashboard(progress_dict, start_times, gpu_progress, gpu_end, last_progress, gpu_start):
    total_done = sum(gpu_progress[gpu_id] - gpu_start[gpu_id] for gpu_id in range(NUM_GPUS))
    total_keys = sum(gpu_end[gpu_id] - gpu_start[gpu_id] + 1 for gpu_id in range(NUM_GPUS))
    total_remaining = total_keys - total_done

    output_lines = []
    for gpu_id in range(NUM_GPUS):
        percent = progress_dict.get(gpu_id, 0)
        filled_length = int(BAR_LENGTH * percent // 100)
        bar = '█' * filled_length + '-' * (BAR_LENGTH - filled_length)

        # ETA y velocidad
        elapsed = time.time() - start_times[gpu_id]
        done = gpu_progress[gpu_id] - gpu_start[gpu_id]
        total = gpu_end[gpu_id] - gpu_start[gpu_id] + 1
        remaining = total - done
        eta_str = "N/A"
        speed_str = "N/A"
        if done > 0 and elapsed > 0:
            rate = done / elapsed
            speed_str = f"{rate:,.0f} keys/s"
            eta = remaining / rate
            h = int(eta // 3600)
            m = int((eta % 3600) // 60)
            s = int(eta % 60)
            eta_str = f"{h:02d}h {m:02d}m {s:02d}s"

        # Claves probadas y restantes
        probadas = done
        restantes = remaining

        output_lines.append(f"[GPU {gpu_id}] |{bar}| {percent:3.3f}% ETA: {eta_str} Speed: {speed_str} Probadas: {probadas:,} Restantes: {restantes:,}")

    output_lines.append(f"Total claves probadas: {total_done:,} / {total_keys:,}  Claves restantes: {total_remaining:,}")
    print("\033[F" * (NUM_GPUS + 1), end="")
    for line in output_lines:
        print(line)

# ----------------------
# Auto-save periódico
# ----------------------
def auto_save_loop(progress_dict, gpu_progress):
    while True:
        for gpu_id in range(NUM_GPUS):
            save_progress(gpu_id, gpu_progress[gpu_id])
        time.sleep(AUTO_SAVE_INTERVAL)

# ----------------------
# Ejecutar sub-bloque de BitCrack
# ----------------------
def run_bitcrack_subrange(gpu_id, start_hex, end_hex, progress_dict, gpu_progress, start_times, gpu_end):
    start_hex_str = f"{start_hex:x}"
    end_hex_str = f"{end_hex:x}"
    total_range = end_hex - start_hex + 1

    cmd = [
        BITCRACK_PATH,
        "-r", f"{start_hex_str}-{end_hex_str}",
        "-a", TARGET_ADDRESS,
        "-d", str(gpu_id)
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last_update_time = time.time()

    for line in process.stdout:
        line = line.strip()
        if not line:
            continue

        if line.startswith("HEX") or line.startswith("0x"):
            try:
                current_hex = int(line.split()[1], 16)
                gpu_progress[gpu_id] = current_hex
            except:
                pass

        if "FOUND" in line.upper():
            with open(RESULTS_FILE, "a") as f:
                f.write(f"[GPU {gpu_id}] {line}\n")

        now = time.time()
        if now - last_update_time >= 1:
            progress_dict[gpu_id] = ((gpu_progress[gpu_id] - start_hex) / total_range) * 100
            last_update_time = now

    process.wait()
    gpu_progress[gpu_id] = end_hex
    progress_dict[gpu_id] = ((gpu_progress[gpu_id] - start_hex) / total_range) * 100

# ----------------------
# Procesar GPU en sub-bloques
# ----------------------
def process_gpu_dashboard(gpu_id, start_hex, end_hex, progress_dict, gpu_progress, start_times, gpu_end):
    current = start_hex
    while current <= end_hex:
        sub_end = min(current + SUBRANGE_SIZE - 1, end_hex)
        run_bitcrack_subrange(gpu_id, current, sub_end, progress_dict, gpu_progress, start_times, gpu_end)
        current = sub_end + 1

# ----------------------
# Ejecutar keyhunt con dashboard ultra-robusto
# ----------------------
def start_keyhunt_dashboard():
    total_range = RANGE_END - RANGE_START + 1
    gpu_start = [RANGE_START + gpu_id * (total_range // NUM_GPUS) for gpu_id in range(NUM_GPUS)]
    gpu_progress = [load_progress(gpu_id, gpu_start[gpu_id]) for gpu_id in range(NUM_GPUS)]
    gpu_end = [RANGE_END if gpu_id == NUM_GPUS - 1 else RANGE_START + (gpu_id + 1) * (total_range // NUM_GPUS) - 1 for gpu_id in range(NUM_GPUS)]
    progress_dict = {gpu_id: 0 for gpu_id in range(NUM_GPUS)}
    start_times = {gpu_id: time.time() for gpu_id in range(NUM_GPUS)}
    last_progress = gpu_progress.copy()

    for _ in range(NUM_GPUS + 1):
        print()

    save_thread = threading.Thread(target=auto_save_loop, args=(progress_dict, gpu_progress), daemon=True)
    save_thread.start()

    threads = []
    for gpu_id in range(NUM_GPUS):
        t = threading.Thread(target=process_gpu_dashboard,
                             args=(gpu_id, gpu_progress[gpu_id], gpu_end[gpu_id], progress_dict, gpu_progress, start_times, gpu_end))
        t.start()
        threads.append(t)

    while any(t.is_alive() for t in threads):
        print_dashboard(progress_dict, start_times, gpu_progress, gpu_end, last_progress, gpu_start)
        time.sleep(1)

    for t in threads:
        t.join()
    print_dashboard(progress_dict, start_times, gpu_progress, gpu_end, last_progress, gpu_start)

# ----------------------
# MAIN
# ----------------------
if __name__ == "__main__":
    start_time = time.time()
    start_keyhunt_dashboard()
    elapsed = time.time() - start_time
    print(f"Tiempo total de ejecución: {int(elapsed // 3600)}h {int((elapsed % 3600) // 60)}m {int(elapsed % 60)}s")

