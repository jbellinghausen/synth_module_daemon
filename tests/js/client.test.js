// Tests for the JS client against a dry-run daemon (spawned via Python).
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';

import WebSocketImpl, { WebSocketServer } from 'ws';

import {
  CMD, RESP, SynthModuleClient, SynthModuleError, packMsg, unpackMsg,
} from '../../clients/js/synth-module-client.js';

const WebSocket = globalThis.WebSocket || WebSocketImpl;
const PYTHON = process.env.PYTHON || fileURLToPath(new URL('../../venv/bin/python', import.meta.url));
const HELPER = fileURLToPath(new URL('./run_dry_daemon.py', import.meta.url));

let daemon;
let wsPort;

before(async () => {
  daemon = spawn(PYTHON, [HELPER], { stdio: ['pipe', 'pipe', 'inherit'] });
  const lines = createInterface({ input: daemon.stdout });
  const [line] = await once(lines, 'line');
  wsPort = JSON.parse(line).ws_port;
});

after(async () => {
  daemon.stdin.end();
  await once(daemon, 'exit');
});

async function connected(fn) {
  const synth = await new SynthModuleClient('127.0.0.1', { port: wsPort, WebSocket }).connect();
  try {
    await fn(synth);
  } finally {
    synth.close();
    await new Promise((r) => setTimeout(r, 700)); // let the daemon free the client slot
  }
}

test('packMsg/unpackMsg round-trip big-endian', () => {
  const buf = packMsg(CMD.MIDI_CC, 3, 74, 0x1234, 2);
  assert.deepEqual([...new Uint8Array(buf)], [0x05, 3, 0, 74, 0x12, 0x34, 0, 2]);
  assert.deepEqual(unpackMsg(buf), { cmd: 0x05, slot: 3, val1: 74, val2: 0x1234, flags: 2 });
});

test('packMsg rejects out-of-range values', () => {
  assert.throws(() => packMsg(CMD.CV_NOTE_ON, 256), RangeError);
  assert.throws(() => packMsg(CMD.CV_NOTE_ON, 0, -1), RangeError);
  assert.throws(() => packMsg(CMD.CV_NOTE_ON, 0, 1.5), RangeError);
});

test('url is built from host and port, or passed through', () => {
  assert.equal(new SynthModuleClient('pi.local').url, 'ws://pi.local:9743');
  assert.equal(new SynthModuleClient('pi.local', { port: 1234 }).url, 'ws://pi.local:1234');
  assert.equal(new SynthModuleClient('ws://x:1/').url, 'ws://x:1/');
});

test('ping, queryDevices, notes and transport', async () => {
  await connected(async (synth) => {
    assert.ok(synth.isConnected);
    const pong = await synth.ping();
    assert.ok(pong.rttMs >= 0);
    assert.ok(pong.uptimeS >= 0);

    const info = await synth.queryDevices();
    assert.equal(info.cv_slots, 12);
    assert.ok(Array.isArray(info.midi_devices));

    synth.cvNoteOn(0, 60, 100);
    synth.cvGateOff(0);
    synth.midiNoteOn(0, 64);
    synth.midiNoteOff(0, 64);
    synth.midiCC(0, 74, 90);
    await synth.clockStart(120);
    await synth.setBpm(140);
    await synth.setRunState(true);
    await synth.setRunState(false);
    await synth.clockStop();
    synth.allNotesOff();
    await synth.ping();
  });
});

test('concurrent requests resolve in order', async () => {
  await connected(async (synth) => {
    const [a, devices, b] = await Promise.all([synth.ping(), synth.queryDevices(), synth.clockStart(100)]);
    assert.ok(a.rttMs >= 0);
    assert.equal(devices.cv_slots, 12);
    assert.equal(b, undefined);
  });
});

test('second client is rejected as busy', async () => {
  await connected(async () => {
    const other = new SynthModuleClient('127.0.0.1', { port: wsPort, WebSocket });
    await assert.rejects(other.connect(), (err) => {
      assert.ok(err instanceof SynthModuleError);
      assert.match(err.message, /busy/);
      return true;
    });
  });
});

test('sending while disconnected throws', () => {
  const synth = new SynthModuleClient('127.0.0.1', { port: wsPort, WebSocket });
  assert.throws(() => synth.cvNoteOn(0, 60), SynthModuleError);
});

test('connect to closed port rejects', async () => {
  const synth = new SynthModuleClient('127.0.0.1', { port: 1, WebSocket, timeout: 1000 });
  await assert.rejects(synth.connect(), SynthModuleError);
});

test('RESP constants match protocol', () => {
  assert.deepEqual(RESP, { PONG: 0xfe, ERROR: 0xf0, ACK: 0xf1 });
});

test('onclose fires when an established connection drops', async () => {
  // Fake daemon: answers the connect ping, then drops the connection
  const server = new WebSocketServer({ port: 0 });
  await once(server, 'listening');
  server.on('connection', (sock) => {
    sock.on('message', () => {
      sock.send(packMsg(RESP.PONG));
      setTimeout(() => sock.terminate(), 50);
    });
  });
  try {
    const synth = new SynthModuleClient('127.0.0.1', { port: server.address().port, WebSocket });
    const dropped = new Promise((resolve) => { synth.onclose = resolve; });
    await synth.connect();
    await dropped;
    assert.equal(synth.isConnected, false);
  } finally {
    server.close();
  }
});

test('onclose does not fire for close() or a rejected connect()', async () => {
  let calls = 0;
  await connected(async (synth) => {
    const other = new SynthModuleClient('127.0.0.1', { port: wsPort, WebSocket });
    other.onclose = () => { calls += 1; };
    await assert.rejects(other.connect(), /busy/);
    synth.onclose = () => { calls += 1; };
  });
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(calls, 0);
});
