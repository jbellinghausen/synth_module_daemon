"""
Python client for the synth module daemon.

Talks to the daemon over its TCP command port. Hot-path commands (notes, CC)
are fire-and-forget; clock/transport commands wait for an ACK.

Example:
    from synth_module_client import SynthModuleClient

    with SynthModuleClient("raspberrypi.local") as synth:
        synth.cv_note_on(slot=0, note=60, velocity=100)
        synth.cv_gate_off(slot=0)
"""

import json
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .protocol import (
    MSG_SIZE,
    CMD_CV_NOTE_ON, CMD_CV_GATE_OFF,
    CMD_MIDI_NOTE_ON, CMD_MIDI_NOTE_OFF, CMD_MIDI_CC,
    CMD_CLOCK_START, CMD_CLOCK_STOP, CMD_CLOCK_SET_BPM,
    CMD_RUN_STATE, CMD_ALL_NOTES_OFF,
    CMD_QUERY_DEVICES,
    CMD_PING, CMD_SHUTDOWN,
    RESP_PONG, RESP_ACK, RESP_ERROR,
    DEFAULT_PORT, DISCOVERY_PORT,
    DISCOVERY_MAGIC, DISCOVERY_RESPONSE_MAGIC,
    pack_msg, unpack_msg,
)


class SynthModuleError(Exception):
    """Raised when the daemon rejects a command or replies unexpectedly."""


@dataclass
class Pong:
    """Result of a ping: round-trip time and daemon uptime."""

    rtt_ms: float
    uptime_s: int


@dataclass
class DaemonInfo:
    """A daemon found by discover()."""

    host: str
    port: int
    hostname: str = ""
    uptime: int = 0
    busy: bool = False
    ws_port: Optional[int] = None


class SynthModuleClient:
    """TCP client for the synth module daemon.

    Methods are thread-safe. Send failures and reply timeouts raise
    ConnectionError and close the connection; the client does not reconnect
    on its own, call connect() again to retry.
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT,
                 timeout: float = 2.0) -> None:
        """Create a client (does not connect yet).

        Args:
            host: Daemon hostname or IP.
            port: Daemon TCP port.
            timeout: Seconds to wait for connect and for ACK/ping replies.
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()

    # --- Connection ---

    def connect(self) -> "SynthModuleClient":
        """Connect to the daemon and verify it answers a ping.

        Raises:
            ConnectionError: If the daemon is unreachable, busy with another
                client, or does not answer.
        """
        self.close()
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise ConnectionError(f"Cannot connect to {self.host}:{self.port}: {e}") from e
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(self.timeout)
        with self._lock:
            self._sock = sock
        try:
            self.ping()
        except (ConnectionError, SynthModuleError) as e:
            self.close()
            # The daemon closes extra connections straight away
            raise ConnectionError(
                f"{self.host}:{self.port} did not answer (busy with another client?): {e}"
            ) from e
        return self

    def close(self) -> None:
        """Close the connection. The daemon silences all outputs on disconnect."""
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    @property
    def is_connected(self) -> bool:
        """True while a connection is open."""
        return self._sock is not None

    def __enter__(self) -> "SynthModuleClient":
        return self.connect()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- CV / gate ---

    def cv_note_on(self, slot: int, note: int, velocity: int = 127) -> None:
        """Set a slot's pitch CV for a MIDI note number and raise its gate."""
        self._send(CMD_CV_NOTE_ON, slot, note, velocity)

    def cv_gate_off(self, slot: int, note: int = 0) -> None:
        """Lower a slot's gate. Pitch CV holds its last value."""
        self._send(CMD_CV_GATE_OFF, slot, note)

    # --- MIDI ---

    def midi_note_on(self, channel: int, note: int, velocity: int = 100,
                     device: int = 0) -> None:
        """Send a MIDI note-on. channel is 0-15; device 0 = default output."""
        self._send(CMD_MIDI_NOTE_ON, channel, note, velocity, device)

    def midi_note_off(self, channel: int, note: int, device: int = 0) -> None:
        """Send a MIDI note-off."""
        self._send(CMD_MIDI_NOTE_OFF, channel, note, 0, device)

    def midi_cc(self, channel: int, cc: int, value: int, device: int = 0) -> None:
        """Send a MIDI control change."""
        self._send(CMD_MIDI_CC, channel, cc, value, device)

    # --- Clock / transport ---

    def clock_start(self, bpm: int) -> None:
        """Start the hardware clock output at the given BPM."""
        self._send_and_ack(CMD_CLOCK_START, val1=bpm)

    def clock_stop(self) -> None:
        """Stop the hardware clock output."""
        self._send_and_ack(CMD_CLOCK_STOP)

    def set_bpm(self, bpm: int) -> None:
        """Change clock BPM (restarts the clock if it is running)."""
        self._send_and_ack(CMD_CLOCK_SET_BPM, val1=bpm)

    def set_run_state(self, running: bool) -> None:
        """Drive the run/stop output."""
        self._send_and_ack(CMD_RUN_STATE, val1=1 if running else 0)

    # --- Safety / control ---

    def all_notes_off(self) -> None:
        """Drop all gates and send MIDI all-notes-off on every channel."""
        self._send(CMD_ALL_NOTES_OFF)

    def ping(self) -> Pong:
        """Round-trip a ping. Returns RTT and daemon uptime."""
        with self._lock:
            t0 = time.perf_counter()
            self._sendall(pack_msg(CMD_PING))
            cmd, _, uptime, _, _ = unpack_msg(self._recv_exact(MSG_SIZE))
            rtt_ms = (time.perf_counter() - t0) * 1000.0
        if cmd != RESP_PONG:
            raise SynthModuleError(f"Expected PONG, got 0x{cmd:02x}")
        return Pong(rtt_ms=rtt_ms, uptime_s=uptime)

    def query_devices(self) -> Dict[str, Any]:
        """Ask the daemon for its MIDI outputs and CV slot count.

        Returns:
            {"midi_devices": [{"index", "name", "default"}, ...], "cv_slots": int}
        """
        with self._lock:
            self._sendall(pack_msg(CMD_QUERY_DEVICES))
            (length,) = struct.unpack(">I", self._recv_exact(4))
            payload = self._recv_exact(length)
        return json.loads(payload)

    def shutdown_daemon(self) -> None:
        """Ask the daemon process to exit. Under systemd it will not restart."""
        self._send(CMD_SHUTDOWN)

    # --- Internals ---

    def _send(self, cmd: int, slot: int = 0, val1: int = 0, val2: int = 0,
              flags: int = 0) -> None:
        data = self._pack(cmd, slot, val1, val2, flags)
        with self._lock:
            self._sendall(data)

    def _send_and_ack(self, cmd: int, slot: int = 0, val1: int = 0,
                      val2: int = 0, flags: int = 0) -> None:
        data = self._pack(cmd, slot, val1, val2, flags)
        with self._lock:
            self._sendall(data)
            rcmd, _, rval1, rval2, _ = unpack_msg(self._recv_exact(MSG_SIZE))
        if rcmd == RESP_ERROR:
            raise SynthModuleError(f"Daemon rejected command 0x{rval2:02x} (error {rval1})")
        if rcmd != RESP_ACK or rval1 != cmd:
            raise SynthModuleError(f"Expected ACK for 0x{cmd:02x}, got 0x{rcmd:02x}")

    @staticmethod
    def _pack(cmd: int, slot: int, val1: int, val2: int, flags: int) -> bytes:
        try:
            return pack_msg(cmd, slot, val1, val2, flags)
        except struct.error as e:
            raise ValueError(
                f"Value out of range for command 0x{cmd:02x} "
                f"(slot/channel 0-255, values 0-65535): {e}"
            ) from e

    def _sendall(self, data: bytes) -> None:
        """Send bytes. Caller must hold self._lock."""
        if self._sock is None:
            raise ConnectionError("Not connected")
        try:
            self._sock.sendall(data)
        except OSError as e:
            self._drop()
            raise ConnectionError(f"Send failed: {e}") from e

    def _recv_exact(self, n: int) -> bytes:
        """Read exactly n bytes. Caller must hold self._lock."""
        buf = b""
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf)) if self._sock else b""
            except socket.timeout as e:
                # A late reply would be mistaken for the next one; start fresh
                self._drop()
                raise ConnectionError(
                    "Timed out waiting for daemon reply; connection closed") from e
            except OSError as e:
                self._drop()
                raise ConnectionError(f"Receive failed: {e}") from e
            if not chunk:
                self._drop()
                raise ConnectionError("Daemon closed the connection")
            buf += chunk
        return buf

    def _drop(self) -> None:
        """Forget a dead socket. Caller must hold self._lock."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def discover(timeout: float = 2.0, address: str = "<broadcast>",
             port: int = DISCOVERY_PORT) -> List[DaemonInfo]:
    """Find daemons on the LAN via UDP broadcast.

    Args:
        timeout: Seconds to collect replies.
        address: Where to send the probe (broadcast by default).
        port: Discovery UDP port.

    Returns:
        One DaemonInfo per daemon that replied.
    """
    results: List[DaemonInfo] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.sendto(DISCOVERY_MAGIC, (address, port))
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                break
            if not data.startswith(DISCOVERY_RESPONSE_MAGIC):
                continue
            try:
                info = json.loads(data[len(DISCOVERY_RESPONSE_MAGIC):])
            except ValueError:
                info = {}
            results.append(DaemonInfo(
                host=addr[0],
                port=info.get("port", DEFAULT_PORT),
                hostname=info.get("hostname", ""),
                uptime=info.get("uptime", 0),
                busy=info.get("busy", False),
                ws_port=info.get("ws_port"),
            ))
    finally:
        sock.close()
    return results
