"""6-DOF command -> per-thruster values (-1000..1000) using the mixing matrix from config."""

DOFS = ("surge", "sway", "heave", "roll", "pitch", "yaw")


class Mixer:
    def __init__(self, matrix, reverse=None):
        self.m = [list(map(float, row)) for row in matrix]
        for row in self.m:
            if len(row) != len(DOFS):
                raise ValueError(f"mixing rows need {len(DOFS)} columns {DOFS}")
        self.reverse = list(reverse or [False] * len(self.m))
        if len(self.reverse) != len(self.m):
            raise ValueError("thrusters.reverse length != mixing rows")

    def mix(self, cmd, gain=1.0):
        """cmd: dict dof -> -1..1. gain: 0..1. Returns list[int] in -1000..1000."""
        v = [max(-1.0, min(1.0, float(cmd.get(d, 0.0)))) for d in DOFS]
        raw = [sum(r * x for r, x in zip(row, v)) for row in self.m]
        peak = max((abs(x) for x in raw), default=0.0)
        scale = 1.0 / peak if peak > 1.0 else 1.0          # keep ratios, never exceed full scale
        out = []
        for x, rev in zip(raw, self.reverse):
            val = x * scale * gain * 1000.0
            if rev:
                val = -val
            out.append(int(round(max(-1000.0, min(1000.0, val)))))
        return out
