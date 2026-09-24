"""Protocol tests for hw_daemon running in dry-run mode (no hardware needed)."""

import json
import socket
import struct
import threading
import time
import unittest

import hw_daemon
from remote_protocol import (
    MSG_SIZE,
    CMD_CLOCK_START, CMD_CLOCK_STOP, CMD_CV_NOTE_ON, CMD_PING,
    CMD_QUERY_DEVICES, CMD_RUN_STATE,
    RESP_ACK, RESP_ERROR, RESP_PONG,
    pack_msg, unpack_msg,
)


class TestHardwareDaemonProtocol(unittest.TestCase):
    """Start a dry-run daemon on an ephemeral port and exercise the protocol."""

    def setUp(self):
        # Ephemeral discovery port so tests don't clash with a running service
        hw_daemon.DISCOVERY_PORT = 0
        self.daemon = hw_daemon.HardwareDaemon(
            hw_daemon.DEFAULT_CONFIG, port=0, dry_run=True)
        self.thread = threading.Thread(target=self.daemon.run, daemon=True)
        self.thread.start()

        deadline = time.monotonic() + 5
        while self.daemon.server_sock is None or \
                self.daemon.server_sock.getsockname()[1] == 0:
            if time.monotonic() > deadline:
                self.fail("daemon did not start listening")
            time.sleep(0.01)
        port = self.daemon.server_sock.getsockname()[1]

        self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)

    def tearDown(self):
        self.sock.close()
        self.daemon.running = False
        self.thread.join(timeout=3)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("daemon closed connection")
            buf += chunk
        return buf

    def _request(self, cmd: int, **kwargs) -> tuple:
        self.sock.sendall(pack_msg(cmd, **kwargs))
        return unpack_msg(self._recv_exact(MSG_SIZE))

    def test_ping(self):
        resp_cmd, *_ = self._request(CMD_PING)
        self.assertEqual(resp_cmd, RESP_PONG)

    def test_clock_and_run_state_are_acked(self):
        for cmd, val1 in [(CMD_CLOCK_START, 120), (CMD_RUN_STATE, 1),
                          (CMD_RUN_STATE, 0), (CMD_CLOCK_STOP, 0)]:
            resp_cmd, _, acked, _, _ = self._request(cmd, val1=val1)
            self.assertEqual(resp_cmd, RESP_ACK)
            self.assertEqual(acked, cmd)

    def test_query_devices(self):
        self.sock.sendall(pack_msg(CMD_QUERY_DEVICES))
        (length,) = struct.unpack(">I", self._recv_exact(4))
        info = json.loads(self._recv_exact(length))
        self.assertEqual(info["cv_slots"], len(hw_daemon.DEFAULT_CONFIG["slots"]))
        self.assertIsInstance(info["midi_devices"], list)

    def test_unknown_command_returns_error(self):
        resp_cmd, _, _, failed_cmd, _ = self._request(0x99)
        self.assertEqual(resp_cmd, RESP_ERROR)
        self.assertEqual(failed_cmd, 0x99)

    def test_fire_and_forget_does_not_block_following_request(self):
        self.sock.sendall(pack_msg(CMD_CV_NOTE_ON, slot=0, val1=60, val2=100))
        resp_cmd, *_ = self._request(CMD_PING)
        self.assertEqual(resp_cmd, RESP_PONG)


if __name__ == "__main__":
    unittest.main()
