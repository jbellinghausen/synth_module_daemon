"""Raw protocol tests for hw_daemon over TCP and WebSocket (dry-run, no hardware)."""

import json
import socket
import struct
import unittest

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect

from helpers import DryRunDaemon, hw_daemon
from synth_module_client.protocol import (
    MSG_SIZE,
    CMD_CLOCK_START, CMD_CLOCK_STOP, CMD_CV_NOTE_ON, CMD_PING,
    CMD_QUERY_DEVICES, CMD_RUN_STATE,
    RESP_ACK, RESP_ERROR, RESP_PONG,
    pack_msg, unpack_msg,
)


class TestTcpProtocol(unittest.TestCase):
    """Exercise the raw TCP protocol."""

    def setUp(self):
        self.fixture = DryRunDaemon()
        self.sock = socket.create_connection(("127.0.0.1", self.fixture.tcp_port), timeout=2)

    def tearDown(self):
        self.sock.close()
        self.fixture.stop()

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

    def test_second_tcp_client_is_rejected(self):
        self._request(CMD_PING)  # make sure the first client is registered
        other = socket.create_connection(("127.0.0.1", self.fixture.tcp_port), timeout=2)
        try:
            self.assertEqual(other.recv(8), b"")  # closed by daemon
        finally:
            other.close()


class TestWebSocketProtocol(unittest.TestCase):
    """Exercise the same protocol over WebSocket binary frames."""

    def setUp(self):
        self.fixture = DryRunDaemon()
        self.url = f"ws://127.0.0.1:{self.fixture.ws_port}"

    def tearDown(self):
        self.fixture.stop()

    def test_ping_and_ack(self):
        with ws_connect(self.url) as ws:
            ws.send(pack_msg(CMD_PING))
            self.assertEqual(unpack_msg(ws.recv(timeout=2))[0], RESP_PONG)
            ws.send(pack_msg(CMD_CLOCK_START, val1=120))
            resp_cmd, _, acked, _, _ = unpack_msg(ws.recv(timeout=2))
            self.assertEqual((resp_cmd, acked), (RESP_ACK, CMD_CLOCK_START))

    def test_multiple_messages_in_one_frame(self):
        with ws_connect(self.url) as ws:
            ws.send(pack_msg(CMD_CV_NOTE_ON, slot=1, val1=60, val2=100) + pack_msg(CMD_PING))
            self.assertEqual(unpack_msg(ws.recv(timeout=2))[0], RESP_PONG)

    def test_query_devices(self):
        with ws_connect(self.url) as ws:
            ws.send(pack_msg(CMD_QUERY_DEVICES))
            frame = ws.recv(timeout=2)
            (length,) = struct.unpack(">I", frame[:4])
            info = json.loads(frame[4:4 + length])
            self.assertEqual(info["cv_slots"], len(hw_daemon.DEFAULT_CONFIG["slots"]))

    def test_websocket_rejected_while_tcp_client_connected(self):
        sock = socket.create_connection(("127.0.0.1", self.fixture.tcp_port), timeout=2)
        try:
            sock.sendall(pack_msg(CMD_PING))
            sock.recv(MSG_SIZE)
            with ws_connect(self.url) as ws:
                with self.assertRaises(ConnectionClosed) as ctx:
                    ws.recv(timeout=2)
                self.assertEqual(ctx.exception.rcvd.code, 1013)
        finally:
            sock.close()

    def test_client_slot_freed_after_websocket_disconnect(self):
        with ws_connect(self.url) as ws:
            ws.send(pack_msg(CMD_PING))
            ws.recv(timeout=2)
        self.fixture.wait_until_idle()
        sock = socket.create_connection(("127.0.0.1", self.fixture.tcp_port), timeout=2)
        try:
            sock.sendall(pack_msg(CMD_PING))
            self.assertEqual(unpack_msg(sock.recv(MSG_SIZE))[0], RESP_PONG)
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
