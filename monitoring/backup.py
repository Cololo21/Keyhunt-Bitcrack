"""Backup automático de slices completados"""

import time
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from config import BACKUP_DIR, BACKUP_KEEP_DAYS, SLICES_FILE
from monitoring.logs import add_log


def backup_loop():
    """Loop de backup diario"""
    while True:
        time.sleep(86400)  # 24 horas

        if not SLICES_FILE or not Path(SLICES_FILE).exists():
            continue

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = Path(BACKUP_DIR) / f"completed_slices_{ts}.json"

        try:
            shutil.copy2(SLICES_FILE, dest)
            add_log(f"Backup creado: {dest}", "INFO")
        except Exception as e:
            add_log(f"Error en backup: {e}", "ERROR")

        # Limpiar backups antiguos
        cutoff = datetime.now() - timedelta(days=BACKUP_KEEP_DAYS)
        for fp in Path(BACKUP_DIR).glob("completed_slices_*.json"):
            try:
                mtime = datetime.fromtimestamp(fp.stat().st_mtime)
                if mtime < cutoff:
                    fp.unlink()
                    add_log(f"Backup antiguo eliminado: {fp.name}", "INFO")
            except Exception:
                pass


def start_backup_loop():
    """Inicia el thread de backup"""
    thread = threading.Thread(target=backup_loop, daemon=True)
    thread.start()
    return thread