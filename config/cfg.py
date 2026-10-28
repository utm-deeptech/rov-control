#!/usr/bin/env python3
"""Tiny config loader shared by every component.

Python:  from cfg import load; c = load()
Shell:   python3 config/cfg.py network.laptop_ip      -> prints the value
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "system.yaml")
ROV_PATH = os.path.join(HERE, "rov.yaml")          # ROV wiring set from the app (thruster outputs/reverse), in git
LOCAL_PATH = os.path.join(HERE, "local.yaml")      # per-machine overrides, not in git


def _merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None):
    path = path or os.environ.get("ROV_CONFIG") or DEFAULT_PATH
    with open(path) as f:
        c = yaml.safe_load(f)
    for extra in (ROV_PATH, LOCAL_PATH):
        if os.path.exists(extra):
            with open(extra) as f:
                _merge(c, yaml.safe_load(f))
    return c


def save_local(dotted, value, path=LOCAL_PATH):
    """Set one value in config/local.yaml (or another override file, e.g. ROV_PATH)."""
    over = {}
    if os.path.exists(path):
        with open(path) as f:
            over = yaml.safe_load(f) or {}
    node = over
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value
    with open(path, "w") as f:
        yaml.safe_dump(over, f, sort_keys=False, default_flow_style=None)


def get(cfg, dotted):
    node = cfg
    for part in dotted.split("."):
        node = node[part]
    return node


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: cfg.py section.key")
    val = get(load(), sys.argv[1])
    if isinstance(val, bool):
        val = "true" if val else "false"
    print(val)
