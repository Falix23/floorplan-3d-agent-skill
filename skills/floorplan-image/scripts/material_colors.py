"""按明确的源材质映射适配配色；不识图、不改变几何、不修改源文件。"""
import hashlib
import io
import math
import re
from pathlib import Path


def _linear(rgb):
    import numpy as np
    rgb = np.asarray(rgb, dtype=np.float64)
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def _srgb(rgb):
    import numpy as np
    rgb = np.asarray(rgb, dtype=np.float64)
    return np.where(rgb <= .0031308, 12.92 * rgb, 1.055 * rgb ** (1 / 2.4) - .055)


def validate_material_overrides(document):
    if (not isinstance(document, dict) or type(document.get('schemaVersion')) is not int
            or document.get('schemaVersion') != 1):
        raise ValueError('材质配色需要schemaVersion为1的JSON对象')
    if set(document) - {'schemaVersion', 'materials', 'evidence', 'sourceMeshSha256'}:
        raise ValueError('材质配色包含未知字段')
    default_evidence = document.get('evidence')
    if default_evidence is not None and (not isinstance(default_evidence, str) or not default_evidence.strip()):
        raise ValueError('材质配色evidence必须为非空文字')
    digest = document.get('sourceMeshSha256')
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest)):
        raise ValueError('sourceMeshSha256需要64位十六进制哈希')
    patches = document.get('materials')
    if not isinstance(patches, list) or not patches:
        raise ValueError('材质配色materials需要至少一个显式映射')
    result = []
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) - {'index', 'name', 'color', 'textureMode', 'roughness', 'evidence'}:
            raise ValueError('材质配色项格式错误或包含未知字段')
        if ('index' in patch) == ('name' in patch):
            raise ValueError('材质选择器必须在index与name中明确填写一个')
        if 'index' in patch and (not isinstance(patch['index'], int) or isinstance(patch['index'], bool) or patch['index'] < 0):
            raise ValueError('材质index需要非负整数')
        if 'name' in patch and (not isinstance(patch['name'], str) or not patch['name'].strip()):
            raise ValueError('材质name需要非空名称')
        if not isinstance(patch.get('color'), str) or not re.fullmatch('#[0-9a-fA-F]{6}', patch['color']):
            raise ValueError('材质color需要#rrggbb格式的sRGB颜色')
        mode = patch.get('textureMode', 'luminance')
        if mode not in ('luminance', 'solid'):
            raise ValueError('textureMode仅支持luminance或solid')
        roughness = patch.get('roughness')
        if roughness is not None and (not isinstance(roughness, (int, float)) or isinstance(roughness, bool)
                                      or not math.isfinite(roughness) or not 0 <= roughness <= 1):
            raise ValueError('roughness需要0到1的有限数值')
        evidence = patch.get('evidence', default_evidence)
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError('每项材质配色需要evidence照片分区或用户色卡依据')
        item = dict(patch, color=patch['color'].lower(), textureMode=mode, evidence=evidence.strip())
        result.append(item)
    return {'schemaVersion': 1, 'materials': result, **({'sourceMeshSha256': digest.lower()} if digest else {})}


def material_groups(scene):
    """同一源文件内按节点名稳定排序，合并真正共享的材质，不用名称误合并。"""
    import trimesh
    groups, keys = [], {}
    for node in sorted(scene.graph.nodes_geometry, key=str):
        _, geometry_name = scene.graph[node]
        geometry = scene.geometry[geometry_name]
        if not isinstance(geometry, trimesh.Trimesh) or not len(geometry.faces):
            continue
        visual = geometry.visual
        material = getattr(visual, 'material', None) if visual.kind == 'texture' else None
        # 一个材质名称可对应多个不同材料；name选择器会拒绝这种歧义。
        key = ('material', id(material)) if material is not None else ('vertex', geometry_name)
        if key not in keys:
            keys[key] = len(groups)
            groups.append({'index': len(groups), 'name': getattr(material, 'name', None) or str(geometry_name),
                           'kind': 'material' if material is not None else 'vertex-color',
                           'nodes': [], 'geometries': [], '_material': material})
        group = groups[keys[key]]
        group['nodes'].append(str(node))
        if geometry_name not in group['geometries']:
            group['geometries'].append(geometry_name)
    return groups


def material_inventory(path=None, asset=None):
    import base64
    import numpy as np
    import trimesh
    if (path is None) == (asset is None):
        raise ValueError('材质枚举需要且仅需要mesh或asset一个来源')
    if asset is not None:
        from asset_validation import validate_asset
        validate_asset(asset)
        raw = base64.b64decode(asset['dataUrl'].split(',', 1)[1])
        scene = trimesh.load(io.BytesIO(raw), file_type='glb', force='scene', process=False)
        source = {'assetId': asset['id'], 'sourceKind': 'prepared-asset'}
    else:
        raw = Path(path).read_bytes()
        scene = trimesh.load(str(path), force='scene', process=False)
        source = {'sourceName': Path(path).name, 'sourceKind': 'mesh'}
    materials = []
    for group in material_groups(scene):
        material = group['_material']
        factor = getattr(material, 'baseColorFactor', None)
        color = None
        if factor is not None:
            rgb = np.asarray(factor, dtype=float)[:3] / 255
            color = '#' + ''.join('%02x' % v for v in np.rint(np.clip(_srgb(rgb), 0, 1) * 255).astype(int))
        materials.append({k: v for k, v in group.items() if not k.startswith('_')} | {
            'colorSrgb': color, 'hasBaseColorTexture': getattr(material, 'baseColorTexture', getattr(material, 'image', None)) is not None,
            'roughness': getattr(material, 'roughnessFactor', None)})
    if not materials:
        raise ValueError('来源没有可枚举的三角网格材质')
    return {'ok': True, **source, 'sourceMeshSha256': hashlib.sha256(raw).hexdigest(), 'materials': materials,
            'indexScope': '索引仅对该哈希对应的源文件有效；重新导出后重新枚举。'}


def _neutral_texture(image):
    import numpy as np
    from PIL import Image
    pixels = np.asarray(image.convert('RGBA')).copy()
    luminance = _linear(pixels[..., :3] / 255) @ np.array([.2126, .7152, .0722])
    gray = np.rint(np.clip(_srgb(luminance), 0, 1) * 255).astype(np.uint8)
    pixels[..., :3] = gray[..., None]
    return Image.fromarray(pixels, 'RGBA')


def _solid_texture(image):
    """纯色仍保留原纹理的透明孔洞；不让沙发网眼或玻璃变成实心。"""
    import numpy as np
    from PIL import Image
    if image is None:
        return None
    pixels = np.asarray(image.convert('RGBA')).copy()
    if np.all(pixels[..., 3] == 255):
        return None
    pixels[..., :3] = 255
    return Image.fromarray(pixels, 'RGBA')


def _neutral_vertex_colors(visual, count, path, mode, color_space):
    import numpy as np
    from furniture_assets import linear_vertex_colors, texture_vertex_color_multiplier
    if visual.kind == 'texture':
        colors = texture_vertex_color_multiplier(visual, count, path)
        if colors is None:
            return None
    else:
        values, _ = linear_vertex_colors(visual, count, path, color_space)
        colors = values.astype(np.float64) / 255
    colors = colors.copy()
    if mode == 'luminance':
        colors[:, :3] = (colors[:, :3] @ np.array([.2126, .7152, .0722]))[:, None]
    else:
        colors[:, :3] = 1
    # 临时源场景仍由原扩展名解释；非glTF顶点属性需按其sRGB约定储存，
    # 后续共享采样/导出器再转换一次，避免把已线性化的灰色重复解码。
    if Path(path).suffix.lower() not in ('.glb', '.gltf'):
        colors[:, :3] = _srgb(colors[:, :3])
    return np.rint(np.clip(colors, 0, 1) * 255).astype(np.uint8)


def apply_material_overrides(scene, document, path, vertex_color_space='auto'):
    """改选中的源材质副本；调用者随后从该材质同时生成GLB和俯视颜色。"""
    import numpy as np
    import trimesh
    from trimesh.visual.material import PBRMaterial
    recipe = validate_material_overrides(document)
    if recipe.get('sourceMeshSha256') and recipe['sourceMeshSha256'] != hashlib.sha256(Path(path).read_bytes()).hexdigest():
        raise ValueError('配色映射的源文件哈希不符，请重新枚举材质')
    groups = material_groups(scene)
    selected, seen = [], set()
    for patch in recipe['materials']:
        matches = [g for g in groups if g['index'] == patch['index']] if 'index' in patch else [g for g in groups if g['name'] == patch['name']]
        if len(matches) != 1:
            raise ValueError('材质选择器无匹配或名称有歧义: ' + str(patch.get('name', patch.get('index'))))
        group = matches[0]
        if group['index'] in seen:
            raise ValueError('同一材质重复设置，请合并为一个配色项')
        seen.add(group['index'])
        selected.append((group, patch))
    records = []
    for group, patch in selected:
        source = group['_material']
        if source is None:
            material = PBRMaterial(name=group['name'], metallicFactor=0, roughnessFactor=.8)
        elif isinstance(source, PBRMaterial):
            material = source.copy()
        elif hasattr(source, 'to_pbr'):
            material = source.to_pbr()
        else:
            raise ValueError('选中材质类型不支持可靠适配，请先转成PBR网格')
        texture = material.baseColorTexture
        if patch['textureMode'] == 'solid':
            material.baseColorTexture = _solid_texture(texture)
        elif texture is not None:
            material.baseColorTexture = _neutral_texture(texture)
        srgb = np.array([int(patch['color'][i:i + 2], 16) / 255 for i in (1, 3, 5)])
        linear = _linear(srgb)
        alpha = float(material.baseColorFactor[3] / 255) if material.baseColorFactor is not None else 1.
        material.baseColorFactor = np.rint(np.r_[linear, alpha] * 255).astype(np.uint8)
        if 'roughness' in patch:
            material.roughnessFactor = patch['roughness']
        vertex_changed = False
        for geometry_name in group['geometries']:
            geometry = scene.geometry[geometry_name]
            old = geometry.visual
            vertex = _neutral_vertex_colors(old, len(geometry.vertices), path, patch['textureMode'], vertex_color_space)
            if old.kind == 'texture':
                visual = old.copy()
            else:
                visual = trimesh.visual.TextureVisuals(uv=np.zeros((len(geometry.vertices), 2)), material=material)
            visual.material = material
            if vertex is not None:
                visual.vertex_attributes['color'] = vertex
                vertex_changed = True
            geometry.visual = visual
        records.append({'index': group['index'], 'name': group['name'], 'nodes': group['nodes'],
                        'color': patch['color'], 'colorSpace': 'srgb', 'textureMode': patch['textureMode'],
                        'linearBaseColorFactor': (np.asarray(material.baseColorFactor) / 255).tolist(),
                        'roughness': material.roughnessFactor, 'evidence': patch['evidence'],
                        'baseColorTexture': ('alpha-only-rgb-white' if material.baseColorTexture is not None else 'removed') if patch['textureMode'] == 'solid' else ('linear-luminance-srgb-texture' if texture is not None else 'none'),
                        'vertexColor': ('linear-luminance' if patch['textureMode'] == 'luminance' else 'rgb-white-alpha-preserved') if vertex_changed else 'none'})
    return records
