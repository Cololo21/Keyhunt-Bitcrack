"""Middleware de seguridad y rate limiting"""

from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import API_TOKEN  # ← Línea corregida
from monitoring.logs import add_log

security = HTTPBearer(auto_error=False)


def verify_token(creds: HTTPAuthorizationCredentials = Depends(security)):
    """Verifica el token de API"""
    if not creds or creds.credentials != API_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")
    return True


def log_access(request: Request, status: int):
    """Registra acceso a endpoints (implementado en routes.py)"""
    pass