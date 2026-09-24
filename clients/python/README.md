# synth-module-client (Python)

Python client for the [synth module daemon](../../README.md). Pure standard
library, Python 3.8+.

```bash
pip install "git+https://github.com/jbellinghausen/synth_module_daemon.git#subdirectory=clients/python"
# pin a version:
pip install "git+https://github.com/jbellinghausen/synth_module_daemon.git@v0.1.0#subdirectory=clients/python"
```

## Example

```python
import time
from synth_module_client import SynthModuleClient, discover

daemons = discover()          # [DaemonInfo(host='192.168.1.234', port=9741, ...)]
host = daemons[0].host if daemons else "raspberrypi.local"

with SynthModuleClient(host) as synth:
    print(synth.ping())            # Pong(rtt_ms=0.4, uptime_s=153)
    print(synth.query_devices())   # {'midi_devices': [...], 'cv_slots': 12}

    synth.cv_note_on(slot=0, note=48, velocity=100)
    time.sleep(0.25)
    synth.cv_gate_off(slot=0)

    synth.midi_note_on(channel=0, note=60, velocity=100)
    time.sleep(0.25)
    synth.midi_note_off(channel=0, note=60)

    synth.clock_start(120)
    synth.set_run_state(True)
```

## API

| Method | Notes |
|--------|-------|
| `connect()` / `close()` / context manager | `connect()` verifies with a ping |
| `cv_note_on(slot, note, velocity=127)` | Pitch CV + gate high |
| `cv_gate_off(slot, note=0)` | Gate low, pitch holds |
| `midi_note_on(channel, note, velocity=100, device=0)` | |
| `midi_note_off(channel, note, device=0)` | |
| `midi_cc(channel, cc, value, device=0)` | |
| `clock_start(bpm)` / `clock_stop()` / `set_bpm(bpm)` | Wait for ACK |
| `set_run_state(running)` | Wait for ACK |
| `all_notes_off()` | |
| `ping()` → `Pong(rtt_ms, uptime_s)` | |
| `query_devices()` → dict | MIDI outputs and CV slot count |
| `discover(timeout=2.0)` → `[DaemonInfo]` | UDP broadcast on the LAN |

Errors:

- `ConnectionError`: not connected, unreachable, busy with another client, or
  a reply timed out. The connection is closed; call `connect()` to retry.
- `SynthModuleError`: the daemon rejected a command or replied unexpectedly.
- `ValueError`: an argument doesn't fit the protocol (slot/channel 0-255,
  values 0-65535).

Methods are thread-safe. The client does not reconnect automatically.
