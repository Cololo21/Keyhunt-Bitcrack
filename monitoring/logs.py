"""Sistema de logs en memoria"""

from datetime import datetime

logs_global = []


def add_log(msg: str, level: str = "INFO"):
    """Añade un mensaje al log global"""
    entry = {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "msg": msg,
        "level": level
    }
    logs_global.append(entry)
    if len(logs_global) > 500:
        logs_global.pop(0)

    # También imprimir en consola
    print(f"[{entry['ts']}] [{level}] {msg}")