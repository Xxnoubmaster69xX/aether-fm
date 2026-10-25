"""
Zero-Touch Remote Uplink & Auto-Handshake Beacon (remote_tunnel.py)
Punches outbound through Starlink / ISP CGNAT using Windows' built-in OpenSSH client
(`ssh -R 80:127.0.0.1:8000 nokey@localhost.run`) with zero extra software installs,
and publishes the live HTTPS/WSS tunnel hostname to a deterministic rendezvous beacon
so the FM Android app automatically discovers and handshakes with the home PC from anywhere.
"""

import asyncio
import json
import re
import socket
import subprocess
import threading
import time
from typing import Callable, Coroutine, Any, Optional
import httpx

BEACON_TOPIC = "fm_node_sync_83_d71cd629"
BEACON_PUBLISH_URL = f"https://ntfy.sh/{BEACON_TOPIC}"
BEACON_POLL_URL = f"https://ntfy.sh/{BEACON_TOPIC}/raw?poll=1&since=all"


def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "192.168.3.85"


class AutoHandshakeTunnelManager:
    def __init__(self, local_port: int = 8000):
        self.local_port = local_port
        self.lan_ip: str = detect_lan_ip()
        self.remote_host: str = ""
        self.remote_url: str = ""
        self.status: str = "INITIALIZING"
        self.last_published_ts: float = 0.0
        self._running: bool = False
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._beacon_task: Optional[asyncio.Task] = None
        self.on_tunnel_updated: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        self._running = True
        self._loop = loop
        self._thread = threading.Thread(target=self._ssh_tunnel_worker, daemon=True)
        self._thread.start()
        self._beacon_task = loop.create_task(self._periodic_beacon_refresh())

    def stop(self) -> None:
        self._running = False
        if self._beacon_task and not self._beacon_task.done():
            self._beacon_task.cancel()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _ssh_tunnel_worker(self) -> None:
        """
        Maintains an outbound SSH reverse tunnel via localhost.run using Windows' built-in ssh.exe.
        Automatically reconnects if Starlink switches satellites or the session rotates.
        """
        url_pattern = re.compile(r"([a-zA-Z0-9-]+\.lhr\.life)")
        while self._running:
            try:
                self.status = "CONNECTING"
                cmd = [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=NUL",
                    "-o",
                    "ServerAliveInterval=25",
                    "-o",
                    "ServerAliveCountMax=3",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-R",
                    f"80:127.0.0.1:{self.local_port}",
                    "nokey@localhost.run",
                ]
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    if not self._running:
                        break
                    m = url_pattern.search(line)
                    if m:
                        new_host = m.group(1).strip()
                        if new_host and new_host != self.remote_host:
                            self.remote_host = new_host
                            self.remote_url = f"https://{new_host}"
                            self.status = "ONLINE"
                            if self._loop:
                                asyncio.run_coroutine_threadsafe(
                                    self.publish_beacon(), self._loop
                                )
                                if self.on_tunnel_updated:
                                    asyncio.run_coroutine_threadsafe(
                                        self.on_tunnel_updated(self.remote_url),
                                        self._loop,
                                    )
                if self._proc:
                    self._proc.wait(timeout=3)
            except Exception:
                pass
            if self._running:
                self.status = "RECONNECTING"
                time.sleep(3.0)

    async def publish_beacon(self) -> None:
        if not self.remote_host:
            return
        self.lan_ip = detect_lan_ip()
        payload = {
            "station": "FM",
            "remote_host": self.remote_host,
            "remote_url": self.remote_url,
            "lan_host": self.lan_ip,
            "lan_port": self.local_port,
            "updated_at": int(time.time()),
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                await client.post(
                    BEACON_PUBLISH_URL,
                    content=json.dumps(payload),
                    headers={"Title": "FM-Node-Handshake", "Priority": "default"},
                )
                self.last_published_ts = time.time()
        except Exception:
            pass

    async def _periodic_beacon_refresh(self) -> None:
        while self._running:
            await asyncio.sleep(600.0)  # Refresh beacon every 10 minutes
            if self.remote_host:
                await self.publish_beacon()
