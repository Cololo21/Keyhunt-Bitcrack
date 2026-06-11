"""Cálculo de fitness basado en coincidencia de dirección"""

import config
import hashlib  # ← AÑADE ESTA LÍNEA

# Intentar usar coincurve (rápido) o fallback a ecdsa (lento)
try:
    import coincurve
    from coincurve import PublicKey
    COINCURVE_AVAILABLE = True
    print("[FITNESS] Usando coincurve (rápido) ✅")
except ImportError:
    from ecdsa import SigningKey, SECP256k1
    import base58
    COINCURVE_AVAILABLE = False
    print("[FITNESS] Usando ecdsa (lento) ⚠️ - instala coincurve: pip install coincurve")


def privkey_to_address(privkey_int: int) -> str:
    """Convierte una clave privada entera a dirección Bitcoin"""
    try:
        # Convertir entero a bytes (32 bytes)
        privkey_bytes = privkey_int.to_bytes(32, 'big')
        
        if COINCURVE_AVAILABLE:
            # Usar coincurve (rápido)
            pubkey = PublicKey.from_valid_secret(privkey_bytes)
            pubkey_bytes = pubkey.format(compressed=False)[1:]  # 64 bytes sin el 0x04
        else:
            # Usar ecdsa (lento) - fallback
            sk = SigningKey.from_string(privkey_bytes, curve=SECP256k1)
            vk = sk.get_verifying_key()
            pubkey_bytes = b'\x04' + vk.to_string()
            pubkey_bytes = pubkey_bytes[1:]  # quitar el 0x04
        
        # SHA256 + RIPEMD160
        sha256 = hashlib.sha256(pubkey_bytes).digest()
        ripemd160 = hashlib.new('ripemd160', sha256).digest()
        
        # Añadir checksum
        versioned = b'\x00' + ripemd160
        checksum = hashlib.sha256(hashlib.sha256(versioned).digest()).digest()[:4]
        
        # Codificar a base58
        address_bytes = versioned + checksum
        alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
        num = int.from_bytes(address_bytes, 'big')
        
        # Convertir a base58
        address = ''
        while num > 0:
            num, idx = divmod(num, 58)
            address = alphabet[idx] + address
        
        # Añadir leading '1's
        for byte in address_bytes:
            if byte == 0:
                address = '1' + address
            else:
                break
        
        return address
        
    except Exception:
        return ""


def calc_fitness(address: str) -> int:
    """Calcula fitness: cuántos caracteres coinciden con la dirección objetivo"""
    target = config.ADDRESS
    
    if not address or not target:
        return 0
    
    # Contar coincidencias carácter por carácter
    fitness = 0
    min_len = min(len(address), len(target))
    
    for i in range(min_len):
        if address[i] == target[i]:
            fitness += 1
    
    return fitness


def validate_address(address: str) -> bool:
    """Valida si una dirección Bitcoin es potencialmente válida"""
    if not address:
        return False
    
    # Formato básico: comienza con 1 o 3, longitud 26-35
    if not (address.startswith('1') or address.startswith('3')):
        return False
    
    if not (26 <= len(address) <= 35):
        return False
    
    # Caracteres válidos base58
    valid_chars = set('123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz')
    if not all(c in valid_chars for c in address):
        return False
    
    return True