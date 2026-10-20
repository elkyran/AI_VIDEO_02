"""Exercise actual preparation of non-widescreen images through FFmpeg."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from app.media import camera_filter


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required for geometric media validation")
class CameraGeometryTests(unittest.TestCase):
    def test_contain_preserves_all_four_corners_of_a_portrait_panel(self):
        with tempfile.TemporaryDirectory(prefix="studio_panel_") as folder:
            width,height=100,200
            colors=((255,0,0),(0,255,0),(0,0,255),(255,255,0))
            pixels=bytes(component for y in range(height) for x in range(width)
                         for component in colors[(2 if y>=height//2 else 0)+(1 if x>=width//2 else 0)])
            source=Path(folder)/'panel.ppm'
            source.write_bytes(f'P6\n{width} {height}\n255\n'.encode('ascii')+pixels)
            output=subprocess.check_output(['ffmpeg','-v','error','-i',str(source),'-vf',
                camera_filter(1,0,width=640,height=360,fit='contain',motion='static'),
                '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
            for (x,y),expected in zip(((234,4),(405,4),(234,355),(405,355)),colors):
                actual=output[(y*640+x)*3:(y*640+x)*3+3]
                self.assertTrue(all(abs(a-b)<20 for a,b in zip(actual,expected)),(x,y,actual,expected))
            self.assertLess(max(output[:3]),40,'Portrait must retain side padding instead of horizontal stretching')

    def test_circles_stay_round_for_portrait_and_four_three_sources(self):
        with tempfile.TemporaryDirectory(prefix="studio_geometry_") as folder:
            for width, height in ((400, 300), (300, 600)):
                with self.subTest(source=(width, height)):
                    # A real round object exposes stretching that a solid color cannot.
                    pixels = bytes(255 if (x - width / 2) ** 2 + (y - height / 2) ** 2 <= 45 ** 2 else 0
                                   for y in range(height) for x in range(width))
                    source = Path(folder) / f"circle_{width}x{height}.pgm"
                    source.write_bytes(f"P5\n{width} {height}\n255\n".encode("ascii") + pixels)
                    output_width, output_height = 640, 360
                    rendered = subprocess.check_output([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source),
                        "-vf", camera_filter(1, 0, width=output_width, height=output_height) + ",format=gray",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
                    ])
                    self.assertEqual(len(rendered), output_width * output_height)
                    coordinates = [(index % output_width, index // output_width)
                                   for index, value in enumerate(rendered) if value > 128]
                    xs, ys = zip(*coordinates)
                    circle_width = max(xs) - min(xs) + 1
                    circle_height = max(ys) - min(ys) + 1
                    self.assertLess(abs(circle_width / circle_height - 1.0), .02,
                                    f"Source {width}x{height} was stretched into {circle_width}x{circle_height}")
                    self.assertGreater(min(xs), 0)
                    self.assertGreater(min(ys), 0)
                    self.assertLess(max(xs), output_width - 1)
                    self.assertLess(max(ys), output_height - 1)


if __name__ == "__main__":
    unittest.main()
