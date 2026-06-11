"""Persistencia con SQLite - más rápido y confiable"""

import sqlite3
import json
import time
import os
import threading
from typing import Set, Dict, Optional
import config

DB_PATH = os.path.join(config.PROGRESS_DIR, "bitcrack_state.db")
DB_LOCK = threading.Lock()


class Database:
    """Gestor de base de datos SQLite para persistencia"""
    
    def __init__(self):
        self._init_db()
        self._migrate()
    
    def _init_db(self):
        """Inicializa la base de datos"""
        os.makedirs(config.PROGRESS_DIR, exist_ok=True)
        
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            
            # Tabla de slices completados
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS completed_slices (
                    slice_num INTEGER PRIMARY KEY,
                    completed_at REAL,
                    completed_by INTEGER
                )
            ''')
            
            # Tabla de progreso de slices (NO completados)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS slice_progress (
                    slice_num INTEGER PRIMARY KEY,
                    keys_processed INTEGER DEFAULT 0,
                    last_updated REAL,
                    gpu_id INTEGER,
                    percentage REAL DEFAULT 0
                )
            ''')
            
            # Tabla de asignaciones de GPU
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gpu_assignments (
                    gpu_id INTEGER PRIMARY KEY,
                    slice_num INTEGER,
                    assigned_at REAL,
                    status TEXT DEFAULT 'active'
                )
            ''')
            
            # Tabla de checkpoints
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gpu_id INTEGER,
                    slice_num INTEGER,
                    keys_processed INTEGER,
                    timestamp REAL,
                    UNIQUE(gpu_id, slice_num)
                )
            ''')
            
            # Tabla de metadata
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at REAL
                )
            ''')
            
            # Índices para búsquedas rápidas
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_progress_slice ON slice_progress(slice_num)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_checkpoints_gpu ON checkpoints(gpu_id, slice_num)')
            
            conn.commit()
            conn.close()
    
    def _migrate(self):
        """Migra datos existentes desde JSON si es necesario"""
        # Verificar si hay datos en JSON y migrar
        completed_file = config.SLICES_FILE
        if os.path.exists(completed_file) and self.get_completed_count() == 0:
            try:
                with open(completed_file, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        completed = data.get('completed', [])
                    else:
                        completed = data
                
                for slice_num in completed:
                    self.add_completed_slice(slice_num)
                print(f"[DB] Migrados {len(completed)} slices completados")
            except:
                pass
    
    def add_completed_slice(self, slice_num: int, gpu_id: int = None):
        """Marca un slice como completado"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO completed_slices (slice_num, completed_at, completed_by) VALUES (?, ?, ?)',
                (slice_num, time.time(), gpu_id)
            )
            # Eliminar progreso si existe
            cursor.execute('DELETE FROM slice_progress WHERE slice_num = ?', (slice_num,))
            conn.commit()
            conn.close()
    
    def remove_completed_slice(self, slice_num: int):
        """Elimina un slice de completados (para marcarlo como pendiente)"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM completed_slices WHERE slice_num = ?', (slice_num,))
            conn.commit()
            conn.close()
    
    def is_completed(self, slice_num: int) -> bool:
        """Verifica si un slice está completado"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM completed_slices WHERE slice_num = ?', (slice_num,))
            result = cursor.fetchone() is not None
            conn.close()
            return result
    
    def get_completed_slices(self) -> Set[int]:
        """Retorna todos los slices completados"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('SELECT slice_num FROM completed_slices')
            result = {row[0] for row in cursor.fetchall()}
            conn.close()
            return result
    
    def get_completed_count(self) -> int:
        """Retorna el número de slices completados"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM completed_slices')
            count = cursor.fetchone()[0]
            conn.close()
            return count
    
    def update_slice_progress(self, slice_num: int, keys_processed: int, gpu_id: int = None):
        """Actualiza el progreso de un slice no completado"""
        from config import SLICE_SIZE
        percentage = (keys_processed / SLICE_SIZE * 100) if SLICE_SIZE else 0
        
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO slice_progress 
                (slice_num, keys_processed, last_updated, gpu_id, percentage) 
                VALUES (?, ?, ?, ?, ?)
            ''', (slice_num, keys_processed, time.time(), gpu_id, round(percentage, 2)))
            conn.commit()
            conn.close()
    
    def get_slice_progress(self, slice_num: int) -> int:
        """Obtiene el progreso de un slice"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('SELECT keys_processed FROM slice_progress WHERE slice_num = ?', (slice_num,))
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else 0
    
    def get_all_progress(self) -> Dict[int, int]:
        """Obtiene todo el progreso de slices"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('SELECT slice_num, keys_processed FROM slice_progress')
            result = {row[0]: row[1] for row in cursor.fetchall()}
            conn.close()
            return result
    
    def save_checkpoint(self, gpu_id: int, slice_num: int, keys_processed: int):
        """Guarda un checkpoint para reanudar exactamente"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO checkpoints (gpu_id, slice_num, keys_processed, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (gpu_id, slice_num, keys_processed, time.time()))
            conn.commit()
            conn.close()
    
    def load_checkpoint(self, gpu_id: int, slice_num: int) -> int:
        """Carga un checkpoint"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute(
                'SELECT keys_processed FROM checkpoints WHERE gpu_id = ? AND slice_num = ?',
                (gpu_id, slice_num)
            )
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else 0
    
    def clear_checkpoint(self, gpu_id: int, slice_num: int):
        """Limpia un checkpoint"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM checkpoints WHERE gpu_id = ? AND slice_num = ?', (gpu_id, slice_num))
            conn.commit()
            conn.close()
    
    def assign_slice_to_gpu(self, gpu_id: int, slice_num: int):
        """Asigna un slice a una GPU"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO gpu_assignments (gpu_id, slice_num, assigned_at, status)
                VALUES (?, ?, ?, 'active')
            ''', (gpu_id, slice_num, time.time()))
            conn.commit()
            conn.close()
    
    def get_gpu_assignment(self, gpu_id: int) -> Optional[int]:
        """Obtiene el slice asignado a una GPU"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('SELECT slice_num FROM gpu_assignments WHERE gpu_id = ? AND status = "active"', (gpu_id,))
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
    
    def get_all_assignments(self) -> Dict[int, int]:
        """Obtiene todas las asignaciones"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('SELECT gpu_id, slice_num FROM gpu_assignments WHERE status = "active"')
            result = {row[0]: row[1] for row in cursor.fetchall()}
            conn.close()
            return result
    
    def clear_gpu_assignment(self, gpu_id: int):
        """Limpia la asignación de una GPU"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute('UPDATE gpu_assignments SET status = "completed" WHERE gpu_id = ?', (gpu_id,))
            conn.commit()
            conn.close()
    
    def get_stats(self) -> dict:
        """Obtiene estadísticas de la base de datos"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM completed_slices')
            completed = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM slice_progress')
            in_progress = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM checkpoints')
            checkpoints = cursor.fetchone()[0]
            
            conn.close()
            return {
                "completed_slices": completed,
                "slices_in_progress": in_progress,
                "checkpoints": checkpoints
            }
    
    def vacuum(self):
        """Optimiza la base de datos"""
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.execute('VACUUM')
            conn.close()
    
    def backup(self, backup_path: str = None):
        """Crea un backup de la base de datos"""
        if backup_path is None:
            backup_path = os.path.join(config.BACKUP_DIR, f"state_backup_{int(time.time())}.db")
        
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            backup_conn = sqlite3.connect(backup_path)
            conn.backup(backup_conn)
            backup_conn.close()
            conn.close()
        
        return backup_path


# Instancia global
db = Database()