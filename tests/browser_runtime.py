"""可选的同版本Three.js测试传输；不改变产品HTML或WebGL行为。

设置FLOORPLAN_THREE_ROOT为npm安装的three包根目录时启用；未设置仍使用原CDN。
仅服务精确0.160.0的公开CDN URL，文件限制在包内build/与examples/jsm/。
"""
import json
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit


THREE_VERSION = '0.160.0'
THREE_PREFIXES = (
    'https://cdn.jsdelivr.net/npm/three@0.160.0/',
    'https://unpkg.com/three@0.160.0/',
)


def _three_file(package_root, url):
    prefix = next((value for value in THREE_PREFIXES if url.startswith(value)), None)
    if prefix is None or urlsplit(url).query or urlsplit(url).fragment:
        raise ValueError('测试运行时只接受精确Three.js版本的普通模块URL')
    relative = PurePosixPath(unquote(url[len(prefix):]))
    if (relative.is_absolute() or '..' in relative.parts
            or not (relative.parts[:1] == ('build',)
                    or relative.parts[:2] == ('examples', 'jsm'))
            or relative.suffix != '.js'):
        raise ValueError('测试运行时只允许包内build/examples/jsm模块')
    path = (package_root / relative).resolve(strict=True)
    if not path.is_relative_to(package_root) or not path.is_file():
        raise ValueError('测试运行时模块不能越过Three.js包目录')
    return path


def configure_browser_runtime(context):
    """为单个Playwright context安装可选传输；无环境变量时不注册任何route。"""
    value = os.environ.get('FLOORPLAN_THREE_ROOT')
    if not value:
        return None
    root = Path(value).resolve(strict=True)
    manifest = (root / 'package.json').resolve(strict=True)
    if not manifest.is_relative_to(root):
        raise ValueError('Three.js package.json不能越过包目录')
    package = json.loads(manifest.read_text())
    if package.get('name') != 'three' or package.get('version') != THREE_VERSION:
        raise ValueError('FLOORPLAN_THREE_ROOT需要官方three@' + THREE_VERSION + '包')

    def serve(route):
        try:
            path = _three_file(root, route.request.url)
        except (OSError, ValueError):
            route.abort('blockedbyclient')
            return
        route.fulfill(path=str(path), content_type='text/javascript')

    pattern = re.compile('^(?:' + '|'.join(re.escape(p) for p in THREE_PREFIXES) + ')')
    context.route(pattern, serve)
    return {'version': THREE_VERSION, 'root': str(root)}
