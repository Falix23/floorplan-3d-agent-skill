"""家具照片适配输入的透明背景与无损失败边界，不下载模型或伪造推理。"""
from pathlib import Path
import sys
import tempfile
import unittest
try:
    from PIL import Image
except ImportError:
    Image = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/floorplan-image/scripts'))
from image_to_furniture import prepare_input


@unittest.skipIf(Image is None, '可选家具图片模式需Pillow；基础计划校验不依赖它')
class InputTests(unittest.TestCase):
    def test_transparent_source_is_centered_on_gray(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = Image.new('RGBA', (100, 150))
            for y in range(30, 130):
                for x in range(30, 70):
                    source.putpixel((x, y), (200, 30, 10, 255))
            source.save(root / 'source.png')
            report = prepare_input(root / 'source.png', root / 'prepared.png')
            self.assertEqual(report['backgroundMethod'], 'existing-alpha')
            self.assertEqual(report['sourceForegroundBoxPx'], [30, 30, 70, 130])
            result = Image.open(root / 'prepared.png')
            self.assertEqual(result.size, (512, 512))
            self.assertEqual(result.getpixel((0, 0)), (128, 128, 128))
            self.assertEqual(result.getpixel((256, 256)), (200, 30, 10))
            with Image.open(root / 'source.png') as original:
                self.assertEqual(original.size, (100, 150))

    def test_missing_alpha_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / 'photo.png', Path(temp) / 'prepared.png'
            Image.new('RGB', (10, 10), 'white').save(source)
            with self.assertRaisesRegex(ValueError, '透明'):
                prepare_input(source, output, background='alpha')
            self.assertFalse(output.exists())

    def test_empty_foreground_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / 'photo.png', Path(temp) / 'prepared.png'
            Image.new('RGBA', (10, 10), (0, 0, 0, 0)).save(source)
            with self.assertRaisesRegex(ValueError, '为空'):
                prepare_input(source, output)
            self.assertFalse(output.exists())

    def test_user_prepared_rgb_is_not_recropped(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / 'photo.png', Path(temp) / 'prepared.png'
            Image.new('RGB', (64, 48), (128, 128, 128)).save(source)
            report = prepare_input(source, output, background='none')
            self.assertEqual(report['backgroundMethod'], 'none-user-prepared')
            with Image.open(output) as prepared:
                self.assertEqual(prepared.size, (64, 48))


if __name__ == '__main__':
    unittest.main()
