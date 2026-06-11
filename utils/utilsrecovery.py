"""Script de recuperación para restaurar estado después de un apagón o corrupción"""

import sys
import os
import json
import time
import shutil
from datetime import datetime

# Añadir directorio padre al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from monitoring.logs import add_log


class StateRecovery:
    """Recuperador de estado desde backups y checkpoints"""
    
    def __init__(self):
        self.progress_dir = config.PROGRESS_DIR
        self.backup_dir = os.path.join(config.BASE_DIR, "backups", "progress")
        self.checkpoint_dir = os.path.join(config.BASE_DIR, "checkpoints")
        self.results_dir = config.RESULTS_DIR
        
    def find_latest_backup(self) -> str:
        """Encuentra el backup más reciente de slices completados"""
        if not os.path.exists(self.backup_dir):
            return None
        
        backups = [f for f in os.listdir(self.backup_dir) if f.startswith("completed_slices_")]
        if not backups:
            return None
        
        # Ordenar por timestamp en el nombre
        backups.sort(reverse=True)
        latest = os.path.join(self.backup_dir, backups[0])
        print(f"📁 Backup más reciente encontrado: {backups[0]}")
        return latest
    
    def recover_completed_slices(self) -> set:
        """Recupera slices completados desde el mejor origen disponible"""
        
        print("\n" + "="*60)
        print("🔍 RECUPERANDO SLICES COMPLETADOS")
        print("="*60)
        
        completed = set()
        
        # 1. Intentar desde archivo principal
        main_file = config.SLICES_FILE
        if os.path.exists(main_file):
            try:
                with open(main_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "completed" in data:
                        completed = set(data["completed"])
                        print(f"✅ Cargados {len(completed)} slices desde archivo principal")
                    elif isinstance(data, list):
                        completed = set(data)
                        print(f"✅ Cargados {len(completed)} slices desde archivo principal (formato antiguo)")
            except Exception as e:
                print(f"⚠️ Error leyendo archivo principal: {e}")
        
        # 2. Si el principal está vacío o corrupto, usar backup
        if len(completed) == 0:
            backup_file = self.find_latest_backup()
            if backup_file:
                try:
                    with open(backup_file, "r") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "completed" in data:
                            completed = set(data["completed"])
                            print(f"✅ Recuperados {len(completed)} slices desde backup")
                        elif isinstance(data, list):
                            completed = set(data)
                            print(f"✅ Recuperados {len(completed)} slices desde backup (formato antiguo)")
                except Exception as e:
                    print(f"⚠️ Error leyendo backup: {e}")
        
        # 3. Si aún vacío, intentar desde checkpoints
        if len(completed) == 0:
            print("🔄 Buscando en checkpoints...")
            completed = self.recover_from_checkpoints()
        
        print(f"\n📊 TOTAL SLICES RECUPERADOS: {len(completed)}")
        return completed
    
    def recover_from_checkpoints(self) -> set:
        """Recupera slices completados desde checkpoints"""
        completed = set()
        
        if not os.path.exists(self.checkpoint_dir):
            return completed
        
        from config import SLICE_SIZE
        
        for filename in os.listdir(self.checkpoint_dir):
            if not filename.endswith(".json"):
                continue
            
            filepath = os.path.join(self.checkpoint_dir, filename)
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    keys = data.get("keys", 0)
                    slice_num = data.get("slice")
                    
                    # Si tiene más del 99% del slice, considerar completado
                    if keys >= SLICE_SIZE * 0.99:
                        completed.add(slice_num)
                        print(f"  📍 Slice {slice_num} completado al {keys/SLICE_SIZE*100:.1f}%")
            except Exception as e:
                pass
        
        return completed
    
    def recover_gpu_state(self) -> dict:
        """Recupera estado de GPUs desde backups"""
        print("\n" + "="*60)
        print("🖥️ RECUPERANDO ESTADO DE GPUs")
        print("="*60)
        
        gpu_state = {}
        state_file = config.GPU_STATE_FILE
        
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    gpu_state = json.load(f)
                    print(f"✅ Cargado estado de {len(gpu_state)} GPUs")
            except Exception as e:
                print(f"⚠️ Error cargando estado de GPUs: {e}")
        
        return gpu_state
    
    def recover_checkpoints_info(self) -> dict:
        """Muestra información de todos los checkpoints"""
        print("\n" + "="*60)
        print("💾 CHECKPOINTS EXISTENTES")
        print("="*60)
        
        checkpoints = {}
        if not os.path.exists(self.checkpoint_dir):
            print("No hay checkpoints")
            return checkpoints
        
        from config import SLICE_SIZE
        
        for filename in os.listdir(self.checkpoint_dir):
            if not filename.endswith(".json"):
                continue
            
            filepath = os.path.join(self.checkpoint_dir, filename)
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    slice_num = data.get("slice")
                    keys = data.get("keys", 0)
                    gpu = data.get("gpu", filename.split("_")[1])
                    timestamp = data.get("timestamp", os.path.getmtime(filepath))
                    pct = (keys / SLICE_SIZE * 100) if SLICE_SIZE else 0
                    
                    checkpoints[filename] = {
                        "gpu": gpu,
                        "slice": slice_num,
                        "keys": keys,
                        "pct": round(pct, 2),
                        "timestamp": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                    }
                    print(f"  📄 {filename}: GPU {gpu} | Slice {slice_num} | {pct:.1f}%")
            except Exception as e:
                print(f"  ⚠️ Archivo corrupto: {filename}")
        
        return checkpoints
    
    def validate_integrity(self, completed: set) -> list:
        """Valida integridad de los slices recuperados"""
        print("\n" + "="*60)
        print("🔍 VALIDANDO INTEGRIDAD")
        print("="*60)
        
        invalid = []
        for slice_num in completed:
            if slice_num < 0 or slice_num >= config.SLICE_COUNT:
                invalid.append(slice_num)
        
        if invalid:
            print(f"⚠️ Encontrados {len(invalid)} slices inválidos (fuera de rango): {invalid[:10]}...")
        else:
            print("✅ Todos los slices son válidos")
        
        return invalid
    
    def save_recovered_state(self, completed: set):
        """Guarda el estado recuperado"""
        print("\n" + "="*60)
        print("💾 GUARDANDO ESTADO RECUPERADO")
        print("="*60)
        
        # Crear backup del estado actual antes de sobrescribir
        if os.path.exists(config.SLICES_FILE):
            backup_name = f"completed_slices_before_recovery_{int(time.time())}.json"
            backup_path = os.path.join(self.backup_dir, backup_name)
            shutil.copy2(config.SLICES_FILE, backup_path)
            print(f"📁 Backup del estado anterior guardado: {backup_name}")
        
        # Guardar nuevo estado
        try:
            with open(config.SLICES_FILE, "w") as f:
                json.dump({
                    "completed": list(completed),
                    "timestamp": time.time(),
                    "version": 2,
                    "recovered": True
                }, f, indent=2)
            print(f"✅ Estado recuperado guardado: {len(completed)} slices")
        except Exception as e:
            print(f"❌ Error guardando estado: {e}")
    
    def clean_corrupted_files(self):
        """Limpia archivos corruptos"""
        print("\n" + "="*60)
        print("🧹 LIMPIANDO ARCHIVOS CORRUPTOS")
        print("="*60)
        
        cleaned = 0
        
        # Limpiar checkpoints corruptos
        if os.path.exists(self.checkpoint_dir):
            for filename in os.listdir(self.checkpoint_dir):
                if not filename.endswith(".json"):
                    continue
                
                filepath = os.path.join(self.checkpoint_dir, filename)
                try:
                    with open(filepath, "r") as f:
                        json.load(f)
                except:
                    os.remove(filepath)
                    cleaned += 1
                    print(f"  🗑️ Eliminado checkpoint corrupto: {filename}")
        
        print(f"✅ Limpieza completada: {cleaned} archivos eliminados")
    
    def full_recovery(self):
        """Ejecuta recuperación completa"""
        print("\n" + "="*60)
        print("🔄 RECUPERACIÓN COMPLETA DEL SISTEMA")
        print("="*60)
        print(f"📅 Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📂 Directorio base: {config.BASE_DIR}")
        
        # 1. Mostrar checkpoints existentes
        self.recover_checkpoints_info()
        
        # 2. Recuperar slices completados
        completed = self.recover_completed_slices()
        
        # 3. Validar integridad
        invalid = self.validate_integrity(completed)
        for i in invalid:
            completed.discard(i)
        
        # 4. Recuperar estado de GPUs
        gpu_state = self.recover_gpu_state()
        
        # 5. Guardar estado recuperado
        self.save_recovered_state(completed)
        
        # 6. Limpiar archivos corruptos
        self.clean_corrupted_files()
        
        print("\n" + "="*60)
        print("✅ RECUPERACIÓN COMPLETADA")
        print("="*60)
        print(f"📊 Slices completados: {len(completed)}/{config.SLICE_COUNT}")
        print(f"🖥️ GPUs con estado guardado: {len(gpu_state)}")
        print("\n💡 Recomendación: Reinicia el servidor")
        print("   python app.py")
        
        return completed


def main():
    """Función principal"""
    recovery = StateRecovery()
    
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║           BITCRACK STATE RECOVERY TOOL v1.0             ║
    ║                                                          ║
    ║  Recupera slices completados, checkpoints y estado      ║
    ║  después de un apagón o corrupción de datos.            ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    # Preguntar antes de proceder
    response = input("¿Deseas ejecutar la recuperación completa? (s/n): ").lower()
    
    if response == 's':
        recovery.full_recovery()
    else:
        print("\n❌ Recuperación cancelada.")
        print("Para ejecutar manualmente, usa:")
        print("  recovery.full_recovery()")


if __name__ == "__main__":
    main()