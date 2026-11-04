"""Host test: the Blue Pill protocol core (C) against the Python reference (protocol/rovproto.py)."""
import ctypes
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "protocol"))
import rovproto as rp  # noqa: E402


CORE = ["core.c", "proto.c", "params.c", "pid.c", "mixer.c"]


def build(tmp, name, sim=False):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not installed (Windows: install MSYS2/MinGW or run these in WSL)")
    win = os.name == "nt"
    so = str(tmp / (f"{name}.dll" if win else f"{name}.so"))
    srcs = [os.path.join(HERE, "common", f) for f in CORE + (["sim.c"] if sim else [])]
    srcs.append(os.path.join(HERE, "host", "flash_emu.c"))
    subprocess.check_call(["gcc", "-shared"] + ([] if win else ["-fPIC"])
                          + ["-O1", "-Wall", "-Wextra", "-Werror", "-I", os.path.join(HERE, "common")]
                          + (["-DBENCH_SIM"] if sim else []) + srcs + ["-o", so])
    l = ctypes.CDLL(so)
    l.core_pwm.restype = ctypes.c_uint16
    l.core_gimbal.restype = ctypes.c_uint16
    l.core_lights.restype = ctypes.c_uint16
    l.core_crc16.restype = ctypes.c_uint16
    l.core_crc16.argtypes = [ctypes.c_char_p, ctypes.c_int]
    l.core_rx.argtypes = [ctypes.c_uint8, ctypes.c_uint32]
    l.core_rx_port.argtypes = [ctypes.c_int, ctypes.c_uint8, ctypes.c_uint32]
    l.core_tick.argtypes = [ctypes.c_uint32]
    l.core_linked.argtypes = [ctypes.c_uint32]
    l.flash_emu_poke.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    return l


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    l = build(tmp_path_factory.mktemp("fw"), "core")
    yield l


@pytest.fixture(autouse=True)
def clean_flash(request):
    for name in ("lib", "simlib"):
        if name in request.fixturenames:
            request.getfixturevalue(name).flash_emu_wipe()


@pytest.fixture(scope="module")
def simlib(tmp_path_factory):
    return build(tmp_path_factory.mktemp("fwsim"), "core_sim", sim=True)


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
        buf = ctypes.create_string_buffer(600)
        n = self.l.core_tx_take(buf, 600)
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
    fr = [f for f in d.replies() if f.msg_id == rp.TELEMETRY]    # v3 also sends STATUS; v2 apps ignore it
    assert len(fr) == 1
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


def test_repeated_arm_while_armed_keeps_thrust(lib):
    d = Dut(lib)
    d.send(rp.arm(1, True), 0)
    d.send(rp.thrusters(2, [500] * 6), 10)
    d.send(rp.arm(3, True), 20)                    # resent ARM (lost ACK): no dropout
    assert d.pwm() == [1700] * 6
    assert [rp.decode_payload(f) for f in d.replies() if f.msg_id == rp.ACK][-1] == (rp.ARM, 3)
    d.send(rp.arm(4, False), 30)
    assert d.pwm() == [1500] * 6


# ======================= protocol v3: vehicle controller =======================
import math  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "topside"))
from mixer import Mixer  # noqa: E402


class Vehicle(Dut):
    """Drives the core like the topside does: PILOT at 50 Hz, core_tick every ms."""

    def __init__(self, lib):
        super().__init__(lib)
        self.now = 0
        self.mode, self.dof, self.lights, self.gimbal = rp.MODE_MANUAL, [0] * 6, 0, 0
        self.frames = []
        self.pilot_on = True

    def next(self):
        self.seq = (self.seq + 1) & 0xFF
        return self.seq

    def tx(self, frame):
        self.send(frame, self.now)

    def run(self, ms):
        for _ in range(ms):
            self.now += 1
            if self.pilot_on and self.now % 20 == 0:
                self.tx(rp.pilot(self.next(), self.mode, self.dof, self.lights, self.gimbal))
            self.tick(self.now)
            self.frames += self.replies()

    def take(self, msg_id):
        out = [rp.decode_payload(f) for f in self.frames if f.msg_id == msg_id]
        self.frames = [f for f in self.frames if f.msg_id != msg_id]
        return out

    def status(self):
        """A STATUS sent after everything done so far (it goes out every 50 ms)."""
        self.take(rp.STATUS)
        self.run(55)
        return self.take(rp.STATUS)[-1]

    def arm(self, on=True):
        seq = self.next()
        self.tx(rp.arm(seq, on))
        self.run(30)
        return seq

    def params(self):
        self.tx(rp.param_get(self.next()))
        self.run(1000)
        return {p.name: p for p in self.take(rp.PARAM_VALUE)}

    def set(self, name, value, params=None):
        params = params or self.params()
        self.tx(rp.param_set(self.next(), params[name].index, value))
        self.run(5)
        return self.take(rp.RESULT)[-1][2], self.take(rp.PARAM_VALUE)[-1].value


def board_mixer(params):
    rows = [[params[f"MIX_T{t}_{d.upper()}"].value for d in rp.DOFS] for t in range(1, 7)]
    return Mixer(rows), params["THR_LIMIT"].value


def test_pilot_manual_mixes_like_topside_mixer(lib):
    v = Vehicle(lib)
    v.run(50)
    v.arm()
    assert lib.core_armed() == 1 and lib.core_mode() == rp.MODE_MANUAL
    m, limit = board_mixer(v.params())
    for cmd in ({"surge": 1}, {"sway": -0.5, "yaw": 0.4}, {"heave": 1, "roll": 0.3}, {"surge": 1, "sway": 1, "yaw": 1}):
        v.dof = [int(cmd.get(d, 0) * 1000) for d in rp.DOFS]
        v.run(600)                                            # > slew time (0.25 s to full)
        want = [rp.cmd_to_pwm(x) for x in m.mix(cmd, gain=limit)]
        assert all(abs(a - b) <= 1 for a, b in zip([v.l.core_pwm(i) for i in range(6)], want)), (cmd, want)


def test_vectored_frame_signs(lib):
    """BlueROV2 vectored (ArduSub): forward = front pair (T1,T2) negative, rear pair (T3,T4) positive."""
    v = Vehicle(lib)
    v.arm()
    v.dof = [1000, 0, 0, 0, 0, 0]
    v.run(400)
    p = [v.l.core_pwm(i) for i in range(6)]
    assert p[0] < 1500 and p[1] < 1500 and p[2] > 1500 and p[3] > 1500 and p[4] == p[5] == 1500
    v.dof = [0, 0, 1000, 0, 0, 0]
    v.run(600)
    p = [v.l.core_pwm(i) for i in range(6)]
    assert p[:4] == [1500] * 4 and p[4] < 1500 and p[5] < 1500


def test_slew_limits_thruster_rate(lib):
    v = Vehicle(lib)
    v.arm()
    v.dof = [0, 0, 1000, 0, 0, 0]
    v.run(60)                                                  # ~50 ms of command at 4 /s -> ~0.2
    assert 1500 - 400 * 0.3 < v.l.core_pwm(4) < 1500 - 400 * 0.1


def test_prearm_refuses_with_sticks_deflected(lib):
    v = Vehicle(lib)
    v.dof = [600, 0, 0, 0, 0, 0]
    v.run(50)
    arm_seq = v.arm()
    assert lib.core_armed() == 0
    assert any(f == (rp.ARM, arm_seq) for f in v.take(rp.ACK))   # still ACKed: the app stops resending
    assert rp.ARM_BLOCKS[v.status().arm_block] == "sticks not centred"
    v.dof = [0] * 6
    v.run(50)
    v.arm()
    assert lib.core_armed() == 1 and v.status().arm_block == 0


def test_direct_and_pilot_dont_mix(lib):
    v = Vehicle(lib)
    v.arm()
    v.tx(rp.thrusters(v.next(), [800] * 6))                     # MANUAL: raw THRUSTERS are ignored
    v.run(30)
    assert [v.l.core_pwm(i) for i in range(6)] == [1500] * 6
    v.mode = rp.MODE_DIRECT
    v.dof = [1000, 0, 0, 0, 0, 0]
    v.run(60)
    v.tx(rp.thrusters(v.next(), [800] * 6))
    assert [v.l.core_pwm(i) for i in range(6)] == [1820] * 6


def test_pilot_timeout_zeroes_sticks_but_stays_armed(lib):
    v = Vehicle(lib)
    v.arm()
    v.dof = [1000, 0, 0, 0, 0, 0]
    v.run(400)
    assert v.l.core_pwm(0) != 1500
    v.pilot_on = False
    for _ in range(40):                                        # heartbeats keep the link, pilot frames stop
        v.tx(rp.heartbeat(v.next(), v.now))
        v.run(25)
    assert lib.core_armed() == 1 and [v.l.core_pwm(i) for i in range(6)] == [1500] * 6
    assert v.status().flag("pilot_stale")


def test_failsafe_in_manual(lib):
    v = Vehicle(lib)
    v.arm()
    v.dof = [1000, 0, 0, 0, 0, 0]
    v.run(400)
    v.pilot_on = False
    v.run(520)
    assert (lib.core_armed(), lib.core_failsafe()) == (0, 1)
    assert [v.l.core_pwm(i) for i in range(6)] == [1500] * 6


def test_hold_modes_refused_without_sensors(lib):
    v = Vehicle(lib)
    v.mode = rp.MODE_DEPTH_HOLD
    v.run(100)
    st = v.status()
    assert (st.mode_req, st.mode) == (rp.MODE_DEPTH_HOLD, rp.MODE_MANUAL)
    assert not st.flag("depth_ok") and not st.flag("sim")


def test_reverse_and_output_map(lib):
    v = Vehicle(lib)
    pr = v.params()
    assert v.set("THR_REV_T5", 1, pr)[0] == 0
    assert v.set("THR_OUT_T5", 6, pr)[0] == 0 and v.set("THR_OUT_T6", 5, pr)[0] == 0
    v.arm()
    v.dof = [0, 0, 1000, 0, 0, 0]                               # heave: T5,T6 both < 1500 by default
    v.run(600)
    out = [v.l.core_pwm(i) for i in range(6)]
    assert out[5] > 1500 and out[4] < 1500                       # T5 now on output 6 and flipped
    v.arm(False)
    v.set("THR_OUT_T6", 6, pr)                                   # two thrusters on output 6
    v.run(50)
    v.arm()
    assert lib.core_armed() == 0 and v.status().arm_block == 2


def test_params_stream_set_clamp_and_armed_rules(lib):
    v = Vehicle(lib)
    pr = v.params()
    assert len(pr) == next(iter(pr.values())).count > 60
    assert pr["FS_TIMEOUT_MS"].value == 500 and pr["MIX_T1_SURGE"].value == -1
    assert v.set("DEPTH_KP", 999, pr) == (0, 20.0)               # clamped to max
    assert v.set("THR_OUT_T1", 2.4, pr) == (0, 2.0)              # integer param rounds
    v.set("THR_OUT_T1", 1, pr)
    v.arm()
    assert v.set("DEPTH_KP", 3.5, pr) == (0, 3.5)                # gains: live tuning allowed
    assert v.set("MIX_T1_SURGE", 1, pr) == (1, -1.0)             # matrix: denied while armed
    v.tx(rp.param_save(v.next()))
    v.run(5)
    assert v.take(rp.RESULT)[-1][2] == 1                         # save: denied while armed


def test_params_survive_reboot_and_power_cut(lib):
    v = Vehicle(lib)
    pr = v.params()
    v.set("DEPTH_KI", 1.25, pr)
    assert v.status().flag("params_dirty")
    v.tx(rp.param_save(v.next())); v.run(5)
    assert v.take(rp.RESULT)[-1][2] == 0
    v.set("DEPTH_KI", 2.5, pr)
    v.tx(rp.param_save(v.next())); v.run(5)
    v2 = Vehicle(lib)                                            # "reboot"
    p2 = v2.params()
    assert p2["DEPTH_KI"].value == 2.5
    v2.run(60)
    assert v2.status().flag("params_loaded") and not v2.status().flag("params_dirty")
    rec = 5 + p2["DEPTH_KI"].count                               # words per record (header 4 + values + crc); 2nd record = the 2.5 one
    lib.flash_emu_poke(rec + 4 + p2["DEPTH_KI"].index, 0)        # corrupt it: like a power cut mid-save
    v3 = Vehicle(lib)
    assert v3.params()["DEPTH_KI"].value == 1.25                 # previous good record wins


def test_every_param_survives_reboot_including_last(lib):
    v = Vehicle(lib)
    pr = v.params()
    last = max(pr.values(), key=lambda p: p.index)               # the record's crc once overwrote this one
    assert v.set(last.name, 0.5, pr)[0] == 0
    v.tx(rp.param_save(v.next())); v.run(5)
    assert v.take(rp.RESULT)[-1][2] == 0
    assert Vehicle(lib).params()[last.name].value == 0.5


def test_param_save_fills_sector_then_erases(lib):
    v = Vehicle(lib)
    pr = v.params()
    rec = 4 + next(iter(pr.values())).count
    for i in range(2048 // rec + 2):
        v.set("HDG_KP", 0.001 * (i + 1), pr)
        v.tx(rp.param_save(v.next())); v.run(3)
        assert v.take(rp.RESULT)[-1][2] == 0
    assert lib.flash_emu_erases.__class__  # symbol exists
    assert abs(Vehicle(lib).params()["HDG_KP"].value - 0.001 * (2048 // rec + 2)) < 1e-6


def test_gimbal_slews_and_works_disarmed(lib):
    v = Vehicle(lib)
    v.gimbal = 1000
    v.run(100)
    mid = lib.core_gimbal()
    assert 1500 < mid < 1900                                     # 600 us/s: not there yet after 0.1 s
    v.run(1000)
    assert lib.core_gimbal() == 1900
    v.gimbal = -500
    v.run(2000)
    assert lib.core_gimbal() == 1300                             # half of (CTR - MIN) below centre


# ---------- closed loop against the bench plant model (make SIM=1) ----------

def test_sim_depth_hold_holds_against_buoyancy(simlib):
    v = Vehicle(simlib)
    v.mode = rp.MODE_DEPTH_HOLD
    v.run(100)
    st = v.status()
    assert st.mode == rp.MODE_DEPTH_HOLD and st.flag("sim")
    v.arm()
    v.run(3000)
    st = v.status()
    start = st.depth_sp
    assert st.flag("depth_hold")
    v.run(20000)
    st = v.status()
    assert abs(st.depth - start) < 0.05, (st.depth, start)      # sinks 2.5 cm/s without the hold
    assert st.depth_pid[1] > 0.02                                # integrator carries the buoyancy offset


def test_sim_pilot_overrides_then_new_depth_is_held(simlib):
    v = Vehicle(simlib)
    v.mode = rp.MODE_DEPTH_HOLD
    v.arm()
    v.run(2000)
    d0 = v.status().depth
    v.dof = [0, 0, 800, 0, 0, 0]                                 # climb
    v.run(2000)
    assert v.status().depth < d0 - 0.4
    v.dof = [0] * 6
    v.run(15000)
    st = v.status()
    assert st.flag("depth_hold") and abs(st.depth - st.depth_sp) < 0.05


def test_sim_heading_hold_after_turn(simlib):
    v = Vehicle(simlib)
    v.mode = rp.MODE_STABILIZE
    v.arm()
    v.dof = [0, 0, 0, 0, 0, 600]
    v.run(1500)
    v.dof = [0] * 6
    v.run(8000)
    st = v.status()
    err = (st.heading - st.heading_sp + 180) % 360 - 180
    assert st.flag("hdg_hold") and abs(err) < 2, (st.heading, st.heading_sp)
    assert abs(st.roll) < 1.5                                    # levelled against the roll bias
