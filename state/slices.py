"""Gestor de slices con persistencia JSON (sin SQLite)"""

import json
import os
import time
from typing import Set, Dict, Optional
import config
import random
from monitoring.logs import add_log

class SliceManager:
    """Gestiona slices completados, progreso y checkpoints con JSON"""
    
    def __init__(self):
        self.completed_set: Set[int] = set()
        self.assigned: Dict[int, int] = {}
        self.slice_progress: Dict[int, int] = {}
        self.load_state()
    
    def get_completed_set(self) -> set:
        return self.completed_set
    
    def get_completed_count(self) -> int:
        return len(self.completed_set)
    
    def get_assigned(self, gpu: int) -> Optional[int]:
        return self.assigned.get(gpu)
    
    def get_all_assignments(self) -> dict:
        return self.assigned.copy()
    
    def get_slice_progress(self, slice_num: int) -> int:
        return self.slice_progress.get(slice_num, 0)
    
    def update_slice_progress(self, slice_num: int, keys_processed: int, gpu_id: int = None):
        if not self.is_completed(slice_num):
            self.slice_progress[slice_num] = keys_processed
            self._save_progress()
    
    def is_completed(self, slice_num: int) -> bool:
        return slice_num in self.completed_set
    
    def mark_completed(self, slice_num: int, gpu_id: int = None):
        if slice_num not in self.completed_set:
            self.completed_set.add(slice_num)
            if slice_num in self.slice_progress:
                del self.slice_progress[slice_num]
            self.save_state()
            add_log(f"Slice {slice_num} completado", "OK")
    
    def mark_pending(self, slice_num: int):
        if slice_num in self.completed_set:
            self.completed_set.remove(slice_num)
            self.save_state()
            add_log(f"Slice {slice_num} marcado como pendiente", "WARN")
    
    def assign_slice(self, gpu: int, slice_num: int):
        self.assigned[gpu] = slice_num
        self._save_assignments()
    
    def next_available_slice(self, explore_rate: float, bandit, best_key_int: int, 
                             explore_sigma: float, elite_history: list) -> Optional[int]:
        from config import SLICE_COUNT
        
        available = [i for i in range(SLICE_COUNT) if i not in self.completed_set]
        if not available:
            return None
        
        if random.random() < explore_rate:
            candidates = []
            for s in available:
                progress = self.slice_progress.get(s, 0)
                weight = progress / 1_000_000 if progress > 0 else 0
                candidates.extend([s] * max(1, int(weight + 1)))
            return random.choice(candidates)
        else:
            candidates = [(s, self.slice_progress.get(s, 0)) for s in available]
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0] if candidates else random.choice(available)
    
    def save_checkpoint(self, gpu: int, slice_num: int, keys_processed: int):
        """Guarda checkpoint en JSON"""
        checkpoint_dir = os.path.join(config.BASE_DIR, "checkpoints")
        os.makedirs(checkpoint_dir, exist_ok=True)
        checkpoint_file = os.path.join(checkpoint_dir, f"gpu_{gpu}_slice_{slice_num}.json")
        try:
            with open(checkpoint_file, "w") as f:
                json.dump({
                    "slice": slice_num,
                    "keys": keys_processed,
                    "gpu": gpu,
                    "timestamp": time.time()
                }, f)
            add_log(f"Checkpoint guardado: GPU {gpu} slice {slice_num}", "INFO")
        except Exception as e:
            add_log(f"Error guardando checkpoint: {e}", "WARN")
    
    def load_checkpoint(self, gpu: int, slice_num: int) -> int:
        checkpoint_dir = os.path.join(config.BASE_DIR, "checkpoints")
        checkpoint_file = os.path.join(checkpoint_dir, f"gpu_{gpu}_slice_{slice_num}.json")
        if os.path.exists(checkpoint_file):
            try:
                with open(checkpoint_file, "r") as f:
                    data = json.load(f)
                    return data.get("keys", 0)
            except:
                pass
        return 0
    
    def clear_checkpoint(self, gpu: int, slice_num: int):
        checkpoint_dir = os.path.join(config.BASE_DIR, "checkpoints")
        checkpoint_file = os.path.join(checkpoint_dir, f"gpu_{gpu}_slice_{slice_num}.json")
        if os.path.exists(checkpoint_file):
            try:
                os.remove(checkpoint_file)
            except:
                pass
    
    def _save_assignments(self):
        assign_file = os.path.join(config.PROGRESS_DIR, "assignments.json")
        try:
            with open(assign_file, "w") as f:
                json.dump(self.assigned, f)
        except:
            pass
    
    def _save_progress(self):
        progress_file = os.path.join(config.PROGRESS_DIR, "slice_progress.json")
        try:
            with open(progress_file, "w") as f:
                json.dump(self.slice_progress, f)
        except:
            pass
    
    def save_state(self):
        state_file = config.SLICES_FILE
        try:
            with open(state_file, "w") as f:
                json.dump({
                    "completed": list(self.completed_set),
                    "assigned": self.assigned,
                    "progress": self.slice_progress,
                    "timestamp": time.time()
                }, f)
        except Exception as e:
            add_log(f"Error guardando estado: {e}", "ERROR")
    
    def load_state(self):
        state_file = config.SLICES_FILE
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    data = json.load(f)
                    self.completed_set = set(data.get("completed", []))
                    self.assigned = data.get("assigned", {})
                    self.slice_progress = data.get("progress", {})
                add_log(f"Estado cargado: {len(self.completed_set)} completados, {len(self.slice_progress)} en progreso", "INFO")
            except Exception as e:
                add_log(f"Error cargando estado: {e}", "WARN")
        
        # Cargar progreso de archivos separados si existe
        progress_file = os.path.join(config.PROGRESS_DIR, "slice_progress.json")
        if os.path.exists(progress_file):
            try:
                with open(progress_file, "r") as f:
                    self.slice_progress.update(json.load(f))
            except:
                pass
    
    def save(self):
        self.save_state()
    
    def get_stats(self) -> dict:
        return {
            "completed_slices": len(self.completed_set),
            "slices_in_progress": len(self.slice_progress),
            "checkpoints": 0
        }
    
    @property
    def completed_set_prop(self):
        return self.completed_set


# Instancia global
slice_manager = SliceManager()