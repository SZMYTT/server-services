"""
Live log broadcast: tails journalctl for both prisma services and pushes
every line to all connected WebSocket clients plus an in-memory ring buffer.
"""
import asyncio
import logging
from collections import deque
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger("prisma.log_stream")

RING: deque[str] = deque(maxlen=500)
CONNECTIONS: Set[WebSocket] = set()


async def _broadcast(msg: str) -> None:
    RING.append(msg)
    dead: Set[WebSocket] = set()
    for ws in CONNECTIONS:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    CONNECTIONS.difference_update(dead)


async def tail_journal() -> None:
    """
    Tail journalctl for both prisma services indefinitely.
    Reconnects automatically on failure.
    Requires: sudo usermod -aG systemd-journal <your-user>  (once)
    """
    units = ["-u", "prisma-orchestrator", "-u", "prisma-web"]
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", *units,
                "-f", "-n", "50", "--no-pager", "--output=short-iso",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                await _broadcast(line.decode("utf-8", errors="replace").rstrip())
        except Exception as exc:
            logger.warning("[LOG_STREAM] journalctl failed (%s) — retry in 10s", exc)
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
        await asyncio.sleep(10)
