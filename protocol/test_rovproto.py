import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rovproto as rp  # noqa: E402


def test_crc_known_vector():
    # CRC-16/CCITT-FALSE check value
    assert rp.crc16_ccitt(b"123456789") == 0x29B1


def test_frame_layout():
    f = rp.encode(0x02, 7, b"\x01")
    assert f[:2] == b"\xAA\x55"
    assert f[2] == 1 and f[3] == 0x02 and f[4] == 7 and f[5] == 1
    assert struct.unpack("<H", f[6:8])[0] == rp.crc16_ccitt(f[2:6])


def test_roundtrip_all_messages():
    tel = rp.Telemetry(123456, True, False, 10, 2, 1500, [1500, 1600, 1400, 1900, 1100, 1500])
    frames = [rp.heartbeat(1, 99999), rp.arm(2, True), rp.thrusters(3, [0, 1000, -1000, 5, -5, 500]),
              rp.telemetry(4, tel), rp.ack(5, rp.ARM, 2)]
    out = rp.Parser().feed(b"".join(frames))
    assert [f.msg_id for f in out] == [rp.HEARTBEAT, rp.ARM, rp.THRUSTERS, rp.TELEMETRY, rp.ACK]
    assert [f.seq for f in out] == [1, 2, 3, 4, 5]
    assert rp.decode_payload(out[0]) == 99999
    assert rp.decode_payload(out[1]) is True
    assert rp.decode_payload(out[2]) == [0, 1000, -1000, 5, -5, 500]
    assert rp.decode_payload(out[3]) == tel
    assert rp.decode_payload(out[4]) == (rp.ARM, 2)
    assert out[2].raw == frames[2]


def test_byte_by_byte():
    data = rp.thrusters(9, [1, 2, 3])
    p = rp.Parser()
    got = []
    for b in data:
        got += p.feed(bytes([b]))
    assert len(got) == 1 and rp.decode_payload(got[0]) == [1, 2, 3]


def test_bad_crc_dropped():
    good = rp.arm(1, True)
    bad = bytearray(rp.arm(2, False))
    bad[-1] ^= 0xFF
    p = rp.Parser()
    out = p.feed(bytes(bad) + good)
    assert len(out) == 1 and out[0].seq == 1
    assert p.bad >= 1


def test_resync_on_garbage():
    random.seed(1)
    garbage = bytes(random.randrange(256) for _ in range(500)).replace(b"\xAA\x55", b"\x00\x00")
    good = rp.heartbeat(42, 1)
    p = rp.Parser()
    out = p.feed(garbage + b"\xAA" + good + b"\xAA\x55" + good)
    assert [f.seq for f in out] == [42, 42]


def test_len_over_64_dropped():
    fake = b"\xAA\x55" + bytes([65, 0x03, 0]) + bytes(65) + b"\x00\x00"
    good = rp.arm(3, True)
    p = rp.Parser()
    out = p.feed(fake + good)
    assert len(out) == 1 and out[0].seq == 3
    assert p.bad >= 1


def test_truncated_frame_then_good():
    good = rp.thrusters(5, [100] * 6)
    truncated = good[:7]          # header + part of payload, cut off
    p = rp.Parser()
    out = p.feed(truncated + good + rp.arm(6, False))
    assert [f.seq for f in out][-2:] == [5, 6]


def test_partial_frame_waits():
    good = rp.heartbeat(1, 5)
    p = rp.Parser()
    assert p.feed(good[:-1]) == []
    assert len(p.feed(good[-1:])) == 1


def test_payload_limit():
    import pytest
    with pytest.raises(ValueError):
        rp.encode(1, 0, bytes(65))


def test_cmd_to_pwm():
    assert rp.cmd_to_pwm(0) == 1500
    assert rp.cmd_to_pwm(1000) == 1900
    assert rp.cmd_to_pwm(-1000) == 1100
    assert rp.cmd_to_pwm(500) == 1700
    assert rp.cmd_to_pwm(5000) == 1900


def test_lights_roundtrip_and_mapping():
    fr = rp.Parser().feed(rp.lights(3, 600))[0]
    assert fr.msg_id == rp.LIGHTS and rp.decode_payload(fr) == 600
    assert rp.decode_payload(rp.Parser().feed(rp.lights(1, 5000))[0]) == 1000    # clamped
    assert [rp.lights_to_pwm(v) for v in (0, 500, 1000)] == [1100, 1500, 1900]
