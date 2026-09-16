"""The `lorascan setup` guided wizard. Pure orchestration: all console I/O and hardware/network
calls go through the IO and Runner seams so every step is unit-tested with no TTY and no hardware."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class StepResult:
    ok: bool
    summary: str
    detail: str = ""


class IO:
    def ask(self, prompt, default=None): raise NotImplementedError
    def choose(self, prompt, options): raise NotImplementedError
    def confirm(self, prompt): raise NotImplementedError
    def say(self, msg): raise NotImplementedError


class ScriptedIO(IO):
    def __init__(self, answers):
        self._a = list(answers); self.said = []
    def _next(self): return self._a.pop(0) if self._a else "quit"
    def ask(self, prompt, default=None):
        v = self._next(); return default if (v == "" and default is not None) else v
    def choose(self, prompt, options):
        v = self._next()
        return (int(v) - 1) if v.isdigit() else 0
    def confirm(self, prompt):
        return self._next().strip().lower() in ("y", "yes", "true", "1")
    def say(self, msg): self.said.append(str(msg))


class TtyIO(IO):
    def ask(self, prompt, default=None):
        s = input(f"{prompt}{' [' + str(default) + ']' if default is not None else ''}: ").strip()
        return default if (s == "" and default is not None) else s
    def choose(self, prompt, options):
        print(prompt)
        for i, o in enumerate(options, 1):
            print(f"  {i}) {o}")
        while True:
            s = input("choice: ").strip()
            if s.isdigit() and 1 <= int(s) <= len(options):
                return int(s) - 1
            print("enter a number from the list")
    def confirm(self, prompt):
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")
    def say(self, msg): print(msg)


class Runner:
    def probe(self, profile): raise NotImplementedError
    def selftest(self, profile): raise NotImplementedError
    def import_daemon(self, source, config): raise NotImplementedError
    def endpoint_health(self, url): raise NotImplementedError
    def first_upload(self, db, cfg): raise NotImplementedError
    def sweep(self, profile): raise NotImplementedError


@dataclass
class Wizard:
    io: IO
    runner: Runner
    home: str | None = None
    state: dict = field(default_factory=dict)
    _steps: list = field(default_factory=list)


def run(w: Wizard) -> int:
    # Fail-closed at every step (spec): a step that returns ok=False offers fix/quit and
    # will not advance. Steps that only "soft-fail" (endpoint unreachable, no scan DB yet,
    # first-light sweep errored) return ok=True with a caveat summary, so they never loop.
    steps = w._steps if w._steps else _default_steps()
    for step in steps:
        while True:
            res = step(w)
            w.io.say(res.summary)
            if res.ok:
                break
            if not w.io.confirm("fix this and retry?"):
                w.io.say("setup not configured; nothing was written")
                return 2
    return 0


def _default_steps():
    return []   # populated in Tasks 7-8
