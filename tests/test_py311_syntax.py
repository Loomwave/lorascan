"""lorascan targets Debian 12 (Python 3.11): every module must compile under a real 3.11 interpreter.
0.1.17 shipped an f-string with a backslash inside the expression (3.12-only) and `report` crashed on the
bench; ast.parse(feature_version=(3, 11)) does NOT catch PEP 701 forms, so this test needs the interpreter."""
import glob, os, re, shutil, subprocess, sys
import pytest

PKG = os.path.join(os.path.dirname(__file__), "..", "lorascan")
FILES = sorted(glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True))
PY311 = shutil.which("python3.11")


@pytest.mark.skipif(PY311 is None, reason="python3.11 not installed here; the bench deploy runs compileall under 3.11")
def test_package_compiles_under_python_3_11():
    r = subprocess.run([PY311, "-m", "compileall", "-q", PKG], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


_FSTRING_BACKSLASH = re.compile(r"""f["'][^"'\n]*\{[^}\n]*\\""")


@pytest.mark.parametrize("path", FILES, ids=[os.path.relpath(p, os.path.join(PKG, "..")) for p in FILES])
def test_no_backslash_inside_fstring_expressions(path):
    """Static approximation of the 3.11 rule that bit us (PEP 701 relaxed it in 3.12)."""
    with open(path) as f:
        for n, line in enumerate(f, 1):
            assert not _FSTRING_BACKSLASH.search(line), f"{path}:{n}: backslash inside an f-string expression is Python 3.12-only"
