export declare const MSG_SIZE: 8;
export declare const DEFAULT_WS_PORT: 9743;
export declare const CLOSE_BUSY: 1013;

export declare const CMD: Readonly<{
  CV_NOTE_ON: number;
  CV_GATE_OFF: number;
  MIDI_NOTE_ON: number;
  MIDI_NOTE_OFF: number;
  MIDI_CC: number;
  CLOCK_START: number;
  CLOCK_STOP: number;
  CLOCK_SET_BPM: number;
  RUN_STATE: number;
  ALL_NOTES_OFF: number;
  QUERY_DEVICES: number;
  PING: number;
  SHUTDOWN: number;
}>;

export declare const RESP: Readonly<{
  PONG: number;
  ERROR: number;
  ACK: number;
}>;

export interface Message {
  cmd: number;
  slot: number;
  val1: number;
  val2: number;
  flags: number;
}

export declare function packMsg(
  cmd: number, slot?: number, val1?: number, val2?: number, flags?: number
): ArrayBuffer;
export declare function unpackMsg(buf: ArrayBuffer): Message;

export interface MidiDevice {
  index: number;
  name: string;
  default: boolean;
}

export interface DeviceInfo {
  midi_devices: MidiDevice[];
  cv_slots: number;
}

export interface Pong {
  rttMs: number;
  uptimeS: number;
}

export interface SynthModuleClientOptions {
  /** WebSocket port (ignored if host is a ws:// URL). Default 9743. */
  port?: number;
  /** ms to wait for connect and replies. Default 2000. */
  timeout?: number;
  /** WebSocket implementation; defaults to globalThis.WebSocket. */
  WebSocket?: unknown;
}

export declare class SynthModuleError extends Error {}

export declare class SynthModuleClient {
  constructor(host: string, options?: SynthModuleClientOptions);
  readonly url: string;
  timeout: number;
  readonly isConnected: boolean;
  onclose: ((event: CloseEvent) => void) | null;

  connect(): Promise<this>;
  close(): void;

  cvNoteOn(slot: number, note: number, velocity?: number): void;
  cvGateOff(slot: number, note?: number): void;

  midiNoteOn(channel: number, note: number, velocity?: number, device?: number): void;
  midiNoteOff(channel: number, note: number, device?: number): void;
  midiCC(channel: number, cc: number, value: number, device?: number): void;

  clockStart(bpm: number): Promise<void>;
  clockStop(): Promise<void>;
  setBpm(bpm: number): Promise<void>;
  setRunState(running: boolean): Promise<void>;

  allNotesOff(): void;
  ping(): Promise<Pong>;
  queryDevices(): Promise<DeviceInfo>;
}
