# Web apps guide

How to connect a browser app (plain JS, three.js, React, …) to the synth
module on the Raspberry Pi and control its CV/gate outputs, clock and MIDI.

The client is a single dependency-free ES module,
[`synth-module-client.js`](synth-module-client.js), with TypeScript types. It
talks to the daemon over a WebSocket on port **9743**.

- [Quick start](#quick-start)
- [Adding the client to a project](#adding-the-client-to-a-project)
- [Serving your app](#serving-your-app)
- [Connecting](#connecting)
- [Controlling the module](#controlling-the-module)
- [Timing tips](#timing-tips)
- [Examples: three.js and React](#example-threejs)
- [Troubleshooting](#troubleshooting)
- [API reference](#api-reference)

## Quick start

Copy `synth-module-client.js` next to this `index.html`, serve the folder
over HTTP (for example `python3 -m http.server 8080`), and open
`http://localhost:8080`:

```html
<!doctype html>
<button id="play" disabled>Play C</button>
<span id="status">connecting…</span>

<script type="module">
  import { SynthModuleClient } from './synth-module-client.js';

  const synth = new SynthModuleClient('raspberrypi.local'); // or '192.168.1.234'
  const status = document.getElementById('status');
  const button = document.getElementById('play');

  try {
    await synth.connect();
    status.textContent = 'connected';
    button.disabled = false;
  } catch (err) {
    status.textContent = err.message;
  }

  button.onclick = () => {
    synth.cvNoteOn(0, 48, 100);                  // slot 0: pitch C3, gate high
    setTimeout(() => synth.cvGateOff(0), 250);   // gate low after 250 ms
  };
</script>
```

## Adding the client to a project

**npm (Vite, webpack, etc.)**

```bash
npm install github:jbellinghausen/synth_module_daemon
# pin a version:
npm install github:jbellinghausen/synth_module_daemon#v0.1.0
```

```js
import { SynthModuleClient } from 'synth-module-client';
```

TypeScript types are included; nothing else to install.

**Copy the file.** For build-free pages, copy
`clients/js/synth-module-client.js` into your project and import it with a
relative path, as in the quick start.

**CDN.** If the GitHub repo is public, jsDelivr serves the file straight
from it:

```js
import { SynthModuleClient } from
  'https://cdn.jsdelivr.net/gh/jbellinghausen/synth_module_daemon@v0.1.0/clients/js/synth-module-client.js';
```

## Serving your app

The daemon speaks plain `ws://`. Browsers block `ws://` connections from
pages loaded over `https://`, so your app must be loaded over **`http://`** or
from **localhost**.

- ✅ `http://localhost:5173` (Vite dev server), `http://localhost:8080`
- ✅ `http://my-laptop.local:5173`: another machine on the LAN (for Vite,
  run `npx vite --host` so it listens on the network)
- ❌ `https://…`, including GitHub Pages, Netlify and Vercel

Recent Chrome versions may ask the user for permission when a page connects
to a device on the local network. Allow it if prompted.

### Finding the Pi

Browsers can't do the UDP discovery the Python client uses, so pass the Pi's
address:

- `raspberrypi.local` works on macOS, iOS, most Linux desktops and Windows
  10+. Android support is unreliable.
- The IP address (currently `192.168.1.234`) always works. Run
  `hostname -I` on the Pi to find it.

Letting users type the address (and saving it in `localStorage`) saves
editing code when the Pi's address changes.

## Connecting

```js
import { SynthModuleClient } from 'synth-module-client';

const synth = new SynthModuleClient('raspberrypi.local');
await synth.connect();   // resolves once the daemon answers a ping
```

Options: `new SynthModuleClient(host, { port: 9743, timeout: 2000 })`. `host`
can also be a full URL like `'ws://192.168.1.234:9743'`.

`connect()` rejects with a `SynthModuleError` if:

- the Pi is unreachable or the daemon isn't running;
- **another client is already connected**. The daemon serves one client at
  a time (across browsers, tabs and native apps), and the error message is
  `Daemon is busy with another client`.

### Staying connected

The client doesn't reconnect on its own. `onclose` fires when an established
connection drops unexpectedly (Pi rebooted, Wi-Fi blip); it does not fire for
your own `close()` or for a failed `connect()`. This helper keeps retrying:

```js
function keepConnected(host, { retryMs = 2000, onStatus = () => {}, ...options } = {}) {
  const synth = new SynthModuleClient(host, options);
  let timer = null;
  let stopped = false;
  const retry = () => { if (!stopped) timer = setTimeout(attempt, retryMs); };

  async function attempt() {
    onStatus('connecting');
    try {
      await synth.connect();
      onStatus('connected');
    } catch (err) {
      onStatus(err.message);   // e.g. "Daemon is busy with another client"
      retry();
    }
  }

  synth.onclose = () => { onStatus('disconnected'); retry(); };
  attempt();
  return { synth, stop() { stopped = true; clearTimeout(timer); synth.close(); } };
}

const { synth } = keepConnected('raspberrypi.local', {
  onStatus: (s) => { document.getElementById('status').textContent = s; },
});
```

Send methods throw when disconnected, so guard calls that can happen at any
time (animation loops, UI events) with `if (synth.isConnected)`.

### Disconnecting

`synth.close()`. When the connection closes for any reason (including the
tab being closed or reloaded), the daemon lowers every gate, sends MIDI
all-notes-off, stops the clock and drops the run output. You don't need a
`beforeunload` handler to avoid stuck notes.

## Controlling the module

Call `synth.queryDevices()` to see what's available:

```js
const info = await synth.queryDevices();
// { cv_slots: 12,
//   midi_devices: [ { index: 0, name: 'USB MIDI Interface:…', default: true },
//                   { index: 1, name: 'Midi Through:…', default: false } ] }
```

### CV / gate

The module has 12 CV/gate slots, numbered **0-11**. Each slot has a pitch CV
output (1 V/octave) and a gate output.

```js
synth.cvNoteOn(slot, note, velocity = 127);   // set pitch, gate high
synth.cvGateOff(slot);                        // gate low (pitch holds)
```

- `note` is a MIDI note number. Note **24 (C1) = 0 V**, and each octave adds
  1 V. The DAC tops out at 3.3 V, so the usable range is notes **24-63**;
  lower notes clamp to 0 V and higher ones to 3.3 V.
- `velocity` is accepted but not currently output as a voltage.
- Calling `cvNoteOn` on a slot whose gate is already high changes the pitch
  without re-triggering the gate (legato). To re-trigger, send `cvGateOff`
  first and leave a few milliseconds before the next note-on.

A helper for fixed-length notes. It cancels the pending gate-off when a slot
is replayed, so a new note isn't cut short by an older one:

```js
const gateTimers = new Map();

function playCv(slot, note, lengthMs = 200) {
  if (!synth.isConnected) return;
  clearTimeout(gateTimers.get(slot));
  synth.cvNoteOn(slot, note);
  gateTimers.set(slot, setTimeout(() => {
    gateTimers.delete(slot);
    if (synth.isConnected) synth.cvGateOff(slot);
  }, lengthMs));
}
```

### MIDI

MIDI goes out of the Pi's USB MIDI interface.

```js
synth.midiNoteOn(channel, note, velocity = 100, device = 0);
synth.midiNoteOff(channel, note, device = 0);
synth.midiCC(channel, controller, value, device = 0);
```

- **Channels are 0-based**: `0` is MIDI channel 1 and `15` is channel 16.
- `note`, `velocity` and CC `value` are 0-127.
- `device` picks an output by `index` from `queryDevices()`. `0` is the
  default output (the USB interface).

### Clock and run

These return Promises that resolve when the daemon acknowledges them:

```js
await synth.clockStart(120);     // start the clock output at 120 BPM
await synth.setBpm(140);         // change tempo (restarts a running clock)
await synth.setRunState(true);   // run output high
await synth.setRunState(false);
await synth.clockStop();
```

### Safety

```js
synth.allNotesOff();   // lower all gates + MIDI all-notes-off on every channel
```

Bind it to a panic key; it's the quickest fix for stuck notes.

### Health check

```js
const { rttMs, uptimeS } = await synth.ping();
```

On a LAN, expect `rttMs` around 1 ms.

## Timing tips

- **Notes, gates and CC are fire-and-forget.** They're sent immediately and
  don't wait for a reply, so you can call them straight from event handlers
  or `requestAnimationFrame`.
- **Background tabs are throttled.** Browsers slow `setTimeout` down to about
  once per second in hidden tabs, so sequencers stutter and gate-offs arrive
  late. Keep the tab visible while playing, or pause on
  `document.visibilitychange`.
- **Timers jitter by a few ms.** That's fine for UI-driven playing. For
  steady rhythms, schedule from the ideal times rather than chaining
  `setTimeout` calls, so errors don't accumulate:

  ```js
  const stepMs = 60000 / 120 / 4;          // 16th notes at 120 BPM
  const start = performance.now();
  let step = 0;
  (function tick() {
    playCv(0, 36 + (step % 4) * 3, stepMs * 0.5);
    step += 1;
    setTimeout(tick, start + step * stepMs - performance.now());
  })();
  ```

- **Only send CC when the value changes.** A three.js loop runs at 60-120
  fps, and each CC takes about 1 ms on a MIDI cable. Several CCs per frame
  can clog the MIDI output even though the network keeps up.

## Example: three.js

Clicking a cube plays a note on its slot, and the camera's rotation sweeps a
filter CC:

```js
import * as THREE from 'three';
import { SynthModuleClient } from 'synth-module-client';

const synth = new SynthModuleClient('raspberrypi.local');
synth.connect().catch((err) => console.warn('synth offline:', err.message));

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 100);
camera.position.z = 8;
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);

// One cube per CV slot, laid out in a row, each tuned to a scale degree
const scale = [36, 39, 41, 43, 46, 48];
const cubes = scale.map((note, slot) => {
  const cube = new THREE.Mesh(
    new THREE.BoxGeometry(0.8, 0.8, 0.8),
    new THREE.MeshNormalMaterial(),
  );
  cube.position.x = (slot - (scale.length - 1) / 2) * 1.2;
  cube.userData = { slot, note };
  scene.add(cube);
  return cube;
});

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
renderer.domElement.addEventListener('pointerdown', (event) => {
  pointer.set((event.clientX / innerWidth) * 2 - 1, -(event.clientY / innerHeight) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const [hit] = raycaster.intersectObjects(cubes);
  if (!hit || !synth.isConnected) return;
  const { slot, note } = hit.object.userData;
  synth.cvNoteOn(slot, note);
  hit.object.scale.setScalar(1.3);
  setTimeout(() => {
    if (synth.isConnected) synth.cvGateOff(slot);
    hit.object.scale.setScalar(1);
  }, 200);
});

let lastCutoff = -1;
renderer.setAnimationLoop((t) => {
  camera.position.x = Math.sin(t / 3000) * 3;
  camera.lookAt(0, 0, 0);

  // Map camera sweep to CC 74 (filter cutoff) on MIDI channel 1, only on change
  const cutoff = Math.round(((camera.position.x + 3) / 6) * 127);
  if (cutoff !== lastCutoff && synth.isConnected) {
    synth.midiCC(0, 74, cutoff);
    lastCutoff = cutoff;
  }
  renderer.render(scene, camera);
});
```

## Example: React

A hook that owns one connection per component tree, reusing `keepConnected`
from [Staying connected](#staying-connected):

```jsx
import { useEffect, useState } from 'react';

export function useSynth(host) {
  const [synth, setSynth] = useState(null);
  const [status, setStatus] = useState('connecting');

  useEffect(() => {
    const conn = keepConnected(host, { onStatus: setStatus });
    setSynth(conn.synth);
    return () => conn.stop();
  }, [host]);

  return { synth, status, ready: status === 'connected' };
}

export function App() {
  const { synth, status, ready } = useSynth('raspberrypi.local');
  return (
    <>
      <p>Synth: {status}</p>
      {[36, 39, 43, 46].map((note, slot) => (
        <Pad key={slot} synth={synth} ready={ready} slot={slot} note={note} />
      ))}
    </>
  );
}

function Pad({ synth, ready, slot, note }) {
  return (
    <button
      disabled={!ready}
      onPointerDown={() => synth.cvNoteOn(slot, note)}
      onPointerUp={() => synth.isConnected && synth.cvGateOff(slot)}
    >
      Slot {slot}
    </button>
  );
}
```

Call `useSynth` **once**, near the top of your app, and pass `synth` down (or
put it in context). Each call opens its own connection, and the daemon only
accepts one. In development, React StrictMode runs effects twice. The first
connection is closed straight away, but you may briefly see a "busy" status
before the retry connects.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Daemon is busy with another client` | Something else is connected: another tab, ai_midi, a script. Close it. Only one client at a time. |
| `Cannot connect to ws://…` | Wrong address, Pi off, or daemon not running. On the Pi: `systemctl status hw-daemon`, then `sudo systemctl restart hw-daemon`. |
| Works on localhost, fails when deployed | The page is on `https://`. Serve it over `http://`; see [Serving your app](#serving-your-app). |
| `raspberrypi.local` doesn't resolve | mDNS isn't supported on that device (common on Android). Use the IP address. |
| Connected but no sound from CV | Check the slot number (0-11) and the note range (24-63). Pitch is 1 V/oct from note 24. |
| Connected but no MIDI | Channels are 0-based. Check `queryDevices()` lists your interface. If you plugged it in after boot, restart the daemon. |
| Notes stutter or hang when the tab is hidden | Background tab throttling; see [Timing tips](#timing-tips). |
| Stuck notes | `synth.allNotesOff()`, or just reload the page: disconnecting silences everything. |

Logs on the Pi: `journalctl -u hw-daemon -f`.

## API reference

| Member | Returns | Notes |
|--------|---------|-------|
| `new SynthModuleClient(host, {port, timeout, WebSocket})` | | `host` is a hostname, IP or `ws://` URL |
| `connect()` | `Promise<this>` | Verifies with a ping; rejects if busy/unreachable |
| `close()` | | Daemon silences all outputs |
| `isConnected` | `boolean` | |
| `onclose` | | `(event) => {}`, fires on unexpected drops only |
| `cvNoteOn(slot, note, velocity=127)` | | Fire-and-forget |
| `cvGateOff(slot, note=0)` | | Fire-and-forget |
| `midiNoteOn(channel, note, velocity=100, device=0)` | | Fire-and-forget |
| `midiNoteOff(channel, note, device=0)` | | Fire-and-forget |
| `midiCC(channel, cc, value, device=0)` | | Fire-and-forget |
| `allNotesOff()` | | Fire-and-forget |
| `clockStart(bpm)` / `clockStop()` / `setBpm(bpm)` | `Promise<void>` | Resolves on ACK |
| `setRunState(running)` | `Promise<void>` | Resolves on ACK |
| `ping()` | `Promise<{rttMs, uptimeS}>` | |
| `queryDevices()` | `Promise<{midi_devices, cv_slots}>` | |

Errors are `SynthModuleError` (connection/daemon problems) or `RangeError`
(argument out of range: slot/channel 0-255, values 0-65535). If a reply times
out, the client closes the connection so later replies can't be mismatched;
reconnect to continue.

Lower-level exports: `packMsg`, `unpackMsg`, `CMD`, `RESP`, `MSG_SIZE`,
`DEFAULT_WS_PORT`, `CLOSE_BUSY`. See [PROTOCOL.md](../../PROTOCOL.md).
