import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ndgcode import NextDraw
from ndgcode.backends import NullBackend
from ndgcode.plot import (Layer, _cut, _user_units_per_mm, parse_dist_mm, parse_plob, read_resume,
                          real_nextdraw, strip_data, write_resume)

SVG = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="100mm" height="80mm" viewBox="0 0 100 80">
 <g inkscape:groupmode="layer" inkscape:label="1 outlines +S30 +H10"><rect x="10" y="10" width="40" height="30" fill="none" stroke="black"/></g>
 <g inkscape:groupmode="layer" inkscape:label="!2 circle"><circle cx="70" cy="50" r="15" fill="none" stroke="black"/></g>
 <g inkscape:groupmode="layer" inkscape:label="% notes"><rect x="0" y="0" width="5" height="5" fill="none" stroke="black"/></g>
</svg>
"""

PLOB = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">
<g inkscape:groupmode="layer" inkscape:label="1 a +S30"><polyline points="0,0 1,0 1,1"/></g>
<g inkscape:groupmode="layer" inkscape:label="!2 b"><polyline points="2,2 3,2"/><polyline points="2,3 2,3"/></g>
<g inkscape:groupmode="layer" inkscape:label="__digest-root__"/></svg>"""


class TestPieces(unittest.TestCase):
    def test_layer_codes(self):
        l = Layer("!3 red +S40 +H15 +D500", [])
        self.assertTrue(l.pause)
        self.assertEqual((l.speed, l.height, l.delay_ms), (40, 15, 500))
        self.assertTrue(Layer("% doc", []).skip)

    def test_parse_plob(self):
        layers = parse_plob(PLOB)
        self.assertEqual([l.label for l in layers], ["1 a +S30", "!2 b"])
        self.assertEqual(layers[0].strokes[0][1], (25.4, 0.0))
        dot = layers[1].strokes[1]
        self.assertEqual(len(dot), 2)
        self.assertAlmostEqual(dot[0][1], 76.2)
        self.assertEqual(dot[0], dot[1])

    def test_dist(self):
        self.assertAlmostEqual(parse_dist_mm(1), 25.4)
        self.assertAlmostEqual(parse_dist_mm("127mm"), 127)
        self.assertAlmostEqual(parse_dist_mm("5cm"), 50)
        self.assertAlmostEqual(parse_dist_mm("2in"), 50.8)

    def test_resume_roundtrip(self):
        svg = write_resume(SVG, {"res_dist_mm": "12.500"})
        self.assertEqual(read_resume(svg)["res_dist_mm"], "12.500")
        svg2 = write_resume(svg, {"res_dist_mm": "20.000"})
        self.assertEqual(svg2.count("ndgcode:plotdata"), 1)
        self.assertIsNone(read_resume(strip_data(svg2)))

    def test_cut(self):
        self.assertEqual(_cut([(0, 0), (10, 0), (10, 10)], 15), [(10, 5), (10, 10)])

    def test_units(self):
        self.assertAlmostEqual(_user_units_per_mm(SVG), 1.0)
        self.assertAlmostEqual(_user_units_per_mm('<svg width="8.5in" viewBox="0 0 8.5 11">'), 1 / 25.4)


@unittest.skipIf(real_nextdraw() is None, "the nextdraw library is not installed")
class TestPlotContext(unittest.TestCase):
    def _nd(self, svg=SVG):
        nd = NextDraw()
        be = NullBackend()
        nd.options.port = be
        nd.plot_setup(svg)
        return nd, be

    def run_quiet(self, nd, output=False):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return nd.plot_run(output)

    def test_plot_pauses_at_bang_layer_and_resumes(self):
        nd, be = self._nd()
        out = self.run_quiet(nd, True)
        self.assertAlmostEqual(nd.res_dist, 140.0, delta=0.5)        # rectangle perimeter
        self.assertEqual(read_resume(out)["res_dist_mm"][:3], "140")
        downs = [l for l in be.lines if l.startswith("G1") and "F" in l]
        self.assertTrue(any(l.endswith("F1000") or l.endswith("F750") for l in downs))
        self.assertIn("M3 S900", be.lines)                           # +H10 -> S900
        # resume: draws only the circle
        nd2 = NextDraw()
        be2 = NullBackend()
        nd2.options.port = be2
        nd2.plot_setup(out)
        nd2.options.mode = "res_plot"
        out2 = self.run_quiet(nd2, True)
        self.assertEqual(nd2.res_dist, 0.0)
        self.assertIsNone(read_resume(out2))
        self.assertAlmostEqual(nd2.distance_pendown * 1000, 140 + 2 * 3.14159 * 15, delta=2)

    def test_layers_mode(self):
        nd, be = self._nd()
        nd.options.mode = "layers"
        nd.options.layer = 2
        self.run_quiet(nd)
        self.assertAlmostEqual(nd.distance_pendown * 1000, 2 * 3.14159 * 15, delta=1)

    def test_preview_estimates_without_output(self):
        nd, be = self._nd()
        nd.options.preview = True
        nd.options.mode = "layers"
        nd.options.layer = 1
        out = self.run_quiet(nd, True)
        self.assertGreater(nd.time_estimate, 1.0)
        self.assertIn('id="ndgcode-preview"', out)

    def test_digest_passthrough(self):
        nd, be = self._nd()
        nd.options.digest = 2
        out = self.run_quiet(nd, True)
        self.assertIn("polyline", out)
        self.assertEqual(be.lines, [])

    def test_offset_start(self):
        nd, be = self._nd()
        nd.options.mode = "layers"
        nd.options.layer = 1
        nd.options.offset_start = True
        nd.options.dist = "100mm"
        self.run_quiet(nd)
        # 140 mm rectangle, started 100 mm in: reported from the document start
        self.assertAlmostEqual(nd.distance_pendown * 1000, 140, delta=0.5)
        drawn = sum(1 for l in be.lines if l == "M3 S900")    # layer 1 has +H10
        self.assertEqual(drawn, 1)

    def test_copies(self):
        nd, be = self._nd()
        nd.options.mode = "layers"
        nd.options.layer = 1
        nd.options.copies = 2
        nd.options.page_delay = 0
        self.run_quiet(nd)
        self.assertAlmostEqual(nd.distance_pendown * 1000, 280, delta=1)

    def test_utilities(self):
        nd, be = self._nd()
        nd.options.mode = "utility"
        nd.options.utility_cmd = "res_adj_mm"
        nd.options.dist = 25
        out = self.run_quiet(nd, True)
        self.assertEqual(nd.res_dist, 25.0)
        nd2 = NextDraw()
        nd2.plot_setup(out)
        nd2.options.mode = "utility"
        nd2.options.utility_cmd = "res_read"
        self.run_quiet(nd2)
        self.assertEqual(nd2.res_dist, 25.0)
        nd2.options.utility_cmd = "strip_data"
        self.assertNotIn("ndgcode:plotdata", self.run_quiet(nd2, True))

    def test_webhook_posts_json(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        got = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                got.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.handle_request, daemon=True)
        th.start()
        try:
            nd, be = self._nd()
            nd.options.mode = "layers"
            nd.options.layer = 2
            nd.options.webhook = True
            nd.options.webhook_url = f"http://127.0.0.1:{srv.server_port}/"
            self.run_quiet(nd)
            th.join(timeout=5)
        finally:
            srv.server_close()
        self.assertEqual(set(got), {"value1", "value2", "value3"})

    def test_cycle_on_fake_board(self):
        nd = NextDraw()
        nd.options.port = "fake:"
        nd.plot_setup()
        nd.options.mode = "cycle"
        self.run_quiet(nd)
        self.assertEqual(nd.errors.code, 0)

    def test_write_and_read_name_on_fake_board(self):
        nd = NextDraw()
        nd.options.port = "fake:"
        nd.plot_setup()
        nd.options.mode = "utility"
        nd.options.utility_cmd = "write_namevgx4"
        self.run_quiet(nd)
        self.assertEqual(nd.nickname, "vgx4")

    def test_file_svg_and_export(self):
        with tempfile.TemporaryDirectory() as d:
            svg = os.path.join(d, "in.svg")
            out = os.path.join(d, "out.gcode")
            with open(svg, "w") as fh:
                fh.write(SVG)
            nd = NextDraw()
            nd.plot_setup(svg)
            nd.options.mode = "layers"
            nd.options.layer = 2
            nd.options.port = "file:" + out
            self.run_quiet(nd)
            with open(out) as fh:
                text = fh.read()
        self.assertIn("M3 S1000", text)
        self.assertTrue(text.rstrip().endswith("M5"))


if __name__ == "__main__":
    unittest.main()
