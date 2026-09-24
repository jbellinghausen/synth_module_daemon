# synth_module_daemon

Standalone hardware daemon for the [ai_midi](https://github.com/jbellinghausen/ai_midi) sequencer. Runs on a
Raspberry Pi and drives CV/gate (GPIO + MCP4728 DACs), clock/run outputs and
USB MIDI in response to commands sent over TCP by a remote sequencer.

Split out of the ai_midi repo so the Pi only needs this small, dependency-light
service instead of the whole application.

## Install (on the Pi)

```bash
./install.sh
```

This creates `venv/`, installs `requirements.txt`, and installs + enables the
`hw-daemon` systemd service so it starts at boot. The service runs as the
installing user, who needs to be in the `gpio`, `i2c` and `audio` groups.

Pass extra daemon arguments via `HW_DAEMON_ARGS`:

```bash
HW_DAEMON_ARGS="--config hw_config.json --log-level DEBUG" ./install.sh
```

Uninstall with `./install.sh --uninstall`.

## Managing the service

```bash
systemctl status hw-daemon
journalctl -u hw-daemon -f
sudo systemctl restart hw-daemon
```

## Running manually

```bash
./venv/bin/python hw_daemon.py              # real hardware
./venv/bin/python hw_daemon.py --dry-run    # log commands, no GPIO/DAC
./venv/bin/python hw_daemon.py --config hw_config.json
```

Stop the service first (`sudo systemctl stop hw-daemon`) when running manually
against real hardware: GPIO pins and ports can only be held by one process.

## Network

| Port | Proto | Purpose |
|------|-------|---------|
| 9741 | TCP | Command stream (8-byte binary messages, one client at a time) |
| 9742 | UDP | Discovery: replies to `AI_MIDI_DISCOVER` broadcasts |

From the ai_midi side, connect with `python server.py --remote-hw raspberrypi.local`
or via the UI's remote hardware discovery.

## Protocol

`remote_protocol.py` defines the wire format and **must match the copy in the
ai_midi repo**. If you change one, change the other.

## Hardware config

Without `--config`, the built-in `DEFAULT_CONFIG` in `hw_daemon.py` is used
(12 CV/gate slots across three MCP4728 DACs at 0x60–0x62, clock on GPIO 18,
run on GPIO 15). Supply a JSON file with the same shape to override it.

## Notes

- MIDI outputs are enumerated at startup. If you plug in a MIDI device later,
  restart the service.
- On disconnect the daemon sends all-notes-off, stops the clock and drops the
  run signal.

## Tests

```bash
./venv/bin/python -m unittest discover
```

Tests run the daemon in dry-run mode on an ephemeral port; no hardware needed.
