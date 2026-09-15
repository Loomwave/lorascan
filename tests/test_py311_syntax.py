"""lorascan targets Debian 12 (Python 3.11): every module must compile under a real 3.11 interpreter.
0.1.17 shipped an f-string with a backslash inside the expression and the 0.1.13 map page nested an f-string
that reused its own quote — both 3.12-only (PEP 701); `report` and the map page raised SyntaxError on the bench.
ast.parse(feature_version=(3, 11)) does NOT catch PEP 701 forms, so: a real interpreter when present, plus a
static check of every f-string replacement field for the enclosing quote character or a backslash."""
import ast, glob, os, shutil, subprocess, sys
import pytest

PKG = os.path.join(os.path.dirname(__file__), "..", "lorascan")
FILES = sorted(glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True))
PY311 = shutil.which("python3.11")


@pytest.mark.skipif(PY311 is None, reason="python3.11 not installed here; release2.sh runs compileall under the bench's 3.11")
def test_package_compiles_under_python_3_11():
    r = subprocess.run([PY311, "-m", "compileall", "-q", PKG], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def fstring_field_violations(src: str) -> list:
    """(lineno, token) for every nested string literal inside an f-string replacement field that contains a
    backslash or one of the enclosing f-strings' quote characters — legal only since Python 3.12 (PEP 701).
    Uses the 3.12 tokenizer's FSTRING_* tokens, so the check itself needs Python >= 3.12."""
    import io, tokenize
    out, stack = [], []          # stack of enclosing f-string quote strings ('"', "'", '"""', "\'\'\'")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.FSTRING_START:
            q = tok.string.lstrip("fFrRbB")
            if stack and (any(eq[0] == q[0] for eq in stack)):
                out.append((tok.start[0], tok.string))
            stack.append(q)
        elif tok.type == tokenize.FSTRING_END:
            stack.pop()
        elif stack and tok.type == tokenize.STRING:
            if "\\" in tok.string or any(eq[0] in tok.string for eq in stack):
                out.append((tok.start[0], tok.string[:80]))
    return out


@pytest.mark.skipif(sys.version_info < (3, 12), reason="the tokenizer-based check needs 3.12's FSTRING tokens; the interpreter test covers 3.11")
@pytest.mark.parametrize("path", FILES, ids=[os.path.relpath(p, os.path.join(PKG, "..")) for p in FILES])
def test_no_312_only_fstring_fields(path):
    with open(path) as f:
        v = fstring_field_violations(f.read())
    assert not v, f"{path}: 3.12-only f-string fields: {v}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="needs 3.12 tokenizer")
def test_checker_catches_the_two_shipped_forms_and_passes_legal_code():
    bad1 = '''x = f"<tr{' class=\\"excluded\\"' if c else ''}>"\n'''
    bad2 = '''x = f"a {', '.join(f'{q['mhz']:.1f}' for q in qs)} b"\n'''
    ok = '''x = f"a {', '.join(fmt(q) for q in qs)} {d['k']} {n:.1f} b"\ny = f'{d["k"]}'\n'''
    assert fstring_field_violations(bad1) and fstring_field_violations(bad2) and not fstring_field_violations(ok)
