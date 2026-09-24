# Synth module protocol

This document is the contract between the daemon and its clients. The Python
constants live in `clients/python/synth_module_client/protocol.py`; the JS
client mirrors them, and `tests/test_protocol_sync.py` fails if they drift.

## Transports

| Port | Transport | Use |
|------|-----------|-----|
| 9741 | TCP       | Native clients (lowest latency) |
| 9743 | WebSocket | Browsers. Plain `ws://`, any origin accepted |
| 9742 | UDP       | Discovery (not available to browsers) |

TCP and WebSocket carry **identical bytes**. Over TCP the stream is a plain
byte stream. Over WebSocket, every frame must be **binary** and may contain
one or more whole messages; text frames are ignored. Each response is sent as
its own binary frame.

## One client at a time

The daemon serves a single client across both transports. While a client is
connected, new connections are refused:

- TCP: the connection is accepted and closed immediately.
- WebSocket: the handshake completes, then the socket is closed with code
  **1013** and reason `busy: another client is connected`.

When the client disconnects (for any reason) the daemon sends all-notes-off,
stops the clock and drops the run output.

## Message format

Every command is 8 bytes, big-endian:

| Offset | Type | Field |
|--------|------|-------|
| 0 | u8  | `cmd` |
| 1 | u8  | `slot` |
| 2 | u16 | `val1` |
| 4 | u16 | `val2` |
| 6 | u16 | `flags` |

(Python `struct` format `>BBHHH`.) Unused fields are 0.

## Commands

| cmd | Name | slot | val1 | val2 | flags | Reply |
|-----|------|------|------|------|-------|-------|
| 0x01 | CV_NOTE_ON    | CV slot | MIDI note | velocity | – | none |
| 0x02 | CV_GATE_OFF   | CV slot | note (unused) | – | – | none |
| 0x03 | MIDI_NOTE_ON  | channel 0-15 | note | velocity | device | none |
| 0x04 | MIDI_NOTE_OFF | channel 0-15 | note | – | device | none |
| 0x05 | MIDI_CC       | channel 0-15 | controller | value | device | none |
| 0x10 | CLOCK_START   | – | BPM | – | – | ACK |
| 0x11 | CLOCK_STOP    | – | – | – | – | ACK |
| 0x12 | CLOCK_SET_BPM | – | BPM | – | – | ACK |
| 0x20 | RUN_STATE     | – | 1 run / 0 stop | – | – | ACK |
| 0x30 | ALL_NOTES_OFF | – | – | – | – | none |
| 0x40 | QUERY_DEVICES | – | – | – | – | device list |
| 0xFE | PING          | – | – | – | – | PONG |
| 0xFF | SHUTDOWN      | – | – | – | – | none (daemon exits) |

Notes:

- **CV pitch**: 1 V/octave with MIDI note 24 at 0 V, clamped to the DAC
  reference (3.3 V, so notes 24–63). The gate goes high on `CV_NOTE_ON` and
  low on `CV_GATE_OFF`; pitch holds its last value.
- **MIDI device**: `flags` selects an output by index from `QUERY_DEVICES`.
  0 (or an unknown index) means the default output.
- **Clock**: `CLOCK_START`/`CLOCK_SET_BPM` drive the hardware clock output;
  `CLOCK_SET_BPM` restarts the clock if it is running.

## Responses

Fixed 8-byte responses use the same layout:

| cmd | Name | Fields |
|-----|------|--------|
| 0xF1 | ACK   | `val1` = the command being acknowledged |
| 0xFE | PONG  | `val1` = daemon uptime in seconds (mod 65536) |
| 0xF0 | ERROR | `val1` = error code, `val2` = the rejected command |

`QUERY_DEVICES` replies with a variable-length message: a u32 big-endian
length followed by that many bytes of UTF-8 JSON:

```json
{
  "midi_devices": [
    {"index": 0, "name": "USB MIDI Interface:USB MIDI Interface MIDI 1 24:0", "default": true},
    {"index": 1, "name": "Midi Through:Midi Through Port-0 14:0", "default": false}
  ],
  "cv_slots": 12
}
```

### Ordering

Only ACK'd commands, `PING` and `QUERY_DEVICES` produce replies (plus `ERROR`
for unknown commands). Replies arrive in the order the requests were sent, so
a client can match them with a FIFO queue. Fire-and-forget commands never
produce a reply.

If a client gives up waiting for a reply it should drop the connection,
otherwise the late reply would be matched with the next request.

## Discovery (UDP 9742)

Send the ASCII bytes `AI_MIDI_DISCOVER` (unicast or broadcast) to UDP port
9742. Each daemon replies to the sender with `AI_MIDI_HERE` followed by JSON:

```json
{"port": 9741, "ws_port": 9743, "hostname": "raspberrypi", "uptime": 153, "busy": false}
```

`ws_port` is `null` when the WebSocket listener is disabled. `busy` is true
while a client is connected.
