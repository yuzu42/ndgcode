import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ndgcode import NextDraw
from ndgcode.backends import NullBackend
from ndgcode.calibrate import calibrate_bed, calibrate_pen
from ndgcode.cli import Shell, main
from ndgcode.fakegrbl import factory
from ndgcode.grbl import SerialBackend
from ndgcode.runner import run_script
from ndgcode.stream import check, clean_lines, stream


def connected(port=None, **opts):
    nd = NextDraw()
    nd.interactive()
    nd.options.units = 2
    nd.options.port = port or NullBackend()
    for k, v in opts.items():
        setattr(nd.options if hasattr(nd.options, k) else nd.params, k, v)
    with redirect_stderr(io.StringIO()):
        assert nd.connect()
    return nd


class Scripted:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        return self.answers.pop(0)


class TestCalibration(unittest.TestCase):
    def test_pen(self):
        nd = connected()
        # Enter (pen inserted), touching at 950/900/850, off at 800, then OK
        ask = Scripted(["", "y", "y", "y", "n", ""])
        pct = calibrate_pen(nd, ask=ask, say=lambda m: None)
        self.assertEqual(pct, 30.0)                  # 800 + 100 margin -> S700
        self.assertEqual(nd.options.pen_pos_up, 30.0)
        self.assertEqual(nd.machine.st.s_up, 700)
        self.assertIn("M3 S800", nd.backend.lines)

    def test_pen_adjust(self):
        nd = connected()
        ask = Scripted(["", "n", "+", "+", ""])     # off at 950 -> 850, then +50 -> 800
        pct = calibrate_pen(nd, ask=ask, say=lambda m: None)
        self.assertEqual(nd.machine.st.s_up, 800)
        self.assertEqual(pct, 20.0)

    def test_bed(self):
        nd = connected(travel_x_mm=200, travel_y_mm=100)
        # 2x2 grid: off at 950, 900, 850, 800 -> s_up 850, 800, 750, 700
        ask = Scripted(["n", "y", "n", "y", "y", "n", "y", "y", "y", "n"])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bed.json")
            hm = calibrate_bed(nd, ask=ask, say=lambda m: None, nx=2, ny=2, save_to=path)
            with open(path) as fh:
                saved = json.load(fh)
        self.assertEqual(hm.s, [[850, 800], [750, 700]])
        self.assertEqual(saved["xs"], [10.0, 190.0])
        self.assertIsNotNone(nd.machine.st.height_map)


class TestStream(unittest.TestCase):
    def test_clean_lines(self):
        src = ["; header", "G1 X1 (comment) Y2", "", "%", "M5 ; rest"]
        self.assertEqual(list(clean_lines(src)), [(2, "G1 X1  Y2"), (5, "M5")])

    def test_stream_and_check(self):
        f = factory()
        be = SerialBackend("fake", transport_factory=f, banner_timeout=1.0)
        be.open()
        try:
            secs = stream(be, ["G21 G90", "G1 X10 Y-10 F1000", "M3 S1000", "G4 P0.1", "M5"])
            self.assertGreaterEqual(secs, 0)
            self.assertEqual(f.holder["last"].mpos[:2], [10.0, -10.0])
            problems = check(be, ["G1 X1 F100", "G99", "M5"])
            self.assertEqual(len(problems), 1)
            self.assertEqual(problems[0][0], 2)
            be.send("G1 X2 F100")       # board usable after leaving check mode
            be.wait_idle(timeout=2)
        finally:
            be.close()


class TestShell(unittest.TestCase):
    def test_commands(self):
        nd = connected()
        sh = Shell(nd)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            sh.onecmd("square 10 20 20")
            sh.onecmd("jog 5 0")
            sh.onecmd("sethome")
            sh.onecmd("goto 1 1")
            sh.onecmd("pen 600")
            sh.onecmd("speed 10")
            sh.onecmd("goto nonsense")        # reported, not raised
        self.assertEqual(nd.current_pos(), (1.0, 1.0))
        self.assertIn("M3 S600", nd.backend.lines)
        self.assertIn("error:", out.getvalue())
        self.assertTrue(sh.onecmd("quit"))
        nd.disconnect()


class TestRunner(unittest.TestCase):
    SCRIPT = (
        "from nextdraw import NextDraw\n"
        "nd = NextDraw()\n"
        "nd.interactive()\n"
        "nd.options.units = 2\n"
        "if not nd.connect():\n"
        "    quit()\n"
        "nd.draw_path([[10, 10], [30, 10], [30, 30]])\n"
        "nd.moveto(0, 0)\n"
        "nd.disconnect()\n"
    )

    def test_run_unchanged_script_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "demo.py")
            out = os.path.join(d, "demo.gcode")
            with open(script, "w") as fh:
                fh.write(self.SCRIPT)
            with redirect_stderr(io.StringIO()):
                run_script(script, port="file:" + out)
            with open(out) as fh:
                text = fh.read()
        self.assertIn("G1 X35 Y-15", text)
        import sys
        self.assertFalse(getattr(sys.modules.get("nextdraw"), "__ndgcode_shim__", False))

    def test_cli_run_export(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "demo.py")
            out = os.path.join(d, "demo.gcode")
            with open(script, "w") as fh:
                fh.write(self.SCRIPT)
            with redirect_stderr(io.StringIO()):
                rc = main(["run", script, "--export", out])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.getsize(out) > 0)

    def test_cli_info_fake(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["info", "--fake"])
        self.assertEqual(rc, 0)
        self.assertIn("VigoWriter 1.1f", out.getvalue())


if __name__ == "__main__":
    unittest.main()
