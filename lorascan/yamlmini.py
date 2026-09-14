"""A small YAML subset parser (no dependency): nested block mappings by indentation, scalars
(int, float, bool, null, hex ints, quoted strings), comments, lists of scalars ('- x'), lists of
mappings ('- key: value'), indentless sequences (key followed by '- x' at the same indent), flow
lists '[]' / maps '{}', and block scalars ('|' / '>', incl. tagged like '!!binary |').
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


def _flow_map(body: str) -> dict:
    d: dict[str, Any] = {}
    for part in body.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition(":")
        d[k.strip()] = _scalar(v)
    return d


def _flow_list(body: str):
    return [_scalar(x) for x in body.split(",") if x.strip()] if body.strip() else []


def parse_yaml(text: str) -> dict:
    lines = []
    for raw in text.splitlines():
        s = _strip(raw)
        if s.strip():
            lines.append((len(s) - len(s.lstrip(" ")), s.strip()))
    pos = [0]

    def mapping(indent: int) -> dict:
        node: dict[str, Any] = {}
        while pos[0] < len(lines):
            ind, s = lines[pos[0]]
            if ind < indent:
                break
            if ind > indent:
                raise ValueError(f"yaml: unexpected indent at {s!r}")
            if not s.startswith("- "):
                key, sep, val = s.partition(":")
                if not sep:
                    raise ValueError(f"yaml: expected 'key: value' at {s!r}")
                pos[0] += 1
                node[key.strip()] = _value(ind, key.strip(), val.strip())
            else:
                # a list at this indent — attach to the last key that is empty?
                # In YAML this only happens for an indentless sequence under the
                # immediately-preceding key; our mapping() callers handle that.
                break
        return node

    def seq(indent: int):
        out = []
        while pos[0] < len(lines):
            ind, s = lines[pos[0]]
            if ind != indent or not s.startswith("- "):
                break
            pos[0] += 1
            item = s[2:].strip()
            k, sep, v = item.partition(":")
            if sep:
                d: dict[str, Any] = {}
                d[k.strip()] = _value(ind, k.strip(), v.strip())
                # remaining members at deeper indent
                if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                    members = mapping(lines[pos[0]][0])
                    d.update(members)
                out.append(d)
            else:
                out.append(_scalar(item))
        return out

    def _value(ind: int, key: str, v: str) -> Any:
        if not v:
            if pos[0] < len(lines) and lines[pos[0]][0] > ind:
                return mapping(lines[pos[0]][0])
            if pos[0] < len(lines) and lines[pos[0]][0] == ind and lines[pos[0]][1].startswith("- "):
                return seq(ind)
            return None
        if v.endswith("|") or v.endswith(">"):
            parts = []
            while pos[0] < len(lines) and lines[pos[0]][0] > ind:
                parts.append(lines[pos[0]][1])
                pos[0] += 1
            return "\n".join(parts) if v.endswith("|") else " ".join(parts)
        if v.startswith("{") and v.endswith("}"):
            return _flow_map(v[1:-1])
        if v.startswith("[") and v.endswith("]"):
            return _flow_list(v[1:-1])
        return _scalar(v)

    return mapping(lines[0][0]) if lines else {}