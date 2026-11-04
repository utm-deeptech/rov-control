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
PILOT = 0x05          # v3
PARAM_GET = 0x06
PARAM_SET = 0x07
PARAM_SAVE = 0x08
PARAM_RESET = 0x09
TELEMETRY = 0x81
ACK = 0x82
STATUS = 0x83         # v3
PARAM_VALUE = 0x84
RESULT = 0x85

NAMES = {HEARTBEAT: "HEARTBEAT", ARM: "ARM", THRUSTERS: "THRUSTERS", LIGHTS: "LIGHTS", PILOT: "PILOT",
         PARAM_GET: "PARAM_GET", PARAM_SET: "PARAM_SET", PARAM_SAVE: "PARAM_SAVE", PARAM_RESET: "PARAM_RESET",
         TELEMETRY: "TELEMETRY", ACK: "ACK", STATUS: "STATUS", PARAM_VALUE: "PARAM_VALUE", RESULT: "RESULT"}

MODES = ("DIRECT", "MANUAL", "STABILIZE", "DEPTH_HOLD")
MODE_DIRECT, MODE_MANUAL, MODE_STABILIZE, MODE_DEPTH_HOLD = range(4)
ARM_BLOCKS = ("", "sticks not centred", "thruster outputs invalid (THR_OUT_Tn)")
RESULTS = ("ok", "not while armed", "flash error", "bad argument")
DOFS = ("surge", "sway", "heave", "roll", "pitch", "yaw")
SAVE_MAGIC = 0x5A

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


def pilot(seq, mode, dof, lights_level=0, gimbal=0):
    """v3 pilot intent, 50 Hz. dof: 6 values -1000..1000 (surge sway heave roll pitch yaw).
    lights 0..1000, gimbal -1000 (full down) .. 1000 (full up)."""
    d = [max(-CMD_MAX, min(CMD_MAX, int(v))) for v in dof]
    if len(d) != 6:
        raise ValueError("pilot needs 6 dof values")
    return encode(PILOT, seq, struct.pack("<BB6hHh", mode, 0, *d, max(0, min(CMD_MAX, int(lights_level))),
                                          max(-CMD_MAX, min(CMD_MAX, int(gimbal)))))


def param_get(seq, index=0xFF):
    """index 0xFF = stream every parameter."""
    return encode(PARAM_GET, seq, bytes((index & 0xFF,)))


def param_set(seq, index, value):
    return encode(PARAM_SET, seq, struct.pack("<Bf", index, float(value)))


def param_save(seq):
    return encode(PARAM_SAVE, seq, bytes((SAVE_MAGIC,)))


def param_reset(seq):
    return encode(PARAM_RESET, seq, bytes((SAVE_MAGIC,)))


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


@dataclass
class Status:
    mode: int
    mode_req: int
    flags: int
    arm_block: int
    loop_us: int
    loop_max_us: int
    load_permille: int
    gimbal_us: int
    depth: float
    depth_sp: float
    heading: float
    heading_sp: float
    roll: float
    pitch: float
    dof: list               # final demand into the mixer, -1..1
    depth_pid: tuple        # (P, I, D) contributions, -1..1
    hdg_pid: tuple

    FLAGS = ("depth_ok", "imu_ok", "depth_hold", "hdg_hold", "sim", "params_dirty", "params_loaded", "pilot_stale")

    def flag(self, name):
        return bool(self.flags & (1 << self.FLAGS.index(name)))


_STATUS = struct.Struct("<BBBBHHHH6f6h3h3h")


@dataclass
class ParamValue:
    index: int
    count: int
    flags: int
    value: float
    default: float
    min: float
    max: float
    name: str


_PARAM = struct.Struct("<BBBxffff16s")


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
    if m == STATUS:
        if len(p) != _STATUS.size:
            raise ValueError("STATUS len")
        v = _STATUS.unpack(p)
        return Status(*v[:8], *v[8:14], [x / 1000 for x in v[14:20]],
                      tuple(x / 1000 for x in v[20:23]), tuple(x / 1000 for x in v[23:26]))
    if m == PARAM_VALUE:
        if len(p) != _PARAM.size:
            raise ValueError("PARAM_VALUE len")
        v = _PARAM.unpack(p)
        return ParamValue(*v[:7], v[7].split(b"\0")[0].decode())
    if m == RESULT:
        if len(p) != 3:
            raise ValueError("RESULT len")
        return (p[0], p[1], p[2])
    if m == PILOT:
        if len(p) != 18:
            raise ValueError("PILOT len")
        v = struct.unpack("<BB6hHh", p)
        return (v[0], list(v[2:8]), v[8], v[9])
    if m in (PARAM_GET, PARAM_SAVE, PARAM_RESET):
        if len(p) != 1:
            raise ValueError("len")
        return p[0]
    if m == PARAM_SET:
        if len(p) != 5:
            raise ValueError("PARAM_SET len")
        return struct.unpack("<Bf", p)
    raise ValueError(f"unknown msg_id 0x{m:02X}")


def cmd_to_pwm(value):
    """Blackpill mapping used while armed."""
    pwm = PWM_CENTER + (int(value) * 400) // 1000
    return max(PWM_MIN, min(PWM_MAX, pwm))


def lights_to_pwm(level):
    """Board mapping for the lights output (works armed or disarmed)."""
    level = max(0, min(CMD_MAX, int(level)))
    return LIGHTS_OFF_US + (level * (LIGHTS_FULL_US - LIGHTS_OFF_US)) // CMD_MAX
