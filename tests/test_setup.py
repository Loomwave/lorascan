from lorascan import setup

def test_scripted_io_consumes_answers():
    io = setup.ScriptedIO(["yes", "2", "hello"])
    assert io.confirm("ok?") is True
    assert io.choose("pick", ["a", "b", "c"]) == 1     # "2" -> index 1
    assert io.ask("name?") == "hello"

def test_run_stops_and_returns_nonzero_on_quit():
    # a wizard whose first step asks to continue; scripted "quit"
    io = setup.ScriptedIO(["quit"])
    w = setup.Wizard(io, setup.Runner(), home="/tmp")
    # a one-step run for the framework test: inject a single failing fail-closed step
    w._steps = [lambda ww: setup.StepResult(False, "blocked", "fix me")]
    rc = setup.run(w)
    assert rc != 0
    assert any("not configured" in m for m in io.said)

def test_run_returns_zero_when_all_steps_ok():
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, setup.Runner(), home="/tmp")
    w._steps = [lambda ww: setup.StepResult(True, "did a thing")]
    assert setup.run(w) == 0
