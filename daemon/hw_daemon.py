#!/usr/bin/env python3
"""
AI MIDI Remote Hardware Daemon

Lightweight daemon that runs on Raspberry Pi and drives GPIO/DAC/MIDI
hardware in response to commands received over TCP (or WebSocket, for
browsers) from a remote sequencer. See PROTOCOL.md.

Usage:
    python hw_daemon.py                    # Run with default settings
    python hw_daemon.py --dry-run          # Log commands without hardware
    python hw_daemon.py --port 9741        # Custom TCP port
    python hw_daemon.py --ws-port 9743     # Custom WebSocket port
    python hw_daemon.py --no-websocket     # TCP only
    python hw_daemon.py --config hw.json   # Custom hardware config

Install as a boot service on the Pi:
    daemon/install.sh
"""

import argparse
import json
import logging
import select
import signal
import socket
import struct
import sys
import threading
import time
from pathlib import Path

try:
    import synth_module_client.protocol  # noqa: F401
except ImportError:
    # Running from a checkout without the client package installed
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "python"))

from synth_module_client.protocol import (
    MSG_FORMAT, MSG_SIZE,
    CMD_CV_NOTE_ON, CMD_CV_GATE_OFF,
    CMD_MIDI_NOTE_ON, CMD_MIDI_NOTE_OFF, CMD_MIDI_CC,
    CMD_CLOCK_START, CMD_CLOCK_STOP, CMD_CLOCK_SET_BPM,
    CMD_RUN_STATE, CMD_ALL_NOTES_OFF,
    CMD_QUERY_DEVICES,
    CMD_PING, CMD_SHUTDOWN,
    RESP_PONG, RESP_ACK, RESP_ERROR,
    DEFAULT_PORT, DISCOVERY_PORT, WEBSOCKET_PORT,
    DISCOVERY_MAGIC, DISCOVERY_RESPONSE_MAGIC,
    pack_msg, unpack_msg,
)

logger = logging.getLogger("hw_daemon")

# --- Hardware imports (conditional) ---

GPIO = None
smbus2 = None
mido = None

def _init_hw_imports(dry_run):
    global GPIO, smbus2, mido
    if not dry_run:
        try:
            import RPi.GPIO as _gpio
            GPIO = _gpio
        except (ImportError, RuntimeError) as e:
            logger.error(f"RPi.GPIO not available (on Pi 5 install rpi-lgpio): {e}")
            sys.exit(1)
        try:
            import smbus2 as _smbus
            smbus2 = _smbus
        except ImportError:
            logger.warning("smbus2 not available - DAC output disabled")
    try:
        import mido as _mido
        mido = _mido
    except ImportError:
        logger.info("mido not available - MIDI output disabled")


# --- Default hardware config (matches gpio_config.py) ---

DEFAULT_CONFIG = {
    "slots": [
        {"id": 0, "gate_pin": 14, "dac": "0:0"},
        {"id": 1, "gate_pin": 26, "dac": "0:1"},
        {"id": 2, "gate_pin": 19, "dac": "0:2"},
        {"id": 3, "gate_pin": 13, "dac": "0:3"},
        {"id": 4, "gate_pin": 6,  "dac": "1:0"},
        {"id": 5, "gate_pin": 5,  "dac": "1:1"},
        {"id": 6, "gate_pin": 11, "dac": "1:2"},
        {"id": 7, "gate_pin": 9,  "dac": "1:3"},
        {"id": 8, "gate_pin": 10, "dac": "2:0"},
        {"id": 9, "gate_pin": 22, "dac": "2:1"},
        {"id": 10, "gate_pin": 27, "dac": "2:2"},
        {"id": 11, "gate_pin": 17, "dac": "2:3"},
    ],
    "dacs": [
        {"id": 0, "address": 96, "vref": 3.3},   # 0x60
        {"id": 1, "address": 97, "vref": 3.3},   # 0x61
        {"id": 2, "address": 98, "vref": 3.3},   # 0x62
    ],
    "clock_pin": 18,
    "run_pin": 15,
    "loopback_output_pin": 4,
    "loopback_input_pin": 23,
    "ppqn": 4,
    "ticks_per_step": 10,
}


class MCP4728:
    """Minimal MCP4728 DAC driver using smbus2."""

    I2C_BUS = 1  # Raspberry Pi default

    def __init__(self, address, vref=3.3):
        self.address = address
        self.vref = vref
        self.bus = None

    def start(self):
        if smbus2 is None:
            return False
        try:
            self.bus = smbus2.SMBus(self.I2C_BUS)
            return True
        except Exception as e:
            logger.error(f"Failed to open I2C for DAC 0x{self.address:02x}: {e}")
            return False

    def stop(self):
        if self.bus:
            try:
                self.bus.close()
            except Exception:
                pass
            self.bus = None

    def set_voltage(self, channel, voltage):
        """Set a channel's voltage. MCP4728 fast write to single channel."""
        if not self.bus:
            return
        voltage = max(0.0, min(voltage, self.vref))
        raw = int((voltage / self.vref) * 4095)
        # Multi-write command for single channel
        cmd = 0x40 | (channel << 1)
        high = (raw >> 8) & 0x0F
        low = raw & 0xFF
        try:
            self.bus.write_i2c_block_data(self.address, cmd, [high, low])
        except Exception as e:
            logger.error(f"DAC write error (0x{self.address:02x} ch{channel}): {e}")


class HardwareDaemon:
    """Lightweight TCP/WebSocket server that drives Pi hardware from remote commands.

    Only one client (over either transport) is served at a time.
    """

    def __init__(self, config, port=DEFAULT_PORT, dry_run=False, ws_port=WEBSOCKET_PORT):
        self.config = config
        self.port = port
        self.ws_port = ws_port      # None disables the WebSocket listener
        self.dry_run = dry_run
        self.running = True
        self.start_time = time.monotonic()

        # Hardware state
        self.dacs = {}          # dac_id -> MCP4728
        self.slot_gate = {}     # slot_id -> gate_pin
        self.slot_dac = {}      # slot_id -> (dac_id, channel)
        self.clock_pin = config.get("clock_pin")
        self.run_pin = config.get("run_pin")
        self.loopback_output_pin = config.get("loopback_output_pin")
        self.loopback_input_pin = config.get("loopback_input_pin")

        # Clock state
        self.clock_running = False
        self.current_bpm = 120
        self.pwm_instances = {}
        self.clk_output_pin = None
        self.clk_pulse_count = 0
        self.clk_pulse_high = False
        self.clock_callback_lock = threading.Lock()

        # MIDI - multiple outputs, indexed by device number
        self.midi_outputs = {}      # device_index -> mido output
        self.midi_device_names = [] # ordered list of device names
        self.midi_default_idx = 0   # index of preferred (real HW) device

        # Network
        self.server_sock = None
        self.client_sock = None
        self.ws_server = None
        self.discovery_port = None
        self.active_client = None   # description of the connected client, if any
        self._client_lock = threading.Lock()

        self._init_hardware()

    def _init_hardware(self):
        """Initialize GPIO, DACs, and MIDI."""
        if self.dry_run:
            logger.info("Dry-run mode: skipping hardware init")
            self._init_slot_maps()
            self._init_midi()
            return

        # GPIO
        GPIO.setmode(GPIO.BCM)

        # Slot gate pins
        self._init_slot_maps()
        for slot_id, pin in self.slot_gate.items():
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

        # Clock pin
        if self.clock_pin is not None:
            GPIO.setup(self.clock_pin, GPIO.OUT, initial=GPIO.LOW)

        # Run pin
        if self.run_pin is not None:
            GPIO.setup(self.run_pin, GPIO.OUT, initial=GPIO.LOW)

        # Loopback pins
        if self.loopback_output_pin is not None:
            GPIO.setup(self.loopback_output_pin, GPIO.OUT, initial=GPIO.LOW)
        if self.loopback_input_pin is not None:
            GPIO.setup(self.loopback_input_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(
                self.loopback_input_pin, GPIO.RISING,
                callback=self._clock_pulse_received, bouncetime=1
            )

        # DACs
        for dac_cfg in self.config.get("dacs", []):
            dac = MCP4728(dac_cfg["address"], dac_cfg.get("vref", 3.3))
            if dac.start():
                self.dacs[dac_cfg["id"]] = dac
                logger.info(f"DAC {dac_cfg['id']} (0x{dac_cfg['address']:02x}) started")
            else:
                logger.error(f"Failed to start DAC {dac_cfg['id']}")

        # MIDI
        self._init_midi()

    def _init_slot_maps(self):
        """Build slot -> gate_pin and slot -> dac mappings."""
        for slot_cfg in self.config.get("slots", []):
            sid = slot_cfg["id"]
            pin = slot_cfg.get("gate_pin")
            if pin is not None:
                self.slot_gate[sid] = pin
            dac_ref = slot_cfg.get("dac")
            if dac_ref:
                dac_id, ch = dac_ref.split(":")
                self.slot_dac[sid] = (int(dac_id), int(ch))

    def _init_midi(self):
        """Initialize all MIDI outputs, preferring real hardware as default."""
        if mido is None:
            return
        try:
            names = mido.get_output_names()
            if not names:
                return
            # Sort: real devices first, then virtual
            real = [n for n in names if 'Through' not in n]
            virtual = [n for n in names if 'Through' in n]
            ordered = real + virtual
            self.midi_device_names = ordered
            self.midi_default_idx = 0

            for idx, name in enumerate(ordered):
                try:
                    self.midi_outputs[idx] = mido.open_output(name)
                    logger.info(f"MIDI output [{idx}]: {name}{' (default)' if idx == 0 else ''}")
                except Exception as e:
                    logger.warning(f"Failed to open MIDI output '{name}': {e}")
        except Exception as e:
            logger.warning(f"MIDI init failed: {e}")

    def _get_midi_output(self, device_idx=0):
        """Get MIDI output by device index. 0 = default."""
        if device_idx == 0 or device_idx not in self.midi_outputs:
            return self.midi_outputs.get(self.midi_default_idx)
        return self.midi_outputs.get(device_idx)

    # --- Hardware operations ---

    def _cv_note_on(self, slot, note, velocity):
        """Set DAC voltage + gate HIGH."""
        if self.dry_run:
            logger.debug(f"CV ON: slot={slot} note={note} vel={velocity}")
            return

        # Set CV voltage
        dac_ref = self.slot_dac.get(slot)
        if dac_ref:
            dac_id, channel = dac_ref
            dac = self.dacs.get(dac_id)
            if dac:
                if note == -1:
                    voltage = 0
                else:
                    voltage = (note - 24) / 12.0
                    voltage = max(0, min(voltage, dac.vref))
                dac.set_voltage(channel, voltage)

        # Gate HIGH
        pin = self.slot_gate.get(slot)
        if pin is not None:
            GPIO.output(pin, GPIO.HIGH)

    def _cv_gate_off(self, slot, note):
        """Gate LOW, CV voltage persists."""
        if self.dry_run:
            logger.debug(f"CV OFF: slot={slot}")
            return

        pin = self.slot_gate.get(slot)
        if pin is not None:
            GPIO.output(pin, GPIO.LOW)

    def _midi_note_on(self, channel, note, velocity, device_idx=0):
        out = self._get_midi_output(device_idx)
        if out:
            msg = mido.Message('note_on', channel=channel, note=note, velocity=velocity)
            out.send(msg)
        elif self.dry_run:
            logger.debug(f"MIDI ON: ch={channel} note={note} vel={velocity}")

    def _midi_note_off(self, channel, note, device_idx=0):
        out = self._get_midi_output(device_idx)
        if out:
            msg = mido.Message('note_off', channel=channel, note=note, velocity=0)
            out.send(msg)
        elif self.dry_run:
            logger.debug(f"MIDI OFF: ch={channel} note={note}")

    def _midi_cc(self, channel, cc, value, device_idx=0):
        out = self._get_midi_output(device_idx)
        if out:
            msg = mido.Message('control_change', channel=channel, control=cc, value=value)
            out.send(msg)
        elif self.dry_run:
            logger.debug(f"MIDI CC: ch={channel} cc={cc} val={value}")

    def _all_notes_off(self):
        """Safety: all gates LOW, all MIDI notes off."""
        logger.info("All notes off")
        if not self.dry_run:
            for pin in self.slot_gate.values():
                try:
                    GPIO.output(pin, GPIO.LOW)
                except Exception:
                    pass
        for out in self.midi_outputs.values():
            for ch in range(16):
                try:
                    msg = mido.Message('control_change', channel=ch, control=123, value=0)
                    out.send(msg)
                except Exception:
                    pass

    def _set_run_state(self, running):
        if self.dry_run:
            logger.debug(f"RUN: {running}")
            return
        if self.run_pin is not None:
            GPIO.output(self.run_pin, GPIO.HIGH if running else GPIO.LOW)

    # --- Clock ---

    def _clock_pulse_received(self, channel):
        """Handle loopback clock pulse - update external clock output."""
        self._update_clk_output()

    def _update_clk_output(self):
        """Drive CLK output pin based on microtick counting."""
        if self.dry_run or not self.clk_output_pin:
            return
        ticks_per_step = self.config.get("ticks_per_step", 10)
        pulse_position = self.clk_pulse_count % ticks_per_step
        if pulse_position == 0:
            GPIO.output(self.clk_output_pin, GPIO.HIGH)
            self.clk_pulse_high = True
        elif pulse_position == 2:
            GPIO.output(self.clk_output_pin, GPIO.LOW)
            self.clk_pulse_high = False
        self.clk_pulse_count += 1

    def _start_clock(self, bpm):
        """Start hardware clock PWM."""
        self.current_bpm = bpm
        if self.dry_run:
            self.clock_running = True
            logger.info(f"Clock started (dry-run): {bpm} BPM")
            return

        if self.clock_running:
            self._stop_clock()

        self.clk_output_pin = self.clock_pin
        if self.clk_output_pin is not None:
            GPIO.output(self.clk_output_pin, GPIO.LOW)
        self.clk_pulse_count = 0
        self.clk_pulse_high = False

        # Start loopback PWM
        if self.loopback_output_pin is not None:
            ppqn = self.config.get("ppqn", 4)
            ticks_per_step = self.config.get("ticks_per_step", 10)
            external_freq = (bpm / 60.0) * ppqn
            internal_freq = external_freq * ticks_per_step
            internal_period = 1.0 / internal_freq
            pulse_width = 0.010
            duty = max(1.0, min(99.0, (pulse_width / internal_period) * 100.0))

            pwm = GPIO.PWM(self.loopback_output_pin, internal_freq)
            pwm.start(duty)
            self.pwm_instances[self.loopback_output_pin] = pwm

        self.clock_running = True
        logger.info(f"Clock started: {bpm} BPM")

    def _stop_clock(self):
        """Stop hardware clock PWM."""
        if self.clk_output_pin is not None and not self.dry_run:
            try:
                GPIO.output(self.clk_output_pin, GPIO.LOW)
            except Exception:
                pass
            self.clk_output_pin = None

        for pin, pwm in self.pwm_instances.items():
            try:
                pwm.stop()
            except Exception:
                pass
        self.pwm_instances.clear()
        self.clock_running = False
        self.clk_pulse_high = False
        logger.info("Clock stopped")

    def _set_clock_bpm(self, bpm):
        """Update clock BPM (restart if running)."""
        was_running = self.clock_running
        if was_running:
            self._stop_clock()
        self.current_bpm = bpm
        if was_running:
            self._start_clock(bpm)

    def _build_device_list_response(self):
        """Build variable-length response: 4-byte length + JSON payload."""
        devices = []
        for idx, name in enumerate(self.midi_device_names):
            devices.append({"index": idx, "name": name, "default": idx == self.midi_default_idx})
        payload = json.dumps({"midi_devices": devices, "cv_slots": len(self.slot_gate)}).encode()
        return struct.pack('>I', len(payload)) + payload

    # --- Command dispatch ---

    def _handle_command(self, cmd, slot, val1, val2, flags):
        """Dispatch a command to the appropriate handler. Returns response or None."""
        if cmd == CMD_CV_NOTE_ON:
            self._cv_note_on(slot, val1, val2)
        elif cmd == CMD_CV_GATE_OFF:
            self._cv_gate_off(slot, val1)
        elif cmd == CMD_MIDI_NOTE_ON:
            self._midi_note_on(slot, val1, val2, flags)
        elif cmd == CMD_MIDI_NOTE_OFF:
            self._midi_note_off(slot, val1, flags)
        elif cmd == CMD_MIDI_CC:
            self._midi_cc(slot, val1, val2, flags)
        elif cmd == CMD_CLOCK_START:
            self._start_clock(val1)
            return pack_msg(RESP_ACK, val1=cmd)
        elif cmd == CMD_CLOCK_STOP:
            self._stop_clock()
            return pack_msg(RESP_ACK, val1=cmd)
        elif cmd == CMD_CLOCK_SET_BPM:
            self._set_clock_bpm(val1)
            return pack_msg(RESP_ACK, val1=cmd)
        elif cmd == CMD_RUN_STATE:
            self._set_run_state(val1 == 1)
            return pack_msg(RESP_ACK, val1=cmd)
        elif cmd == CMD_ALL_NOTES_OFF:
            self._all_notes_off()
        elif cmd == CMD_QUERY_DEVICES:
            return self._build_device_list_response()
        elif cmd == CMD_PING:
            uptime = int(time.monotonic() - self.start_time) & 0xFFFF
            return pack_msg(RESP_PONG, val1=uptime)
        elif cmd == CMD_SHUTDOWN:
            logger.info("Shutdown command received")
            self.running = False
        else:
            logger.warning(f"Unknown command: 0x{cmd:02x}")
            return pack_msg(RESP_ERROR, val1=0, val2=cmd)
        return None

    # --- UDP Discovery ---

    def _run_discovery_listener(self):
        """Background thread: respond to UDP broadcast discovery requests."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
        sock.settimeout(1.0)
        self.discovery_port = sock.getsockname()[1]

        hostname = socket.gethostname()
        logger.info(f"Discovery listener on UDP port {self.discovery_port}")

        while self.running:
            try:
                data, addr = sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break

            if data == DISCOVERY_MAGIC:
                uptime = int(time.monotonic() - self.start_time)
                payload = json.dumps({
                    "port": self.port,
                    "ws_port": self.ws_port if self.ws_server else None,
                    "hostname": hostname,
                    "uptime": uptime,
                    "busy": self.active_client is not None,
                }).encode()
                response = DISCOVERY_RESPONSE_MAGIC + payload
                try:
                    sock.sendto(response, addr)
                    logger.debug(f"Discovery response sent to {addr}")
                except OSError:
                    pass

        sock.close()

    # --- Client ownership (one client across all transports) ---

    def _claim_client(self, description):
        """Mark a client as connected. Returns False if another client is active."""
        with self._client_lock:
            if self.active_client is not None:
                logger.warning(f"Rejecting {description}: {self.active_client} is connected")
                return False
            self.active_client = description
        logger.info(f"Client connected: {description}")
        return True

    def _release_client(self):
        """Silence outputs and free the client slot after a disconnect."""
        logger.info(f"Client disconnected: {self.active_client}")
        self._all_notes_off()
        self._stop_clock()
        self._set_run_state(False)
        with self._client_lock:
            self.active_client = None

    def _process_messages(self, buf, send):
        """Handle all complete messages in buf, sending responses via send().

        Returns the unconsumed remainder of buf.
        """
        while len(buf) >= MSG_SIZE:
            msg = buf[:MSG_SIZE]
            buf = buf[MSG_SIZE:]

            cmd, slot, val1, val2, flags = unpack_msg(msg)
            response = self._handle_command(cmd, slot, val1, val2, flags)
            if response:
                send(response)
        return buf

    # --- WebSocket listener (browsers) ---

    def _run_websocket_server(self):
        """Background thread: accept WebSocket clients speaking the same binary protocol."""
        try:
            from websockets.sync.server import serve
        except ImportError:
            logger.warning("websockets not installed - WebSocket listener disabled")
            return

        # compression=None keeps per-message latency down
        with serve(self._handle_ws_client, "0.0.0.0", self.ws_port,
                   compression=None) as server:
            self.ws_port = server.socket.getsockname()[1]
            self.ws_server = server
            logger.info(f"WebSocket listening on 0.0.0.0:{self.ws_port}")
            server.serve_forever()

    def _handle_ws_client(self, ws):
        """Serve one WebSocket connection. Each binary frame holds one or more messages."""
        from websockets.exceptions import ConnectionClosed

        description = f"websocket {ws.remote_address}"
        if not self._claim_client(description):
            ws.close(1013, "busy: another client is connected")
            return
        try:
            ws.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        try:
            buf = b""
            while self.running:
                try:
                    data = ws.recv(timeout=0.5)
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    break
                if isinstance(data, str):
                    logger.warning("Ignoring text frame: protocol messages must be binary")
                    continue
                try:
                    buf = self._process_messages(buf + data, ws.send)
                except ConnectionClosed:
                    break
        finally:
            self._release_client()

    # --- Main server loop ---

    def run(self):
        """Main loop: listen for connections and process commands."""
        # Start discovery listener
        self._discovery_thread = threading.Thread(
            target=self._run_discovery_listener, daemon=True)
        self._discovery_thread.start()

        if self.ws_port is not None:
            self._ws_thread = threading.Thread(
                target=self._run_websocket_server, daemon=True)
            self._ws_thread.start()

        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(1)
        self.server_sock.setblocking(False)
        self.port = self.server_sock.getsockname()[1]

        logger.info(f"Listening on 0.0.0.0:{self.port}")

        while self.running:
            # Wait for a connection
            try:
                readable, _, _ = select.select([self.server_sock], [], [], 0.5)
            except (select.error, OSError):
                break

            if not readable:
                continue

            accepted = self._accept_tcp()
            if accepted is None:
                continue
            client, addr = accepted

            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.setblocking(False)
            self.client_sock = client

            self._handle_client()

            # Client disconnected
            self._release_client()
            try:
                self.client_sock.close()
            except OSError:
                pass
            self.client_sock = None

        self._cleanup()

    def _accept_tcp(self):
        """Accept a pending TCP connection.

        Returns (sock, addr) if it became the active client, or None if it was
        rejected because another client is connected.
        """
        try:
            client, addr = self.server_sock.accept()
        except OSError:
            return None
        if not self._claim_client(f"tcp {addr}"):
            try:
                client.close()
            except OSError:
                pass
            return None
        return client, addr

    def _handle_client(self):
        """Process commands from the connected client until disconnect."""
        buf = b""

        while self.running:
            try:
                # Also watch the listener so extra clients are turned away
                # immediately instead of waiting in the backlog
                readable, _, _ = select.select(
                    [self.client_sock, self.server_sock], [], [], 0.5)
            except (select.error, ValueError, OSError):
                break

            if self.server_sock in readable:
                self._accept_tcp()  # always rejected: we already have a client
            if self.client_sock not in readable:
                continue

            try:
                data = self.client_sock.recv(4096)
            except (BlockingIOError, InterruptedError):
                continue
            except (ConnectionResetError, BrokenPipeError, OSError):
                break

            if not data:
                break  # Client disconnected

            try:
                buf = self._process_messages(buf + data, self.client_sock.sendall)
            except OSError:
                return

    def _cleanup(self):
        """Clean shutdown."""
        logger.info("Cleaning up...")

        # Stop WebSocket listener and let an active WS client finish releasing
        if self.ws_server:
            self.ws_server.shutdown()
            deadline = time.monotonic() + 2.0
            while self.active_client is not None and time.monotonic() < deadline:
                time.sleep(0.05)

        self._all_notes_off()
        self._stop_clock()

        # Close sockets
        if self.client_sock:
            try:
                self.client_sock.close()
            except OSError:
                pass
        if self.server_sock:
            try:
                self.server_sock.close()
            except OSError:
                pass

        # Stop DACs
        for dac in self.dacs.values():
            dac.stop()

        # Close MIDI
        for out in self.midi_outputs.values():
            try:
                out.close()
            except Exception:
                pass

        # GPIO cleanup
        if not self.dry_run and GPIO:
            try:
                GPIO.cleanup()
            except Exception:
                pass

        logger.info("Cleanup complete")


def main():
    parser = argparse.ArgumentParser(description="AI MIDI Remote Hardware Daemon")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"TCP port (default: {DEFAULT_PORT})")
    parser.add_argument("--ws-port", type=int, default=WEBSOCKET_PORT,
                        help=f"WebSocket port for browser clients (default: {WEBSOCKET_PORT})")
    parser.add_argument("--no-websocket", action="store_true",
                        help="Disable the WebSocket listener")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to JSON hardware config file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log commands without driving hardware")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING"],
                        default="INFO", help="Log level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Load config
    if args.config:
        with open(args.config) as f:
            config = json.load(f)
    else:
        config = DEFAULT_CONFIG

    # Initialize hardware imports
    _init_hw_imports(args.dry_run)

    ws_port = None if args.no_websocket else args.ws_port
    daemon = HardwareDaemon(config, port=args.port, dry_run=args.dry_run, ws_port=ws_port)

    # Signal handlers for clean shutdown
    def shutdown(sig, frame):
        logger.info(f"Signal {sig} received, shutting down...")
        daemon.running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    daemon.run()


if __name__ == "__main__":
    main()
