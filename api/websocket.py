"""WebSocket handler para streaming en tiempo real"""

import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect
from .routes import build_state

clients: list[WebSocket] = []


async def websocket_endpoint(ws: WebSocket):
    """Endpoint WebSocket para streaming de estado"""
    await ws.accept()
    clients.append(ws)

    try:
        while True:
            try:
                state = build_state()
                await ws.send_json(state)
            except Exception as e:
                print(f"[WS ERROR] {e}")  # Solo errores
                await ws.send_json({"error": str(e), "gpus": [], "uptime": 0})
            
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        if ws in clients:
            clients.remove(ws)
    except Exception as e:
        print(f"[WS ERROR] {e}")
        if ws in clients:
            clients.remove(ws)