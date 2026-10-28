"""End-to-end sim test (no hardware, no GUI):
ControlLink --UDP--> bridge.py --pty--> fake_blackpill.py --pty--> bridge --UDP--> ControlLink
"""
import os
import socket
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "protocol"))
sys.path.insert(0, os.path.join(ROOT, "topside"))
import rovproto as rp  # noqa: E402
from control import ControlLink  # noqa: E402

N = 6


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def system():
    procs = []
    pair = subprocess.Popen([sys.executable, os.path.join(ROOT, "sim", "ptypair.py")],
                            stdout=subprocess.PIPE, text=True)
    procs.append(pair)
    a, b = pair.stdout.readline().split()
    ctl_port, tel_port = free_port(), free_port()
    procs.append(subprocess.Popen([sys.executable, os.path.join(ROOT, "sim", "fake_blackpill.py"),
                                   "--serial", a, "--quiet"], stdout=subprocess.DEVNULL))
    procs.append(subprocess.Popen([sys.executable, os.path.join(ROOT, "pi", "bridge.py"), "--no-camera", "--serial", b,
                                   "--udp-port", str(ctl_port), "--telem-port", str(tel_port),
                                   "--laptop", "127.0.0.1"], stderr=subprocess.DEVNULL))
    time.sleep(0.8)
    link = ControlLink("127.0.0.1", ctl_port, tel_port, N).start()
    yield link
    link.stop()
    for p in procs:
        p.terminate()
    for p in procs:
        p.wait(timeout=3)


def wait_for(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_boot_disarmed_and_telemetry(system):
    assert wait_for(lambda: system.snapshot()[0] is not None, 2)
    t, age, _ = system.snapshot()
    assert not t.armed and t.pwm_us == [1500] * N


def test_disarmed_ignores_thrusters(system):
    system.set_thrusters([1000] * N)
    time.sleep(0.4)
    assert system.snapshot()[0].pwm_us == [1500] * N


def test_arm_pwm_and_failsafe(system):
    system.request_arm(True)
    assert wait_for(system.board_armed, 2), "board never armed"
    cmds = [1000, -1000, 500, -500, 0, 250]
    system.set_thrusters(cmds)
    want = [rp.cmd_to_pwm(v) for v in cmds]
    assert wait_for(lambda: system.snapshot()[0].pwm_us == want, 1)
    assert want == [1900, 1100, 1700, 1300, 1500, 1600]

    # laptop "dies": stop sending, keep listening
    system.pause_sending(True)
    t_kill = time.monotonic()
    assert wait_for(lambda: system.snapshot()[0].failsafe, 2)
    elapsed = time.monotonic() - t_kill
    t = system.snapshot()[0]
    assert not t.armed and t.pwm_us == [1500] * N
    # telemetry is 10 Hz so allow one extra telemetry period on top of 500 ms
    assert elapsed < 0.5 + 0.15, f"failsafe took {elapsed * 1000:.0f} ms"

    # must not re-arm on its own when the link returns
    system.pause_sending(False)
    time.sleep(0.5)
    assert not system.snapshot()[0].armed
    system.request_arm(True)
    assert wait_for(system.board_armed, 2)
    assert not system.snapshot()[0].failsafe


def test_disarm(system):
    system.request_arm(True)
    assert wait_for(system.board_armed, 2)
    system.set_thrusters([800] * N)
    time.sleep(0.2)
    system.request_arm(False)
    assert wait_for(lambda: not system.snapshot()[0].armed, 1)
    assert system.snapshot()[0].pwm_us == [1500] * N


def test_lights_disarmed_end_to_end(system):
    assert wait_for(lambda: system.snapshot()[0] is not None, 2)
    assert system.snapshot()[0].lights_us == 1100                    # boots off
    system.set_lights(500)
    assert wait_for(lambda: system.snapshot()[0].lights_us == 1500, 1.5)
    system.set_lights(0)
    assert wait_for(lambda: system.snapshot()[0].lights_us == 1100, 1.5)
