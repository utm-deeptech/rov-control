import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from thruster_setup import assign  # noqa: E402


def test_assign_swaps_and_stays_a_permutation():
    out = [0, 1, 2, 3, 4, 5]
    out = assign(out, 0, 5)                 # FL -> output 6: VR takes FL's old output 1
    assert out == [5, 1, 2, 3, 4, 0]
    out = assign(out, 2, 2)                 # no change
    assert out == [5, 1, 2, 3, 4, 0]
    for i, o in [(1, 3), (4, 0), (5, 4)]:
        out = assign(out, i, o)
        assert sorted(out) == list(range(6))
