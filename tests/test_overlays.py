"""Turning off overlays must suppress legacy labels without losing audio/video."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from app.timeline import build_timeline
from scripts.render_project import render_project
from tests.test_timeline import available_renderer


class OverlayTests(unittest.TestCase):
    @unittest.skipUnless(available_renderer(), 'Shotcut/FFmpeg required')
    def test_disabled_overlays_survive_native_export_and_portable_timeline(self):
        with tempfile.TemporaryDirectory(prefix='studio_no_labels_') as folder:
            root=Path(folder)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=320x180:r=30:d=2',
                            '-c:v','libx264','-pix_fmt','yuv420p',str(root/'motion.mp4')],check=True)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2',
                            str(root/'voice.wav')],check=True)
            segment={'id':'one','text':'Narration','title':'DO NOT DISPLAY',
                     'visual':'motion.mp4','audio':'voice.wav','duration':2.0}
            result=build_timeline([segment],'voice.wav',root/'build',base_dir=root,width=320,height=180,
                                 heading='HIDDEN HEADING',footer='HIDDEN FOOTER',overlays_enabled=False)
            tree=ET.parse(result['project_mlt'])
            self.assertEqual(len(tree.findall('.//tractor/track')),4)
            self.assertFalse(tree.findall(".//filter/property[@name='mlt_service'][.='dynamictext']"))
            self.assertIsNone(tree.find(".//playlist[@id='titles']"))
            self.assertEqual(len(tree.findall(".//playlist[@id='narrator']/entry")),1)
            saved=json.loads(Path(result['timeline_json']).read_text(encoding='utf-8'))
            self.assertIs(saved['overlays_enabled'],False)
            exported=render_project(result['project_mlt'],preset='ultrafast',timeout=90)
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',exported['output']]))
            self.assertEqual({s['codec_type'] for s in probe['streams']},{'audio','video'})
            frames=subprocess.check_output(['ffmpeg','-v','error','-i',exported['output'],'-an','-f','framemd5','-']).decode()
            hashes=[line.rsplit(',',1)[-1].strip() for line in frames.splitlines() if not line.startswith('#')]
            self.assertEqual(len(hashes),60)
            self.assertGreater(len(set(hashes)),40)


if __name__=='__main__':unittest.main()
