"""自包含静态家具 GLB / 俯视 PNG 协议校验；仅使用 Python 标准库。"""
import base64
import binascii
import json
import math
import re
import struct
import zlib

MAX_GLB_BYTES = 12 * 1024 * 1024
MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_TRIANGLES = 250000


def require(ok, message):
    if not ok:
        raise ValueError(message)


def decode_data_url(value, mime, max_bytes):
    prefix = 'data:' + mime + ';base64,'
    require(isinstance(value, str) and value.startswith(prefix), '资产需要内嵌 ' + mime)
    encoded = value[len(prefix):]
    require(0 < len(encoded) <= (max_bytes + 2) // 3 * 4, '内嵌资产为空或超过容量上限')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('资产 base64 无效') from exc
    require(0 < len(data) <= max_bytes, '内嵌资产为空或超过容量上限')
    return data


def inspect_glb(data):
    require(len(data) >= 28, 'GLB 过短')
    magic, version, total = struct.unpack_from('<4sII', data)
    require(magic == b'glTF' and version == 2 and total == len(data), 'GLB 头或长度无效')
    chunks = []
    offset = 12
    while offset < total:
        require(offset + 8 <= total, 'GLB 分块头越界')
        size, kind = struct.unpack_from('<II', data, offset)
        offset += 8
        require(size % 4 == 0 and offset + size <= total, 'GLB 分块长度无效')
        chunks.append((kind, data[offset:offset + size]))
        offset += size
    require(len(chunks) == 2 and [c[0] for c in chunks] == [0x4E4F534A, 0x004E4942], '仅支持内嵌 JSON/BIN 的 GLB')
    try:
        doc = json.loads(chunks[0][1].decode('utf8').rstrip(' \x00'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('GLB JSON 无效') from exc
    binary = chunks[1][1]
    require(isinstance(doc, dict) and doc.get('asset', {}).get('version') == '2.0', '需要 glTF 2.0')
    require(not doc.get('extensionsUsed') and not doc.get('extensionsRequired'), '首版家具资产不支持 glTF 扩展或外部解码器')
    require(not doc.get('skins') and not doc.get('animations'), '首版仅支持静态家具网格')
    buffers = doc.get('buffers', [])
    require(len(buffers) == 1 and 'uri' not in buffers[0] and isinstance(buffers[0].get('byteLength'), int) and 0 < buffers[0]['byteLength'] <= len(binary), 'GLB 缓冲区需全部内嵌')
    views = doc.get('bufferViews', [])
    for view in views:
        begin, length = view.get('byteOffset', 0), view.get('byteLength')
        require(view.get('buffer', 0) == 0 and isinstance(begin, int) and isinstance(length, int) and begin >= 0 and length > 0 and begin + length <= buffers[0]['byteLength'], 'GLB bufferView 越界')
    for image in doc.get('images', []):
        require('uri' not in image and image.get('mimeType') in ('image/png', 'image/jpeg') and isinstance(image.get('bufferView'), int) and 0 <= image['bufferView'] < len(views), 'GLB 贴图必须内嵌 PNG/JPEG')
    identity = {'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], 'translation': [0, 0, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}
    nodes = doc.get('nodes', [])
    for node in nodes:
        require(isinstance(node, dict) and 'skin' not in node, '节点无效或含骨骼')
        for key, values in identity.items():
            actual = node.get(key, values)
            require(isinstance(actual, list) and len(actual) == len(values) and all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and abs(v - target) < 1e-8 for v, target in zip(actual, values)), '家具GLB必须烘焙节点变换，请运行prepare')
    scenes = doc.get('scenes', [])
    selected_scene = doc.get('scene', 0)
    require(isinstance(selected_scene, int) and 0 <= selected_scene < len(scenes), 'GLB缺少有效活动场景')
    used_meshes = set()
    def walk_node(index, ancestors):
        require(isinstance(index, int) and 0 <= index < len(nodes) and index not in ancestors, 'GLB场景节点引用无效或成环')
        node = nodes[index]
        if 'mesh' in node:
            mi = node['mesh']
            require(isinstance(mi, int) and 0 <= mi < len(doc.get('meshes', [])), 'GLB节点网格引用无效')
            used_meshes.add(mi)
        for child in node.get('children', []):
            walk_node(child, ancestors | {index})
    for index in scenes[selected_scene].get('nodes', []):
        walk_node(index, set())
    require(used_meshes == set(range(len(doc.get('meshes', [])))), 'GLB含未加入活动场景的网格，请运行prepare')
    accessors = doc.get('accessors', [])
    sizes = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
    dims = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
    def accessor(i):
        require(isinstance(i, int) and 0 <= i < len(accessors), 'GLB accessor 引用无效')
        acc = accessors[i]
        require('sparse' not in acc and acc.get('componentType') in sizes and acc.get('type') in dims and isinstance(acc.get('count'), int) and 0 < acc['count'] <= MAX_TRIANGLES * 3, 'GLB accessor 格式/数量不受支持')
        vi = acc.get('bufferView')
        require(isinstance(vi, int) and 0 <= vi < len(views), 'GLB accessor 缺少有效 bufferView')
        view = views[vi]
        count, size = acc['count'], sizes[acc['componentType']] * dims[acc['type']]
        stride, offset = view.get('byteStride', size), acc.get('byteOffset', 0)
        require(isinstance(offset, int) and isinstance(stride, int) and offset >= 0 and stride >= size and offset + (count - 1) * stride + size <= view['byteLength'], 'GLB accessor 数据越界')
        return acc, view.get('byteOffset', 0) + offset, stride
    bounds = [[math.inf] * 3, [-math.inf] * 3]
    triangles = 0
    for mesh in doc.get('meshes', []):
        for primitive in mesh.get('primitives', []):
            require(primitive.get('mode', 4) == 4 and not primitive.get('targets'), '仅支持静态三角网格')
            acc, begin, stride = accessor(primitive.get('attributes', {}).get('POSITION'))
            require(acc['componentType'] == 5126 and acc['type'] == 'VEC3', 'POSITION 需要 float32 VEC3')
            for j in range(acc['count']):
                pos = struct.unpack_from('<3f', binary, begin + j * stride)
                require(all(math.isfinite(v) and abs(v) <= 100000 for v in pos), 'GLB 顶点无效')
                for axis, value in enumerate(pos):
                    bounds[0][axis] = min(bounds[0][axis], value)
                    bounds[1][axis] = max(bounds[1][axis], value)
            if 'indices' in primitive:
                ind, start, step = accessor(primitive['indices'])
                require(ind['type'] == 'SCALAR' and ind['componentType'] in (5121, 5123, 5125) and ind['count'] % 3 == 0, '三角网格索引无效')
                fmt = {5121: '<B', 5123: '<H', 5125: '<I'}[ind['componentType']]
                require(all(struct.unpack_from(fmt, binary, start + j * step)[0] < acc['count'] for j in range(ind['count'])), 'GLB 索引越界')
                triangles += ind['count'] // 3
            else:
                require(acc['count'] % 3 == 0, '非索引三角网格顶点数无效')
                triangles += acc['count'] // 3
            for attribute in primitive.get('attributes', {}).values():
                accessor(attribute)
    require(0 < triangles <= MAX_TRIANGLES, '家具三角面为空或超过250000上限')
    require(all(bounds[1][i] > bounds[0][i] for i in range(3)), '家具需有非零宽深高')
    return {'triangles': triangles, 'bounds': bounds, 'bytes': len(data), 'document': doc}


def inspect_png(data):
    require(data[:8] == b'\x89PNG\r\n\x1a\n', '家具俯视PNG签名无效')
    offset, width, height, channels = 8, None, None, None
    compressed = bytearray()
    ended = False
    while offset < len(data):
        require(offset + 12 <= len(data), 'PNG分块截断')
        length = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset + 4:offset + 8]
        require(offset + 12 + length <= len(data), 'PNG分块越界')
        payload = data[offset + 8:offset + 8 + length]
        checksum = struct.unpack_from('>I', data, offset + 8 + length)[0]
        require((zlib.crc32(kind + payload) & 0xffffffff) == checksum, 'PNG分块CRC无效')
        if kind == b'IHDR':
            require(offset == 8 and width is None and length == 13, 'PNG IHDR无效')
            width, height, bits, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            require(1 <= width <= 2048 and 1 <= height <= 2048, '家具俯视图尺寸需在1～2048像素')
            require(bits == 8 and color in (2, 6) and compression == 0 and filtering == 0 and interlace == 0, '俯视图需要非交错8位RGB/RGBA PNG，请运行prepare')
            channels = 3 if color == 2 else 4
        elif kind == b'IDAT':
            require(width is not None, 'PNG缺少IHDR')
            compressed.extend(payload)
        elif kind == b'IEND':
            require(length == 0 and offset + 12 == len(data), 'PNG IEND无效')
            ended = True
            break
        else:
            require(kind[:1].islower() or kind == b'PLTE', 'PNG含不支持的关键分块')
        offset += 12 + length
    require(ended and compressed and channels is not None, 'PNG缺少完整IDAT/IEND')
    expected = height * (1 + width * channels)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as exc:
        raise ValueError('PNG像素解压失败') from exc
    require(decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data and len(raw) == expected, 'PNG像素数据长度无效')
    require(all(raw[y * (1 + width * channels)] <= 4 for y in range(height)), 'PNG行过滤器无效')
    return width, height


def validate_asset(asset, key=None):
    require(isinstance(asset, dict), '家具资产必须为对象')
    aid = asset.get('id')
    require(isinstance(aid, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', aid) is not None, '家具资产 id 无效')
    require(key is None or key == aid, '家具资产表 key 必须等于 id')
    require(asset.get('format') == 'glb', '家具资产格式需要 glb')
    h = asset.get('defaultHeightMm')
    require(isinstance(h, (int, float)) and not isinstance(h, bool) and math.isfinite(h) and 1 <= h <= 10000, '家具资产默认高度需为1～10000mm')
    provenance = asset.get('provenance')
    require(isinstance(provenance, dict) and isinstance(provenance.get('method'), str) and bool(provenance['method'].strip()) and isinstance(provenance.get('license'), str) and bool(provenance['license'].strip()), '家具资产需记录方法与许可（未知请如实填写unknown）')
    digest = provenance.get('sourceImageSha256')
    require(isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) is not None, '家具资产需记录输入图SHA256')
    report = inspect_glb(decode_data_url(asset.get('dataUrl'), 'model/gltf-binary', MAX_GLB_BYTES))
    target = [[-.5, 0, -.5], [.5, 1, .5]]
    require(all(abs(report['bounds'][j][i] - target[j][i]) < .002 for j in range(2) for i in range(3)), '家具GLB需要预处理到X/Z±0.5、Y0～1')
    png = decode_data_url(asset.get('topViewDataUrl'), 'image/png', MAX_PNG_BYTES)
    width, height = inspect_png(png)
    return {'id': aid, 'triangles': report['triangles'], 'bounds': report['bounds'], 'glbBytes': report['bytes'], 'topViewSize': [width, height], 'topViewBytes': len(png)}
