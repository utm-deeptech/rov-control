"""ROV link protocol — shared encoder/decoder (see PROTOCOL.md).

Frame: [0xAA][0x55][len:u8][msg_id:u8][seq:u8][payload:len][crc16:u16 LE]
CRC16-CCITT (poly 0x1021, init 0xFFFF, no reflection, no xorout) over len, msg_id, seq, payload.
"""
import struct
from dataclasses import dataclass, field

SYNC = b"\xAA\x55"
MAX_LEN = 64
HEADER_LEN = 5          # sync(2) + len + msg_id + seq
OVERHEAD = HEADER_LEN + 2

HEARTBEAT = 0x01
ARM = 0x02
THRUSTERS = 0x03
LIGHTS = 0x04
TELEMETRY = 0x81
ACK = 0x82

NAMES = {HEARTBEAT: "HEARTBEAT", ARM: "ARM", THRUSTERS: "THRUSTERS", LIGHTS: "LIGHTS",
         TELEMETRY: "TELEMETRY", ACK: "ACK"}

PWM_CENTER = 1500
PWM_MIN = 1100
PWM_MAX = 1900
CMD_MAX = 1000
LIGHTS_OFF_US = 1100    # lights PWM: 1100 us = off ... 1900 us = full (Blue Robotics Lumen style)
LIGHTS_FULL_US = 1900


def _make_table():
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
        table.append(crc)
    return table


_TABLE = _make_table()


def crc16_ccitt(data, crc=0xFFFF):
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


def encode(msg_id, seq, payload=b""):
    payload = bytes(payload)
    if len(payload) > MAX_LEN:
        raise ValueError(f"payload too long ({len(payload)} > {MAX_LEN})")
    body = bytes((len(payload), msg_id & 0xFF, seq & 0xFF)) + payload
    return SYNC + body + struct.pack("<H", crc16_ccitt(body))


@dataclass
class Frame:
    msg_id: int
    seq: int
    payload: bytes
    raw: bytes = field(repr=False, default=b"")


class Parser:
    """Stream parser: feed() arbitrary byte chunks, get back whole valid frames.

    Resyncs on 0xAA 0x55, drops frames with bad CRC or len > MAX_LEN.
    """

    def __init__(self):
        self.buf = bytearray()
        self.ok = 0
        self.bad = 0

    def feed(self, data):
        self.buf += data
        out = []
        buf = self.buf
        while True:
            i = buf.find(SYNC)
            if i < 0:
                # keep a trailing 0xAA: it may be the first half of a sync
                del buf[: max(0, len(buf) - 1) if buf[-1:] == b"\xAA" else len(buf)]
                break
            if i:
                del buf[:i]
            if len(buf) < HEADER_LEN:
                break
            ln = buf[2]
            if ln > MAX_LEN:
                self.bad += 1
                del buf[:1]
                continue
            total = OVERHEAD + ln
            if len(buf) < total:
                break
            body = bytes(buf[2:HEADER_LEN + ln])
            (crc,) = struct.unpack_from("<H", buf, HEADER_LEN + ln)
            if crc != crc16_ccitt(body):
                self.bad += 1
                del buf[:1]          # resync: next sync may be inside this "frame"
                continue
            out.append(Frame(buf[3], buf[4], bytes(buf[HEADER_LEN:HEADER_LEN + ln]), bytes(buf[:total])))
            self.ok += 1
            del buf[:total]
        return out


# ---------- payload helpers ----------

def heartbeat(seq, laptop_ms):
    return encode(HEARTBEAT, seq, struct.pack("<I", laptop_ms & 0xFFFFFFFF))


def arm(seq, armed):
    return encode(ARM, seq, bytes((1 if armed else 0,)))


def thrusters(seq, values):
    vals = [max(-CMD_MAX, min(CMD_MAX, int(v))) for v in values]
    return encode(THRUSTERS, seq, struct.pack(f"<{len(vals)}h", *vals))


def lights(seq, level):
    """level 0..1000 (0 = off)."""
    return encode(LIGHTS, seq, struct.pack("<H", max(0, min(CMD_MAX, int(level)))))


def ack(seq, acked_msg_id, acked_seq):
    return encode(ACK, seq, bytes((acked_msg_id & 0xFF, acked_seq & 0xFF)))


@dataclass
class Telemetry:
    uptime_ms: int
    armed: bool
    failsafe: bool
    rx_ok: int
    rx_bad: int
    lights_us: int
    pwm_us: list


_TELEM_HDR = struct.Struct("<IBBHHH")


def telemetry(seq, t):
    return encode(TELEMETRY, seq,
                  _TELEM_HDR.pack(t.uptime_ms & 0xFFFFFFFF, int(t.armed), int(t.failsafe),
                                  t.rx_ok & 0xFFFF, t.rx_bad & 0xFFFF, t.lights_us & 0xFFFF)
                  + struct.pack(f"<{len(t.pwm_us)}H", *t.pwm_us))


def decode_payload(frame):
    """Return a python value for a frame's payload, or raise ValueError."""
    p = frame.payload
    m = frame.msg_id
    if m == HEARTBEAT:
        if len(p) != 4:
            raise ValueError("HEARTBEAT len")
        return struct.unpack("<I", p)[0]
    if m == ARM:
        if len(p) != 1:
            raise ValueError("ARM len")
        return bool(p[0])
    if m == THRUSTERS:
        if len(p) % 2:
            raise ValueError("THRUSTERS len")
        return list(struct.unpack(f"<{len(p) // 2}h", p))
    if m == LIGHTS:
        if len(p) != 2:
            raise ValueError("LIGHTS len")
        return struct.unpack("<H", p)[0]
    if m == TELEMETRY:
        if len(p) < _TELEM_HDR.size or (len(p) - _TELEM_HDR.size) % 2:
            raise ValueError("TELEMETRY len")
        up, a, fs, ok, bad, li = _TELEM_HDR.unpack_from(p)
        n = (len(p) - _TELEM_HDR.size) // 2
        pwm = list(struct.unpack_from(f"<{n}H", p, _TELEM_HDR.size))
        return Telemetry(up, bool(a), bool(fs), ok, bad, li, pwm)
    if m == ACK:
        if len(p) != 2:
            raise ValueError("ACK len")
        return (p[0], p[1])
    raise ValueError(f"unknown msg_id 0x{m:02X}")


def cmd_to_pwm(value):
    """Blackpill mapping used while armed."""
    pwm = PWM_CENTER + (int(value) * 400) // 1000
    return max(PWM_MIN, min(PWM_MAX, pwm))


def lights_to_pwm(level):
    """Board mapping for the lights output (works armed or disarmed)."""
    level = max(0, min(CMD_MAX, int(level)))
    return LIGHTS_OFF_US + (level * (LIGHTS_FULL_US - LIGHTS_OFF_US)) // CMD_MAX
