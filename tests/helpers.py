"""Shared test fixture: a dry-run daemon on ephemeral ports."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "daemon"))

import hw_daemon  # noqa: E402


class DryRunDaemon:
    """Runs HardwareDaemon in dry-run mode on ephemeral TCP/UDP/WebSocket ports."""

    def __init__(self, websocket: bool = True) -> None:
        # Ephemeral discovery port so tests don't clash with a running service
        hw_daemon.DISCOVERY_PORT = 0
        self.daemon = hw_daemon.HardwareDaemon(
            hw_daemon.DEFAULT_CONFIG, port=0, dry_run=True,
            ws_port=0 if websocket else None)
        self.thread = threading.Thread(target=self.daemon.run, daemon=True)
        self.thread.start()
        self.tcp_port = self._wait_for(lambda: self._bound_port(self.daemon.server_sock))
        self.discovery_port = self._wait_for(lambda: self.daemon.discovery_port)
        self.ws_port = None
        if websocket:
            self.ws_port = self._wait_for(
                lambda: self.daemon.ws_server and self.daemon.ws_server.socket.getsockname()[1])

    @staticmethod
    def _bound_port(sock) -> int:
        return sock.getsockname()[1] if sock is not None else 0

    @staticmethod
    def _wait_for(fn, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = fn()
            if value:
                return value
            time.sleep(0.01)
        raise RuntimeError("daemon did not start listening")

    def wait_until_idle(self, timeout: float = 3.0) -> None:
        """Wait for the daemon to notice a client disconnect."""
        self._wait_for(lambda: self.daemon.active_client is None, timeout)

    def stop(self) -> None:
        self.daemon.running = False
        self.thread.join(timeout=5)
