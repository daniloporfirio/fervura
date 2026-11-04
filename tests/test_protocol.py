import unittest

from fervura.protocol import FRAME_SIZE, Readings, build_frame, parse_frame


class ProtocolTest(unittest.TestCase):
    def test_frame_layout(self):
        r = Readings(cpu_temp=0x37, cpu_fan=0x04B0, cpu_usage=0x1E, cpu_clock=0x1194,
                     gpu_usage=0x14, gpu_clock=0x0708, gpu_mem_used=0x00001000,
                     gpu_mem_total=0x00002000, gpu_fan=0x03E8, gpu_temp=0x3C, ram_usage=0x2D)
        frame = build_frame(r, show_fahrenheit=True)
        self.assertEqual(len(frame), FRAME_SIZE)
        plain = bytes([0x74, 0x01, 0x37, 0x04, 0xB0, 0x1E, 0x11, 0x94, 0x14, 0x07, 0x08,
                       0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x20, 0x00, 0x03, 0xE8, 0x3C, 0x2D])
        expected = plain[:2] + bytes(b ^ 0x02 for b in plain[2:])
        self.assertEqual(frame, expected)

    def test_roundtrip_and_clamping(self):
        frame = build_frame(Readings(cpu_temp=300, cpu_fan=-5, gpu_clock=1799.6))
        r, fah = parse_frame(frame)
        self.assertFalse(fah)
        self.assertEqual((r.cpu_temp, r.cpu_fan, r.gpu_clock), (255, 0, 1800))


if __name__ == "__main__":
    unittest.main()
