"""Tests for the Python client against a dry-run daemon."""

import unittest

from helpers import DryRunDaemon, hw_daemon
from synth_module_client import SynthModuleClient, SynthModuleError, discover


class TestSynthModuleClient(unittest.TestCase):
    """Drive the daemon through the public client API."""

    def setUp(self):
        self.fixture = DryRunDaemon(websocket=False)
        self.client = SynthModuleClient("127.0.0.1", self.fixture.tcp_port).connect()

    def tearDown(self):
        self.client.close()
        self.fixture.stop()

    def test_ping(self):
        pong = self.client.ping()
        self.assertGreaterEqual(pong.rtt_ms, 0)
        self.assertGreaterEqual(pong.uptime_s, 0)

    def test_query_devices(self):
        info = self.client.query_devices()
        self.assertEqual(info["cv_slots"], len(hw_daemon.DEFAULT_CONFIG["slots"]))

    def test_notes_cc_and_transport(self):
        self.client.cv_note_on(0, 60, 100)
        self.client.cv_gate_off(0)
        self.client.midi_note_on(0, 64)
        self.client.midi_note_off(0, 64)
        self.client.midi_cc(0, 74, 90)
        self.client.clock_start(120)
        self.client.set_bpm(140)
        self.client.set_run_state(True)
        self.client.set_run_state(False)
        self.client.clock_stop()
        self.client.all_notes_off()
        self.assertTrue(self.fixture.daemon.clock_running is False)
        self.client.ping()  # connection still healthy

    def test_clock_state_reaches_daemon(self):
        self.client.clock_start(133)
        self.assertTrue(self.fixture.daemon.clock_running)
        self.assertEqual(self.fixture.daemon.current_bpm, 133)

    def test_out_of_range_value_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.client.cv_note_on(300, 60)
        self.client.ping()  # nothing half-sent

    def test_send_after_close_raises_connection_error(self):
        self.client.close()
        self.assertFalse(self.client.is_connected)
        with self.assertRaises(ConnectionError):
            self.client.cv_note_on(0, 60)

    def test_second_client_cannot_connect(self):
        other = SynthModuleClient("127.0.0.1", self.fixture.tcp_port, timeout=1)
        with self.assertRaises(ConnectionError):
            other.connect()

    def test_disconnect_stops_clock(self):
        self.client.clock_start(120)
        self.client.close()
        self.fixture.wait_until_idle()
        self.assertFalse(self.fixture.daemon.clock_running)

    def test_context_manager(self):
        self.client.close()
        self.fixture.wait_until_idle()
        with SynthModuleClient("127.0.0.1", self.fixture.tcp_port) as synth:
            self.assertTrue(synth.is_connected)
        self.assertFalse(synth.is_connected)


class TestConnectFailures(unittest.TestCase):
    """Connection errors surface as ConnectionError."""

    def test_nothing_listening(self):
        with self.assertRaises(ConnectionError):
            SynthModuleClient("127.0.0.1", 1, timeout=0.5).connect()


class TestDiscover(unittest.TestCase):
    """UDP discovery (sent directly to localhost rather than broadcast)."""

    def setUp(self):
        self.fixture = DryRunDaemon()

    def tearDown(self):
        self.fixture.stop()

    def test_discover_finds_daemon(self):
        found = discover(timeout=0.5, address="127.0.0.1", port=self.fixture.discovery_port)
        self.assertEqual(len(found), 1)
        info = found[0]
        self.assertEqual(info.host, "127.0.0.1")
        self.assertEqual(info.port, self.fixture.tcp_port)
        self.assertEqual(info.ws_port, self.fixture.ws_port)
        self.assertFalse(info.busy)


if __name__ == "__main__":
    unittest.main()
