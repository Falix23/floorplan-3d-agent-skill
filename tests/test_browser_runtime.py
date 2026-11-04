"""验证可选运行时传输的版本与目录边界，不启动浏览器或访问网络。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from browser_runtime import THREE_PREFIXES, _three_file, configure_browser_runtime


class BrowserRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = (Path(self.directory.name) / 'three').resolve()
        self.root.mkdir()
        (self.root / 'package.json').write_text(json.dumps({'name': 'three', 'version': '0.160.0'}))
        self.module = self.root / 'build/three.module.js'
        self.module.parent.mkdir()
        self.module.write_text('export const fixture = true;')

    def tearDown(self):
        self.directory.cleanup()

    def test_unset_environment_preserves_original_network(self):
        context = unittest.mock.Mock()
        with patch.dict('os.environ', {'FLOORPLAN_THREE_ROOT': ''}):
            self.assertIsNone(configure_browser_runtime(context))
        context.route.assert_not_called()

    def test_both_exact_cdns_resolve_same_package_file(self):
        for prefix in THREE_PREFIXES:
            self.assertEqual(_three_file(self.root, prefix + 'build/three.module.js'), self.module)
        with patch.dict('os.environ', {'FLOORPLAN_THREE_ROOT': str(self.root)}):
            context = unittest.mock.Mock()
            configure_browser_runtime(context)
        pattern, handler = context.route.call_args.args
        self.assertFalse(pattern.match('https://unpkg.com/three@0.161.0/build/three.module.js'))
        for prefix in THREE_PREFIXES:
            route = unittest.mock.Mock(request=SimpleNamespace(url=prefix + 'build/three.module.js'))
            handler(route)
            route.fulfill.assert_called_once_with(path=str(self.module), content_type='text/javascript')
            route.abort.assert_not_called()

    def test_wrong_version_is_rejected_before_route_registration(self):
        (self.root / 'package.json').write_text(json.dumps({'name': 'three', 'version': '0.161.0'}))
        context = unittest.mock.Mock()
        with patch.dict('os.environ', {'FLOORPLAN_THREE_ROOT': str(self.root)}):
            with self.assertRaisesRegex(ValueError, '0.160.0'):
                configure_browser_runtime(context)
        context.route.assert_not_called()

    def test_traversal_queries_and_non_module_files_are_rejected(self):
        invalid = ['build/../package.json', 'build/%2e%2e/package.json', 'package.json',
                   'build/three.module.js?module', 'build/three.module.js#fragment',
                   'examples/jsm/../../../outside.js']
        for suffix in invalid:
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                _three_file(self.root, THREE_PREFIXES[0] + suffix)

    def test_symlink_cannot_read_outside_package(self):
        outside = Path(self.directory.name) / 'private.js'
        outside.write_text('private fixture')
        (self.root / 'build/escape.js').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '越过'):
            _three_file(self.root, THREE_PREFIXES[0] + 'build/escape.js')


if __name__ == '__main__':
    unittest.main()
