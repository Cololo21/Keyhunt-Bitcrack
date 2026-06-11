"""Funciones auxiliares generales"""


def format_duration(seconds: int) -> str:
    """Formatea segundos a HH:MM:SS"""
    if seconds is None:
        return "N/A"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_keys(keys: int) -> str:
    """Formatea número de claves (ej: 1.2M, 3.4B)"""
    if keys < 1_000:
        return str(keys)
    if keys < 1_000_000:
        return f"{keys/1_000:.1f}K"
    if keys < 1_000_000_000:
        return f"{keys/1_000_000:.1f}M"
    return f"{keys/1_000_000_000:.2f}B"