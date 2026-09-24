# synth_module_daemon

A Raspberry Pi daemon that drives CV/gate (GPIO + MCP4728 DACs), clock/run
outputs and USB MIDI, plus client libraries for talking to it from Python and
from the browser. Split out of the
[ai_midi](https://github.com/jbellinghausen/ai_midi) sequencer.

```
synth_module_daemon/
  PROTOCOL.md          wire protocol (the contract between daemon and clients)
  daemon/              the Pi service: hw_daemon.py, install.sh, systemd unit
  clients/python/      Python client (pip package: synth-module-client)
  clients/js/          JS/browser client (npm package: synth-module-client)
  tests/               Python + JS tests, run against a dry-run daemon
```

## Using the clients

### Python

```bash
pip install "git+https://github.com/jbellinghausen/synth_module_daemon.git#subdirectory=clients/python"
```

```python
from synth_module_client import SynthModuleClient, discover

print(discover())  # find daemons on the LAN

with SynthModuleClient("raspberrypi.local") as synth:
    synth.cv_note_on(slot=0, note=48, velocity=100)
    synth.cv_gate_off(slot=0)
    synth.midi_note_on(channel=0, note=60)
    synth.clock_start(120)
```

See [clients/python/README.md](clients/python/README.md).

### JavaScript / browser (three.js apps etc.)

```bash
npm install github:jbellinghausen/synth_module_daemon
```

```js
import { SynthModuleClient } from 'synth-module-client';

const synth = await new SynthModuleClient('raspberrypi.local').connect();
synth.cvNoteOn(0, 48, 100);
setTimeout(() => synth.cvGateOff(0), 250);
await synth.clockStart(120);
```

No bundler? It's a single dependency-free ES module, so you can also copy
`clients/js/synth-module-client.js` into your project and import it directly.

Browsers connect over plain `ws://` on port 9743, so the page must be served
over `http://` (or from localhost): browsers block `ws://` from `https://`
pages. Browsers can't do UDP discovery, so pass the Pi's hostname or IP.

## Running the daemon (on the Pi)

```bash
daemon/install.sh
```

This creates `venv/`, installs `daemon/requirements.txt` and the client
package (for the shared protocol module), and installs + enables the
`hw-daemon` systemd service so it starts at boot. The service runs as the
installing user, who needs to be in the `gpio`, `i2c` and `audio` groups.

Pass extra daemon arguments via `HW_DAEMON_ARGS`:

```bash
HW_DAEMON_ARGS="--config hw_config.json --log-level DEBUG" daemon/install.sh
```

Uninstall with `daemon/install.sh --uninstall`.

### Managing the service

```bash
systemctl status hw-daemon
journalctl -u hw-daemon -f
sudo systemctl restart hw-daemon
```

### Running manually

```bash
./venv/bin/python daemon/hw_daemon.py              # real hardware
./venv/bin/python daemon/hw_daemon.py --dry-run    # log commands, no GPIO/DAC
./venv/bin/python daemon/hw_daemon.py --no-websocket
./venv/bin/python daemon/hw_daemon.py --config hw_config.json
```

Stop the service first (`sudo systemctl stop hw-daemon`) when running manually
against real hardware: GPIO pins and ports can only be held by one process.

### Hardware config

Without `--config`, the built-in `DEFAULT_CONFIG` in `daemon/hw_daemon.py` is
used (12 CV/gate slots across three MCP4728 DACs at 0x60–0x62, clock on GPIO
18, run on GPIO 15). Supply a JSON file with the same shape to override it.

### Behavior notes

- One client at a time, across TCP and WebSocket together. Others are refused.
- On disconnect the daemon sends all-notes-off, stops the clock and drops the
  run signal.
- MIDI outputs are enumerated at startup. If you plug in a MIDI device later,
  restart the service.

## Protocol

See [PROTOCOL.md](PROTOCOL.md). When changing it, update
`clients/python/synth_module_client/protocol.py` (used by the daemon and the
Python client) and `clients/js/synth-module-client.js`, and bump the version
in `clients/python/pyproject.toml`, `clients/python/synth_module_client/__init__.py`
and `package.json`.

## Tests

```bash
./venv/bin/python -m unittest discover -s tests   # daemon, Python client, protocol sync
npm install && npm test                            # JS client (Node 18+)
```

Tests run the daemon in dry-run mode on ephemeral ports, so they don't need
hardware and don't clash with a running service.
