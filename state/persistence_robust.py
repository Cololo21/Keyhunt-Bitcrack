"""Sistema robusto de persistencia para slices y checkpoints"""

import json
import os
import time
import shutil
from typing import Set, Dict, Optional
import config

class RobustPersistence:
    """Sistema de guardado robusto con backups y recuperación"""
    
    def __init__(self):
        self.base_dir = config.PROGRESS_DIR
        self.backup_dir = os.path.join(config.BASE_DIR, "backups", "progress")
        os.makedirs(self.backup_dir, exist_ok=True)
        
    def save_completed_slices(self, completed_set: Set[int]):
        """Guarda los slices completados con backup"""
        state_file = config.SLICES_FILE
        temp_file = state_file + ".tmp"
        backup_file = os.path.join(self.backup_dir, f"completed_slices_{int(time.time())}.json")
        
        try:
            # Guardar en archivo temporal
            with open(temp_file, "w") as f:
                json.dump({
                    "completed": list(completed_set),
                    "timestamp": time.time(),
                    "version": 2
                }, f, indent=2)
            
            # Si el archivo original existe, hacer backup
            if os.path.exists(state_file):
                shutil.copy2(state_file, backup_file)
                # Eliminar backups antiguos (mantener últimos 10)
                self._cleanup_old_backups()
            
            # Renombrar temporal a original
            shutil.move(temp_file, state_file)
            
        except Exception as e:
            print(f"Error guardando slices: {e}")
    
    def load_completed_slices(self) -> Set[int]:
        """Carga los slices completados con recuperación"""
        state_file = config.SLICES_FILE
        
        if not os.path.exists(state_file):
            return set()
        
        try:
            with open(state_file, "r") as f:
                data = json.load(f)
                
            # Nuevo formato
            if isinstance(data, dict) and "completed" in data:
                return set(data["completed"])
            # Formato antiguo (lista)
            elif isinstance(data, list):
                return set(data)
            else:
                return set()
                
        except Exception as e:
            print(f"Error cargando slices: {e}, intentando recuperar backup...")
            return self._recover_from_backup()
    
    def _recover_from_backup(self) -> Set[int]:
        """Recupera slices desde el backup más reciente"""
        backups = sorted([f for f in os.listdir(self.backup_dir) if f.startswith("completed_slices_")])
        if not backups:
            return set()
        
        latest_backup = os.path.join(self.backup_dir, backups[-1])
        try:
            with open(latest_backup, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and "completed" in data:
                    print(f"Recuperado desde backup: {latest_backup}")
                    return set(data["completed"])
        except:
            pass
        return set()
    
    def _cleanup_old_backups(self, keep: int = 10):
        """Mantiene solo los últimos N backups"""
        backups = sorted([f for f in os.listdir(self.backup_dir) if f.startswith("completed_slices_")])
        for old_backup in backups[:-keep]:
            os.remove(os.path.join(self.backup_dir, old_backup))


# Instancia global
robust_persistence = RobustPersistence()