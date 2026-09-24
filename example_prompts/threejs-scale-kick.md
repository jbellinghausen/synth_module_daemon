# Prompt: three.js scale visualizer with lead + kick

Paste everything below the line into a new Claude Code session, started in an
empty folder where the app should live. Edit the **Setup** values first if
your Pi's address or patching differs.

---

Build a small browser app with three.js that plays a looping melody on a
hardware synth and a four-on-the-floor kick on a hardware kick drum, and shows
it as an animated scale visualization. The hardware is driven by a Raspberry
Pi "synth module" that I control over the network.

## Setup

- Pi address: `raspberrypi.local` (fallback IP `192.168.1.234`), WebSocket port `9743`
- Lead synth: CV/gate **slot 0** (pitch CV → synth V/oct, gate → envelope)
- Kick drum: CV/gate **slot 1** (gate → kick trigger; pitch CV → kick tune input, if it has one)

## The synth module

- Repo: https://github.com/jbellinghausen/synth_module_daemon (public; use tag `v0.1.0`)
- **Read the web apps guide first**, it's the main reference for this task:
  https://raw.githubusercontent.com/jbellinghausen/synth_module_daemon/v0.1.0/clients/js/README.md
  Protocol details, if you need them:
  https://raw.githubusercontent.com/jbellinghausen/synth_module_daemon/v0.1.0/PROTOCOL.md
- Install the client: `npm install github:jbellinghausen/synth_module_daemon#v0.1.0`,
  then `import { SynthModuleClient } from 'synth-module-client'`. It's a single
  dependency-free ES module with TypeScript types.

Key facts (the guide explains them):

- `await synth.connect()`, then fire-and-forget `cvNoteOn(slot, note, velocity)`
  and `cvGateOff(slot)`. Send methods throw when disconnected, so guard with
  `synth.isConnected`.
- Pitch CV is 1 V/octave with **MIDI note 24 = 0 V**. The usable range is
  **notes 24-63**; anything outside clamps.
- Calling `cvNoteOn` on a slot whose gate is already high changes the pitch
  without re-triggering. For separate notes, the gate must go low between them.
- **Only one client at a time.** A second connection is rejected with
  "Daemon is busy with another client". Open exactly one connection for the
  app's lifetime; beware of React StrictMode or hot-reload making a second one.
- The client doesn't auto-reconnect. Use the guide's `keepConnected` pattern.
- When the page closes or disconnects, the daemon silences all outputs.
- The app must be served over **http://** (the Vite dev server is fine).
  Browsers block `ws://` from https pages.

## What to build

A Vite + three.js app (vanilla JS or TypeScript, no framework needed).

**Sequencer**

- 16 steps of 16th notes at a BPM adjustable from 60 to 180 (default 110).
- **Kick** on steps 0, 4, 8 and 12: `cvNoteOn(1, KICK_NOTE)` followed by
  `cvGateOff(1)` about 15 ms later (a trigger, not a held gate). Make
  `KICK_NOTE` a constant (default 36).
- **Lead**: a melody generated from the selected scale. Walk up and down the
  scale with occasional skips and rests, on 8th notes or a mix of 8ths and
  16ths. The gate length is about 60% of the step. Keep every note within
  36-60 so it stays well inside the 24-63 range. Regenerate the pattern with
  a button, and keep it the same until then.
- Scales: major, natural minor, minor pentatonic, dorian. Root selectable
  (C to B). The melody is built from root + scale, octave 3-4 (notes 48-60
  for the root octave).
- Timing: schedule steps from `performance.now()` against ideal step times
  (don't chain fixed `setTimeout`s), as in the guide's timing tips. Handle a
  replayed slot without an old gate-off cutting a new note short (the
  guide's `playCv` helper). Stop playback when the tab is hidden
  (`visibilitychange`), since background timers are throttled.

**Visualization (three.js)**

- The current scale's notes arranged in a ring (or spiral by octave). Each
  note is a mesh labeled with its note name.
- When the lead plays a note, that mesh lights up and pulses, then fades.
- Each kick makes a pulse through the whole scene: a camera bump, a flash
  of the background, or a ring expanding from the center.
- Show a step indicator for the 16 steps, highlighting the current step and
  marking kick steps.
- Keep it simple and clean. Performance matters more than eye candy.

**UI overlay (plain HTML/CSS on top of the canvas)**

- Pi address field (default from Setup), saved in `localStorage`, with a
  connect/disconnect button. Show the connection status, including the
  "busy" message verbatim if it appears.
- Play/stop (spacebar too), BPM slider, root and scale selects, and a
  "new melody" button.
- A **Panic** button (and the Escape key) that calls `synth.allNotesOff()`.
- Show ping RTT every few seconds while connected (`synth.ping()`).
- The app must **work without the hardware**: if not connected, everything
  runs visually and simply skips sending. No uncaught errors when the Pi is
  unreachable.

## Testing without the Pi

You can run the daemon locally in dry-run mode. It speaks the real protocol
and logs every command, but drives no hardware:

```bash
git clone --branch v0.1.0 https://github.com/jbellinghausen/synth_module_daemon.git /tmp/synth_module_daemon
cd /tmp/synth_module_daemon
python3 -m venv venv && venv/bin/pip install websockets
venv/bin/python daemon/hw_daemon.py --dry-run --log-level DEBUG
```

Then point the app at `localhost`. The daemon log should show
`CV ON: slot=1 note=36` / `CV OFF: slot=1` four times per bar for the kick,
and `CV ON: slot=0 ...` for lead notes. Stop the daemon when done. Don't
leave it holding the port, and only one app can be connected at a time.

If you can open a browser preview, use it to check the scene renders, the
controls work, and the status shows "connected". Also check the app still
runs cleanly with the daemon stopped.

## Deliverables

- The app in this folder, runnable with `npm install && npm run dev`.
- A short README covering how to run it, how to set the Pi address, the slot
  assignments, and where the constants live (slots, `KICK_NOTE`, gate
  lengths).
- Put tunables (slots, kick note, trigger length, gate ratio, note range) in
  one config module at the top of the source.
