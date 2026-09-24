"""
Remote hardware protocol constants and helpers.

Shared between RemoteBackend (PC side) and hw_daemon (Pi side).
Keep in sync with remote_protocol.py in the ai_midi repo.
All messages are fixed 8-byte binary packets: >BBHHH
"""

import struct

# Message format: cmd(u8) slot(u8) val1(u16) val2(u16) flags(u16) = 8 bytes
MSG_FORMAT = '>BBHHH'
MSG_SIZE = 8

# --- Command types ---

# Hot path (fire-and-forget, no ACK)
CMD_CV_NOTE_ON    = 0x01  # slot=slot, val1=note, val2=velocity
CMD_CV_GATE_OFF   = 0x02  # slot=slot, val1=note
CMD_MIDI_NOTE_ON  = 0x03  # slot=channel, val1=note, val2=velocity
CMD_MIDI_NOTE_OFF = 0x04  # slot=channel, val1=note
CMD_MIDI_CC       = 0x05  # slot=channel, val1=cc_number, val2=cc_value

# Clock/transport (ACK'd)
CMD_CLOCK_START   = 0x10  # val1=bpm
CMD_CLOCK_STOP    = 0x11
CMD_CLOCK_SET_BPM = 0x12  # val1=bpm
CMD_RUN_STATE     = 0x20  # val1=1 run, 0 stop

# Safety
CMD_ALL_NOTES_OFF = 0x30

# Query (variable-length response: 4-byte big-endian length + JSON)
CMD_QUERY_DEVICES = 0x40

# Control
CMD_PING          = 0xFE
CMD_SHUTDOWN      = 0xFF

# --- Response types (Pi -> PC) ---
RESP_PONG         = 0xFE  # val1=uptime seconds
RESP_ERROR        = 0xF0  # val1=error code, val2=failed cmd
RESP_ACK          = 0xF1  # val1=ack'd cmd

# Default ports
DEFAULT_PORT = 9741
DISCOVERY_PORT = 9742

# UDP discovery protocol
DISCOVERY_MAGIC = b"AI_MIDI_DISCOVER"
DISCOVERY_RESPONSE_MAGIC = b"AI_MIDI_HERE"


def pack_msg(cmd, slot=0, val1=0, val2=0, flags=0):
    """Pack a command into an 8-byte message."""
    return struct.pack(MSG_FORMAT, cmd, slot, val1, val2, flags)


def unpack_msg(data):
    """Unpack an 8-byte message into (cmd, slot, val1, val2, flags)."""
    return struct.unpack(MSG_FORMAT, data)
