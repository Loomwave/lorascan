"""A small YAML subset parser (no dependency): nested block mappings by indentation, scalars
(int, float, bool, null, hex ints, quoted strings), comments, and lists of scalars ('- x').
Enough for /etc/meshtasticd/*.yaml and /etc/openhop_repeater/config.yaml (Loomwave/lorascan#7)."""
from __future__ import annotations
from typing import Any


def _scalar(v: str) -> Any:
    v = v.strip()
    if v == "" or v in ("~", "null", "Null", "NULL"):
        return None
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(v, 0) if low.startswith(("0x", "-0x")) else int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _strip(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch; out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def parse_yaml(text: str) -> dict:
    lines = []
    for raw in text.splitlines():
        s = _strip(raw)
        if s.strip():
            lines.append((len(s) - len(s.lstrip(" ")), s.strip()))
    pos = [0]

    def block(indent: int):
        node: Any = None
        while pos[0] < len(lines):
            ind, s = lines[pos[0]]
            if ind < indent:
                break
            if ind > indent:
                raise ValueError(f"yaml: unexpected indent at {s!r}")
            if s.startswith("- "):
                node = node if isinstance(node, list) else []
                node.append(_scalar(s[2:])); pos[0] += 1
                continue
            key, sep, val = s.partition(":")
            if not sep:
                raise ValueError(f"yaml: expected 'key: value' at {s!r}")
            node = node if isinstance(node, dict) else {}
            pos[0] += 1
            if val.strip():
                v = val.strip()
                if v.startswith("{") and v.endswith("}"):
                    node[key.strip()] = {k.strip(): _scalar(x) for k, _, x in (p.partition(":") for p in v[1:-1].split(",") if p.strip())}
                elif v.startswith("[") and v.endswith("]"):
                    node[key.strip()] = [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
                else:
                    node[key.strip()] = _scalar(v)
            else:
                if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                    node[key.strip()] = block(lines[pos[0]][0])
                else:
                    node[key.strip()] = None
        return node if node is not None else {}
    return block(lines[0][0]) if lines else {}
