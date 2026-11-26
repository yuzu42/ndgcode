import unittest

from ndgcode import errors
from ndgcode.fakegrbl import factory
from ndgcode.grbl import SerialBackend, parse_status


def backend(**kw):
    fk = kw.pop("fake", {})
    f = factory(**fk)
    be = SerialBackend("fake", transport_factory=f, banner_timeout=1.0, **kw)
    be.open()
    return be, f.holder["last"]


class TestParse(unittest.TestCase):
    def test_status(self):
        st = parse_status("<Idle|MPos:10.000,-5.000,0.000|FS:0,550|Ov:100,100,100|A:S>")
        self.assertEqual(st["state"], "Idle")
        self.assertEqual(st["MPos"], (10.0, -5.0, 0.0))
        self.assertEqual(st["FS"], (0.0, 550.0))
        self.assertEqual(st["Ov"], (100, 100, 100))

    def test_hold_substate(self):
        st = parse_status("<Hold:0|MPos:0,0,0|FS:0,0>")
        self.assertEqual(st["state"], "Hold")
        self.assertEqual(st["substate"], "Hold:0")

    def test_wpos_from_wco(self):
        st = parse_status("<Idle|MPos:10,10,0|FS:0,0|WCO:2,3,0>")
        self.assertEqual(st["WPos"], (8, 7, 0))


class TestSerialBackend(unittest.TestCase):
    def test_open_reads_identity(self):
        be, fake = backend(fake={"name": "vgx4"})
        try:
            self.assertEqual(be.fw_version, "1.1f.20170131")
            self.assertEqual(be.nickname, "vgx4")
            self.assertEqual(be.build_info, "VC,15,128")
        finally:
            be.close()

    def test_soft_reset_when_no_banner_on_open(self):
        be, fake = backend(fake={"reset_on_open": False})
        try:
            self.assertIn(b"\x18", fake.realtime_log)
        finally:
            be.close()

    def test_no_board(self):
        def bad(port, baud):
            raise OSError("no such port")
        be = SerialBackend("/dev/none", transport_factory=bad)
        with self.assertRaises(errors.ConnectError):
            be.open()

    def test_character_counting_never_overflows(self):
        be, fake = backend(fake={"lines_per_read": 1})
        try:
            for i in range(300):
                be.send(f"G1 X{i % 50}.123 Y-{i % 37}.456 F1000")
            be.wait_idle(timeout=10)
            self.assertFalse(fake.overflow)
            self.assertLessEqual(fake.max_rx, 128)
            self.assertGreater(fake.max_rx, 60)   # it did fill the buffer
            self.assertEqual(fake.mpos[0], 299 % 50 + 0.123)
        finally:
            be.close()

    def test_simple_mode(self):
        be, fake = backend(mode="simple")
        try:
            for i in range(20):
                be.send(f"G1 X{i} F1000")
            be.wait_idle(timeout=5)
            self.assertLessEqual(fake.max_rx, 20)
        finally:
            be.close()

    def test_settings_and_query(self):
        be, fake = backend()
        try:
            s = be.settings()
            self.assertEqual(s[110], "5000.000")
            be.query("$120=300.000")
            self.assertEqual(be.settings()[120], "300.000")
        finally:
            be.close()

    def test_streamed_error_raises_later(self):
        be, fake = backend()
        try:
            be.send("G1 X1 F1000")
            be.send("G99")
            with self.assertRaises(errors.GrblError) as cm:
                be.wait_idle(timeout=5)
                be.send("G1 X2")
            self.assertEqual(cm.exception.num, 20)
            self.assertEqual(cm.exception.line, "G99")
        finally:
            be.close()

    def test_query_error(self):
        be, fake = backend()
        try:
            with self.assertRaises(errors.GrblError):
                be.query("$H")     # homing disabled ($22=0)
        finally:
            be.close()

    def test_unexpected_reset_detected(self):
        be, fake = backend()
        try:
            with fake._lock:
                fake.out += b"\r\nVigoWriter 1.1f ['$' for help]\r\n"
            with self.assertRaises(errors.ResetError):
                be.wait_idle(timeout=2)
                be.send("G1 X1 F100")
        finally:
            be.close()

    def test_expected_soft_reset(self):
        be, fake = backend()
        try:
            be.send("G1 X10 F1000")
            be.wait_idle(timeout=2)
            be.soft_reset()
            be.send("G1 X1 F1000")
            be.wait_idle(timeout=2)
        finally:
            be.close()

    def test_disconnect(self):
        be, fake = backend()
        try:
            fake.fail_next_write = OSError("unplugged")
            with self.assertRaises(errors.DisconnectError):
                be.send("G1 X1 F100")
        finally:
            be.close()

    def test_sleep(self):
        be, fake = backend()
        try:
            be.wait_idle(timeout=2)
            be.query("$SLP")
            self.assertEqual(be.status()["state"], "Sleep")
        finally:
            be.close()


if __name__ == "__main__":
    unittest.main()
