"""Host test: the Blue Pill protocol core (C) against the Python reference (protocol/rovproto.py)."""
import ctypes
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "protocol"))
import rovproto as rp  # noqa: E402


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    so = str(tmp_path_factory.mktemp("fw") / "core.so")
    subprocess.check_call(["gcc", "-shared", "-fPIC", "-O1", "-Wall", "-Werror",
                           os.path.join(HERE, "common", "core.c"), "-o", so])
    l = ctypes.CDLL(so)
    l.core_pwm.restype = ctypes.c_uint16
    l.core_crc16.restype = ctypes.c_uint16
    l.core_crc16.argtypes = [ctypes.c_char_p, ctypes.c_int]
    l.core_rx.argtypes = [ctypes.c_uint8, ctypes.c_uint32]
    l.core_tick.argtypes = [ctypes.c_uint32]
    l.core_linked.argtypes = [ctypes.c_uint32]
    return l


class Dut:
    def __init__(self, lib):
        self.l = lib
        self.l.core_init()
        self.seq = 0
        self.parser = rp.Parser()

    def send(self, frame, now):
        for b in frame:
            self.l.core_rx(b, now)

    def tick(self, now):
        self.l.core_tick(now)

    def replies(self):
        buf = ctypes.create_string_buffer(200)
        n = self.l.core_tx_take(buf, 200)
        return self.parser.feed(buf.raw[:n])

    def pwm(self):
        return [self.l.core_pwm(i) for i in range(6)]

    def hb(self, now):
        self.seq += 1
        self.send(rp.heartbeat(self.seq, now), now)


def test_crc_matches_reference(lib):
    assert lib.core_crc16(b"123456789", 9) == 0x29B1
    data = bytes(range(60))
    assert lib.core_crc16(data, 60) == rp.crc16_ccitt(data)


def test_boot_state_and_telemetry(lib):
    d = Dut(lib)
    d.tick(100)
    fr = d.replies()
    assert [f.msg_id for f in fr] == [rp.TELEMETRY]
    t = rp.decode_payload(fr[0])
    assert (t.armed, t.failsafe, t.pwm_us, t.uptime_ms, t.lights_us) == (False, False, [1500] * 6, 100, 1100)


def test_disarmed_ignores_thrusters(lib):
    d = Dut(lib)
    d.send(rp.thrusters(1, [1000] * 6), 10)
    assert d.pwm() == [1500] * 6


def test_arm_acks_and_pwm_mapping(lib):
    d = Dut(lib)
    d.send(rp.arm(7, True), 10)
    ack = [f for f in d.replies() if f.msg_id == rp.ACK]
    assert rp.decode_payload(ack[0]) == (rp.ARM, 7)
    assert d.pwm() == [1500] * 6                     # stays neutral until THRUSTERS arrives
    vals = [1000, -1000, 0, 500, -500, 1]
    d.send(rp.thrusters(8, vals), 20)
    assert d.pwm() == [rp.cmd_to_pwm(v) for v in vals] == [1900, 1100, 1500, 1700, 1300, 1500]


def test_negative_rounding_matches_reference(lib):
    d = Dut(lib)
    d.send(rp.arm(1, True), 0)
    vals = [-1, -3, -999, 3, 999, -2]
    d.send(rp.thrusters(2, vals), 1)
    assert d.pwm() == [rp.cmd_to_pwm(v) for v in vals]


def test_failsafe_after_500ms_and_no_auto_rearm(lib):
    d = Dut(lib)
    d.send(rp.arm(1, True), 0)
    d.send(rp.thrusters(2, [800] * 6), 10)
    d.tick(500)
    assert lib.core_armed() == 1                      # 490 ms since last frame
    d.tick(511)
    assert (lib.core_armed(), lib.core_failsafe(), d.pwm()) == (0, 1, [1500] * 6)
    d.send(rp.thrusters(3, [800] * 6), 600)           # frames alone never re-arm
    assert (lib.core_armed(), d.pwm()) == (0, [1500] * 6)
    d.send(rp.arm(4, True), 700)
    assert (lib.core_armed(), lib.core_failsafe()) == (1, 0)


def test_disarm_goes_neutral_immediately(lib):
    d = Dut(lib)
    d.send(rp.arm(1, True), 0)
    d.send(rp.thrusters(2, [1000] * 6), 1)
    d.send(rp.arm(3, False), 2)
    assert d.pwm() == [1500] * 6 and lib.core_armed() == 0


def test_bad_frames_counted_and_resync(lib):
    d = Dut(lib)
    good = rp.heartbeat(1, 5)
    corrupt = bytearray(good); corrupt[-1] ^= 0xFF
    d.send(b"\x00\x13\xAA" + bytes(corrupt) + good, 50)     # garbage, bad CRC, then a good frame
    d.tick(100)
    t = rp.decode_payload([f for f in d.replies() if f.msg_id == rp.TELEMETRY][-1])
    assert t.rx_ok == 1 and t.rx_bad >= 1


def test_wrong_thruster_count_and_unknown_id_dont_reset_link(lib):
    d = Dut(lib)
    d.send(rp.arm(1, True), 0)
    d.send(rp.encode(rp.THRUSTERS, 2, b"\x00" * 8), 400)    # N=4: wrong
    d.send(rp.encode(0x55, 3, b""), 450)                    # unknown id
    d.tick(600)
    assert lib.core_failsafe() == 1


def test_split_across_bytes_and_back_to_back(lib):
    d = Dut(lib)
    stream = rp.arm(1, True) + rp.thrusters(2, [400] * 6) + rp.heartbeat(3, 9)
    for b in stream:
        lib.core_rx(b, 5)
    assert d.pwm() == [1660] * 6


def test_lights_any_state_and_survive_failsafe(lib):
    d = Dut(lib)
    lib.core_lights.restype = ctypes.c_uint16
    d.send(rp.lights(1, 500), 0)
    assert lib.core_lights() == rp.lights_to_pwm(500) == 1500       # disarmed: still works
    d.send(rp.arm(2, True), 10)
    d.send(rp.lights(3, 1000), 20)
    d.tick(600)                                                      # link lost -> failsafe
    assert lib.core_failsafe() == 1 and lib.core_lights() == 1900    # lights stay on to find the ROV
    d.send(rp.lights(4, 9999), 700)
    assert lib.core_lights() == 1900
    d.tick(800)
    t = rp.decode_payload([f for f in d.replies() if f.msg_id == rp.TELEMETRY][-1])
    assert t.lights_us == 1900
