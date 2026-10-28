"""Thruster setup: which board output each thruster is on, and which ones run reversed. Editable in the app."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QVBoxLayout

# Fixed by the firmware (timer pins). Output n = PIN_NAMES[n-1].
PIN_NAMES = ["PA6", "PA7", "PB0", "PB1", "PB6", "PB7"]


def assign(outputs, i, new_out):
    """Put thruster i on output new_out (0-based). If another thruster had it, the two swap. Returns a new list."""
    out = list(outputs)
    j = out.index(new_out)
    out[i], out[j] = new_out, out[i]
    return out


class ThrusterSetup(QDialog):
    def __init__(self, names, outputs, reverse, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Thruster setup")
        self.names = names
        self.outputs = list(outputs)
        self.reverse = list(reverse)
        v = QVBoxLayout(self)
        info = QLabel("Pick the board output each thruster's ESC is plugged into, and tick Reverse if it spins the\n"
                      "wrong way. Choosing an output that is already used swaps the two. Saved in config/rov.yaml.")
        info.setObjectName("help")
        v.addWidget(info)
        g = QGridLayout()
        for col, h in enumerate(("THRUSTER", "OUTPUT / PIN", "REVERSE")):
            lab = QLabel(h)
            lab.setObjectName("tilekey")
            g.addWidget(lab, 0, col)
        self.combos, self.checks = [], []
        for i, name in enumerate(names):
            g.addWidget(QLabel(name), i + 1, 0)
            cb = QComboBox()
            for n, pin in enumerate(PIN_NAMES[:len(names)]):
                cb.addItem(f"{n + 1}  ·  {pin}", n)
            cb.activated.connect(lambda idx, i=i: self._changed(i, idx))
            g.addWidget(cb, i + 1, 1)
            ck = QCheckBox()
            ck.setChecked(bool(self.reverse[i]))
            g.addWidget(ck, i + 1, 2, alignment=Qt.AlignCenter)
            self.combos.append(cb)
            self.checks.append(ck)
        v.addLayout(g)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._refresh()

    def _changed(self, i, idx):
        self.outputs = assign(self.outputs, i, self.combos[i].itemData(idx))
        self._refresh()

    def _refresh(self):
        for cb, o in zip(self.combos, self.outputs):
            cb.setCurrentIndex(o)

    def result_values(self):
        return list(self.outputs), [ck.isChecked() for ck in self.checks]
