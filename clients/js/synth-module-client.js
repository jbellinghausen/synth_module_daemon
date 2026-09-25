/**
 * Browser/Node client for the synth module daemon, over WebSocket.
 *
 * Speaks the same 8-byte binary protocol as the TCP port (see PROTOCOL.md).
 * Hot-path commands (notes, CC) are fire-and-forget; clock/transport
 * commands return Promises that resolve on ACK.
 *
 * @example
 *   import { SynthModuleClient } from 'synth-module-client';
 *   const synth = await new SynthModuleClient('raspberrypi.local').connect();
 *   synth.cvNoteOn(0, 60, 100);
 *   setTimeout(() => synth.cvGateOff(0), 250);
 */

// --- Protocol constants (mirror clients/python/synth_module_client/protocol.py) ---

export const MSG_SIZE = 8;

export const CMD = Object.freeze({
  CV_NOTE_ON: 0x01,
  CV_GATE_OFF: 0x02,
  MIDI_NOTE_ON: 0x03,
  MIDI_NOTE_OFF: 0x04,
  MIDI_CC: 0x05,
  CLOCK_START: 0x10,
  CLOCK_STOP: 0x11,
  CLOCK_SET_BPM: 0x12,
  RUN_STATE: 0x20,
  ALL_NOTES_OFF: 0x30,
  QUERY_DEVICES: 0x40,
  PING: 0xfe,
  SHUTDOWN: 0xff,
});

export const RESP = Object.freeze({
  PONG: 0xfe,
  ERROR: 0xf0,
  ACK: 0xf1,
});

export const DEFAULT_WS_PORT = 9743;

/** WebSocket close code the daemon uses when another client is connected. */
export const CLOSE_BUSY = 1013;

/** Pack a command into an 8-byte message (big-endian >BBHHH). */
export function packMsg(cmd, slot = 0, val1 = 0, val2 = 0, flags = 0) {
  checkRange('slot', slot, 0xff);
  checkRange('val1', val1, 0xffff);
  checkRange('val2', val2, 0xffff);
  checkRange('flags', flags, 0xffff);
  const buf = new ArrayBuffer(MSG_SIZE);
  const view = new DataView(buf);
  view.setUint8(0, cmd);
  view.setUint8(1, slot);
  view.setUint16(2, val1);
  view.setUint16(4, val2);
  view.setUint16(6, flags);
  return buf;
}

/** Unpack an 8-byte message into {cmd, slot, val1, val2, flags}. */
export function unpackMsg(buf) {
  const view = new DataView(buf);
  return {
    cmd: view.getUint8(0),
    slot: view.getUint8(1),
    val1: view.getUint16(2),
    val2: view.getUint16(4),
    flags: view.getUint16(6),
  };
}

function checkRange(name, value, max) {
  if (!Number.isInteger(value) || value < 0 || value > max) {
    throw new RangeError(`${name} must be an integer 0-${max}, got ${value}`);
  }
}

function closeReason(code) {
  return code === CLOSE_BUSY
    ? 'Daemon is busy with another client'
    : `Connection closed (${code})`;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class SynthModuleError extends Error {}

export class SynthModuleClient {
  /**
   * @param {string} host Hostname/IP of the Pi, or a full ws:// URL.
   * @param {object} [options]
   * @param {number} [options.port=9743] WebSocket port (ignored if host is a URL).
   * @param {number} [options.timeout=2000] ms to wait for connect and replies.
   * @param {typeof WebSocket} [options.WebSocket] WebSocket implementation
   *   (defaults to the global; pass `ws` in older Node versions).
   */
  constructor(host, { port = DEFAULT_WS_PORT, timeout = 2000, WebSocket: WS } = {}) {
    this.url = /^wss?:\/\//.test(host) ? host : `ws://${host}:${port}`;
    this.timeout = timeout;
    this._WebSocket = WS || globalThis.WebSocket;
    this._ws = null;
    this._pending = [];
    /**
     * Called with the CloseEvent when an established connection drops
     * unexpectedly. Not called for close() or for a failed connect().
     */
    this.onclose = null;
  }

  // --- Connection ---

  /** Connect and verify the daemon answers a ping. Resolves to this client. */
  async connect() {
    if (!this._WebSocket) {
      throw new SynthModuleError('No WebSocket implementation available; pass options.WebSocket');
    }
    this.close();
    const ws = new this._WebSocket(this.url);
    ws.binaryType = 'arraybuffer';
    this._ws = ws;

    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new SynthModuleError(`Timed out connecting to ${this.url}`));
        ws.close();
      }, this.timeout);
      ws.onopen = () => { clearTimeout(timer); resolve(); };
      ws.onerror = () => { clearTimeout(timer); reject(new SynthModuleError(`Cannot connect to ${this.url}`)); };
    });

    let resolveClosed;
    const closed = new Promise((resolve) => { resolveClosed = resolve; });
    let established = false;
    ws.onmessage = (event) => this._onMessage(event.data);
    ws.onerror = null;
    ws.onclose = (event) => {
      // close() clears this._ws first, so a user-initiated close isn't "current"
      const unexpected = this._ws === ws && established;
      if (this._ws === ws) this._ws = null;
      this._rejectAll(new SynthModuleError(closeReason(event.code)));
      resolveClosed(event.code);
      if (unexpected && this.onclose) this.onclose(event);
    };

    // The daemon accepts the handshake, then closes with 1013 if it is busy
    try {
      await this.ping();
    } catch (err) {
      const code = await Promise.race([closed, delay(this.timeout)]);
      this.close();
      if (code === CLOSE_BUSY) throw new SynthModuleError(closeReason(code));
      throw err;
    }
    established = true;
    return this;
  }

  /** Close the connection. The daemon silences all outputs on disconnect. */
  close() {
    if (this._ws) {
      const ws = this._ws;
      this._ws = null;
      ws.close();
    }
  }

  get isConnected() {
    return this._ws !== null && this._ws.readyState === 1;
  }

  // --- CV / gate ---

  /**
   * Set a slot's pitch CV for a MIDI note number and raise its gate.
   * Pitch is 1 V/octave with note 24 (C1) at 0 V; the DAC tops out at 3.3 V,
   * so only notes 24-63 are distinct. Out-of-range notes are clamped silently
   * by the daemon (no error).
   */
  cvNoteOn(slot, note, velocity = 127) {
    this._send(CMD.CV_NOTE_ON, slot, note, velocity);
  }

  /** Lower a slot's gate. Pitch CV holds its last value. */
  cvGateOff(slot, note = 0) {
    this._send(CMD.CV_GATE_OFF, slot, note);
  }

  // --- MIDI ---

  /** MIDI note-on. channel is 0-15; device 0 = default output. */
  midiNoteOn(channel, note, velocity = 100, device = 0) {
    this._send(CMD.MIDI_NOTE_ON, channel, note, velocity, device);
  }

  midiNoteOff(channel, note, device = 0) {
    this._send(CMD.MIDI_NOTE_OFF, channel, note, 0, device);
  }

  midiCC(channel, cc, value, device = 0) {
    this._send(CMD.MIDI_CC, channel, cc, value, device);
  }

  // --- Clock / transport (resolve on ACK) ---

  clockStart(bpm) { return this._request(CMD.CLOCK_START, 'ack', 0, bpm); }
  clockStop() { return this._request(CMD.CLOCK_STOP, 'ack'); }
  setBpm(bpm) { return this._request(CMD.CLOCK_SET_BPM, 'ack', 0, bpm); }
  setRunState(running) { return this._request(CMD.RUN_STATE, 'ack', 0, running ? 1 : 0); }

  // --- Safety / control ---

  /** Drop all gates and send MIDI all-notes-off on every channel. */
  allNotesOff() {
    this._send(CMD.ALL_NOTES_OFF);
  }

  /** Round-trip a ping. Resolves to {rttMs, uptimeS}. */
  async ping() {
    const t0 = performance.now();
    const uptimeS = await this._request(CMD.PING, 'pong');
    return { rttMs: performance.now() - t0, uptimeS };
  }

  /** Resolves to {midi_devices: [{index, name, default}], cv_slots}. */
  queryDevices() {
    return this._request(CMD.QUERY_DEVICES, 'devices');
  }

  // --- Internals ---

  _send(cmd, slot = 0, val1 = 0, val2 = 0, flags = 0) {
    const msg = packMsg(cmd, slot, val1, val2, flags);
    if (!this.isConnected) throw new SynthModuleError('Not connected');
    this._ws.send(msg);
  }

  _request(cmd, expect, slot = 0, val1 = 0, val2 = 0) {
    return new Promise((resolve, reject) => {
      const entry = { cmd, expect, resolve, reject, timer: null };
      entry.timer = setTimeout(() => {
        // A late reply would be matched to the next request; start fresh
        this._rejectAll(new SynthModuleError(`Timed out waiting for reply to 0x${cmd.toString(16)}`));
        this.close();
      }, this.timeout);
      this._pending.push(entry);
      try {
        this._send(cmd, slot, val1, val2);
      } catch (err) {
        this._pending.pop();
        clearTimeout(entry.timer);
        reject(err);
      }
    });
  }

  _onMessage(data) {
    const entry = this._pending.shift();
    if (!entry) return; // unsolicited; the daemon does not send these today
    clearTimeout(entry.timer);

    if (!(data instanceof ArrayBuffer)) {
      entry.reject(new SynthModuleError('Expected a binary frame'));
      return;
    }
    if (entry.expect === 'devices') {
      const length = new DataView(data).getUint32(0);
      const json = new TextDecoder().decode(new Uint8Array(data, 4, length));
      entry.resolve(JSON.parse(json));
      return;
    }

    const msg = unpackMsg(data);
    if (msg.cmd === RESP.ERROR) {
      entry.reject(new SynthModuleError(`Daemon rejected command 0x${msg.val2.toString(16)} (error ${msg.val1})`));
    } else if (entry.expect === 'pong' && msg.cmd === RESP.PONG) {
      entry.resolve(msg.val1);
    } else if (entry.expect === 'ack' && msg.cmd === RESP.ACK && msg.val1 === entry.cmd) {
      entry.resolve();
    } else {
      entry.reject(new SynthModuleError(`Unexpected reply 0x${msg.cmd.toString(16)} to 0x${entry.cmd.toString(16)}`));
    }
  }

  _rejectAll(err) {
    const pending = this._pending;
    this._pending = [];
    for (const entry of pending) {
      clearTimeout(entry.timer);
      entry.reject(err);
    }
  }
}
