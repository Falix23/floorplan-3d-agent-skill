"""家具网格→自包含资产、真实俯视图，并替换户型中的指定家具。"""
import argparse
import base64
import copy
import hashlib
import io
import json
from pathlib import Path

from asset_validation import validate_asset


def data_url(mime, data):
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def axis_vector(axis):
    import numpy as np
    sign = -1 if axis.startswith('-') else 1
    value = np.zeros(3)
    value['xyz'.index(axis[-1])] = sign
    return value


def validated_orientation(document):
    """接受有依据的源坐标→家具局部坐标纯旋转，不接受镜像或几何变形。"""
    import numpy as np
    if not isinstance(document, dict) or not isinstance(document.get('evidence'), str) or not document['evidence'].strip():
        raise ValueError('三轴对齐需要JSON对象和非空evidence依据')
    values = document.get('rotation')
    if not isinstance(values, list) or len(values) != 3 or any(not isinstance(row, list) or len(row) != 3 for row in values):
        raise ValueError('rotation需要3×3数值矩阵')
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for row in values for value in row):
        raise ValueError('rotation仅接受有限数值')
    rotation = np.asarray(values, dtype=np.float64)
    if not np.isfinite(rotation).all():
        raise ValueError('rotation仅接受有限数值')
    if not np.allclose(rotation @ rotation.T, np.eye(3), rtol=0, atol=1e-6) or abs(float(np.linalg.det(rotation)) - 1) > 1e-6:
        raise ValueError('rotation必须为正交且det=+1的纯旋转；不能使用镜像、拉伸或剪切')
    return rotation, {'rotation': rotation.tolist(), 'evidence': document['evidence'].strip()}


def normalized_rgba(colors, vertex_count):
    """仅广播明确的单个RGB/RGBA值；拒绝缺失、越界或数量不符的颜色。"""
    import numpy as np
    try:
        values = np.asarray(colors)
    except (TypeError, ValueError) as exc:
        raise ValueError('颜色数组无法读取') from exc
    if values.ndim == 1 and values.shape[0] in (3, 4):
        values = np.broadcast_to(values, (vertex_count, values.shape[0]))
    if (values.ndim != 2 or values.shape[0] != vertex_count
            or values.shape[1] not in (3, 4) or vertex_count <= 0):
        raise ValueError('颜色需要单个RGB/RGBA或每个顶点对应的RGB/RGBA数组')
    if values.dtype.kind not in 'uif' or not np.isfinite(values).all():
        raise ValueError('颜色仅接受有限数值')
    if (values < 0).any() or (values > 255).any():
        raise ValueError('颜色通道需在0到255之间，不自动猜测或裁剪')
    result = np.rint(values).astype(np.uint8)
    if result.shape[1] == 3:
        result = np.column_stack([result, np.full(vertex_count, 255, dtype=np.uint8)])
    return result


def sampled_texture_rgba(image, uv, vertex_count):
    """沿用trimesh的近邻/重复采样，取整后再次取模，避免边界索引越界。"""
    import numpy as np
    coordinates = np.asarray(uv, dtype=np.float64)
    if coordinates.shape != (vertex_count, 2) or not np.isfinite(coordinates).all():
        raise ValueError('纹理UV需要每个顶点对应的有限二维坐标')
    pixels = np.asarray(image.convert('RGBA'))
    height, width = pixels.shape[:2]
    x = (coordinates[:, 0] * (width - 1)) % width
    y = ((1 - coordinates[:, 1]) * (height - 1)) % height
    # 例如width-0.0001取整会得到width；必须在取整后处理重复边界。
    return pixels[np.rint(y).astype(np.int64) % height,
                  np.rint(x).astype(np.int64) % width]


def texture_vertex_color_multiplier(visual, vertex_count, path):
    """读取材质并存的COLOR_0；glTF定义为线性乘数，不再做sRGB转换。"""
    import numpy as np
    attributes = getattr(visual, 'vertex_attributes', {})
    if 'color' not in attributes:
        return None
    values = np.asarray(attributes['color'])
    if (values.ndim != 2 or values.shape[0] != vertex_count
            or values.shape[1] not in (3, 4)):
        raise ValueError('材质顶点颜色需要每个顶点对应的RGB/RGBA数组')
    if Path(path).suffix.lower() in ('.glb', '.gltf'):
        # trimesh保留该accessor的原始类型：float[0,1]或归一化u8/u16。
        if values.dtype.kind == 'u' and values.dtype.itemsize in (1, 2):
            divisor = np.iinfo(values.dtype).max
        elif values.dtype.kind == 'f':
            divisor = 1.
        else:
            raise ValueError('glTF材质顶点颜色仅支持float或归一化uint8/uint16')
        linear = values.astype(np.float64) / divisor
        if not np.isfinite(linear).all() or (linear < 0).any() or (linear > 1).any():
            raise ValueError('glTF材质顶点颜色需要有限的0到1线性乘数')
        if linear.shape[1] == 3:
            linear = np.column_stack([linear, np.ones(vertex_count)])
        return linear
    colors = normalized_rgba(values, vertex_count).astype(np.float64) / 255
    rgb = colors[:, :3]
    colors[:, :3] = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    return colors


def linear_vertex_colors(visual, vertex_count, path, color_space):
    """保留PBR贴图与线性baseColorFactor的来源，输出线性RGBA顶点色。"""
    import numpy as np
    from trimesh.visual.material import PBRMaterial
    factor, vertex_multiplier = None, None
    if visual.kind == 'texture':
        vertex_multiplier = texture_vertex_color_multiplier(visual, vertex_count, path)
        material = visual.material
        if isinstance(material, PBRMaterial):
            texture = material.baseColorTexture
            if texture is not None:
                colors = sampled_texture_rgba(texture, visual.uv, vertex_count)
                factor = material.baseColorFactor
                source, default_space = 'pbr-base-color-texture', 'srgb'
            elif material.baseColorFactor is not None:
                colors = material.baseColorFactor
                source, default_space = 'pbr-base-color-factor', 'linear'
            else:
                # glTF规范中未写baseColorFactor的PBR材质明确默认线性白色。
                colors = [255, 255, 255, 255]
                source, default_space = 'pbr-default-white', 'linear'
        elif getattr(material, 'image', None) is not None:
            colors = sampled_texture_rgba(material.image, visual.uv, vertex_count)
            source, default_space = 'material-texture', 'srgb'
        else:
            colors = visual.to_color().vertex_colors
            source, default_space = 'material-color', 'srgb'
    else:
        colors = visual.vertex_colors
        source = 'vertex-colors'
        default_space = 'linear' if Path(path).suffix.lower() in ('.glb', '.gltf') else 'srgb'
    colors = normalized_rgba(colors, vertex_count)
    space = default_space if color_space == 'auto' else color_space
    if space not in ('srgb', 'linear'):
        raise ValueError('颜色空间仅支持auto、srgb、linear')
    linear = colors.astype(np.float64) / 255
    if space == 'srgb':
        rgb = linear[:, :3]
        linear[:, :3] = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    if factor is not None:
        # glTF的baseColorFactor为线性乘数，不可当作sRGB重复变换。
        linear *= normalized_rgba(factor, vertex_count).astype(np.float64) / 255
    if vertex_multiplier is not None:
        # glTF最终基色 = 线性贴图 × 线性baseColorFactor × 线性COLOR_0，alpha同乘。
        linear *= vertex_multiplier
    return np.rint(linear * 255).astype(np.uint8), {
        'source': source, 'inputColorSpace': space, 'pbrFactorApplied': factor is not None,
        'vertexColorMultiplierApplied': vertex_multiplier is not None}


def normalized_meshes(path, up_axis, front_axis, vertex_color_space="auto", orientation_matrix=None):
    try:
        import numpy as np
        import trimesh
    except ImportError as exc:
        raise ValueError('prepare需要隔离环境中的numpy、trimesh、Pillow；参阅references/furniture-image.md') from exc
    if orientation_matrix is not None:
        rotation, orientation = validated_orientation(orientation_matrix)
    else:
        up, front = axis_vector(up_axis), axis_vector(front_axis)
        if abs(float(np.dot(up, front))) > .001:
            raise ValueError('上轴与正面轴必须正交')
        rotation = np.stack([np.cross(up, front), up, front])
        orientation = None
    # 资产在离线工具中导入，打包后不再允许任何外部资源URI。
    scene = trimesh.load(str(path), force='scene', process=False)
    meshes, transformed_normals, color_sampling = [], [], []
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node]
        geometry = scene.geometry[geometry_name]
        if not isinstance(geometry, trimesh.Trimesh) or len(geometry.faces) == 0:
            continue
        # 在写顶点坐标使缓存失效前保留作者法线；缺失时trimesh从源几何计算。
        normals = np.asarray(geometry.vertex_normals, dtype=np.float64).copy()
        if (normals.shape != (len(geometry.vertices), 3) or not np.isfinite(normals).all()
                or (np.linalg.norm(normals, axis=1) <= 1e-12).any()):
            raise ValueError('原始网格法线缺失、非有限或长度为零，无法可靠导出')
        try:
            normals = normals @ np.linalg.inv(rotation @ np.asarray(transform)[:3, :3])
        except np.linalg.LinAlgError as exc:
            raise ValueError('网格节点变换不可逆，不能变换法线') from exc
        mesh = geometry.copy()
        mesh.apply_transform(transform)
        mesh.vertices = np.asarray(mesh.vertices) @ rotation.T
        if not np.isfinite(mesh.vertices).all():
            raise ValueError('原始网格包含非有限顶点')
        # 同几何俯视与默认导出均使用已合成的线性顶点色；PBR模式另保留源材质。
        # TextureVisuals.copy()只带UV/材质，可能丢掉并存的COLOR_0；从源visual采样。
        colors, sampling = linear_vertex_colors(geometry.visual, len(mesh.vertices), path, vertex_color_space)
        color_sampling.append({'node': node, **sampling})
        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=colors)
        meshes.append(mesh)
        transformed_normals.append(normals)
    if not meshes:
        raise ValueError('文件没有可用三角网格')
    all_vertices = np.concatenate([mesh.vertices for mesh in meshes])
    lower, upper = all_vertices.min(axis=0), all_vertices.max(axis=0)
    extent = upper - lower
    if np.min(extent) <= 1e-8:
        raise ValueError('网格宽深高不能为零')
    origin = np.array([(lower[0] + upper[0]) / 2, lower[1], (lower[2] + upper[2]) / 2])
    for mesh, normals in zip(meshes, transformed_normals):
        mesh.vertices = (mesh.vertices - origin) / extent
        # 归一化含非均匀缩放：法线用逆转置，不能按顶点的缩放矩阵处理。
        normals *= extent
        lengths = np.linalg.norm(normals, axis=1)
        if not np.isfinite(normals).all() or not np.isfinite(lengths).all() or (lengths <= 1e-12).any():
            raise ValueError('变换后的网格法线非有限或长度为零')
        mesh.vertex_normals = normals / lengths[:, None]
    report = {
        'orientationMode': 'explicit_matrix' if orientation is not None else 'cardinal_axes',
        'sourceUpAxis': None if orientation is not None else up_axis,
        'sourceFrontAxis': None if orientation is not None else front_axis,
        'sourceToCanonicalRotation': rotation.tolist(),
        'orientationEvidence': orientation['evidence'] if orientation is not None else '调用者指定的上轴与正面轴',
        'sourceRotatedBounds': [lower.tolist(), upper.tolist()],
        'sourceExtents': extent.tolist(),
        'sourceAspectRatiosWHoverD': [float(extent[0] / extent[2]), float(extent[1] / extent[2])],
        'normalizedBounds': [[-.5, 0, -.5], [.5, 1, .5]],
        'axes': {'width': '+X', 'height': '+Y', 'front': '+Z', 'back': '-Z'},
        'fitMode': 'dimensions_from_plan',
        'colorMode': 'linear-vertex-colors-in-glb-srgb-top-view',
        'sourceVertexColorSpace': vertex_color_space,
        'colorSampling': color_sampling,
        'normalTransform': 'source-normal-inverse-transpose-of-scene-rotation-and-normalization',
        'triangles': sum(len(mesh.faces) for mesh in meshes),
    }
    return meshes, report


def render_top_view(meshes, resolution=384):
    """同一规范化网格的正交俯视，逐三角形深度缓冲；图像上方始终为家具背面-Z。"""
    import numpy as np
    from PIL import Image
    pixels = np.zeros((resolution, resolution, 4), dtype=np.uint8)
    depth = np.full((resolution, resolution), -np.inf, dtype=np.float32)
    for mesh in meshes:
        vertices = np.asarray(mesh.vertices)
        faces = np.asarray(mesh.faces)
        colors = np.asarray(mesh.visual.vertex_colors, dtype=np.float32)
        screen = (vertices[:, [0, 2]] + .5) * (resolution - 1)
        for face in faces:
            tri, ys, color = screen[face], vertices[face, 1], colors[face]
            x0, y0 = np.maximum(np.floor(tri.min(axis=0)).astype(int), 0)
            x1, y1 = np.minimum(np.ceil(tri.max(axis=0)).astype(int), resolution - 1)
            if x0 > x1 or y0 > y1:
                continue
            a, b, c = tri
            denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(denominator) < 1e-10:
                continue
            xx, yy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
            wa = ((b[1] - c[1]) * (xx - c[0]) + (c[0] - b[0]) * (yy - c[1])) / denominator
            wb = ((c[1] - a[1]) * (xx - c[0]) + (a[0] - c[0]) * (yy - c[1])) / denominator
            wc = 1 - wa - wb
            inside = (wa >= -1e-7) & (wb >= -1e-7) & (wc >= -1e-7)
            z = wa * ys[0] + wb * ys[1] + wc * ys[2]
            region = depth[y0:y1 + 1, x0:x1 + 1]
            visible = inside & (z > region)
            if not visible.any():
                continue
            rgba = wa[..., None] * color[0] + wb[..., None] * color[1] + wc[..., None] * color[2]
            # 中性顶光只为辨认层次；不改变家具轮廓、UV或任何位置。
            normal = np.cross(vertices[face[1]] - vertices[face[0]], vertices[face[2]] - vertices[face[0]])
            length = np.linalg.norm(normal)
            shade = .75 + .25 * abs(normal[1] / length) if length > 0 else 1.
            linear = np.clip(rgba[..., :3] / 255 * shade, 0, 1)
            rgba[..., :3] = np.where(linear <= .0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - .055) * 255
            pixels[y0:y1 + 1, x0:x1 + 1][visible] = np.clip(rgba[visible], 0, 255).astype(np.uint8)
            region[visible] = z[visible]
    if not (pixels[..., 3] > 0).any():
        raise ValueError('俯视渲染为空')
    output = io.BytesIO()
    Image.fromarray(pixels, 'RGBA').save(output, format='PNG')
    return output.getvalue()


def pbr_export_meshes(path, meshes, alignment, texture_max_size):
    """只恢复3D副本的源UV/材质；2D采样网格、几何和作者法线不改。"""
    import trimesh
    import numpy as np
    from PIL import Image
    scene = trimesh.load(str(path), force='scene', process=False)
    material_cache, image_cache, texture_records, exported = {}, {}, [], []
    properties = ('baseColorTexture', 'metallicRoughnessTexture', 'normalTexture',
                  'occlusionTexture', 'emissiveTexture', 'image')
    for mesh, sampling in zip(meshes, alignment['colorSampling']):
        _, name = scene.graph[sampling['node']]
        original = scene.geometry[name]
        part = mesh.copy()
        if original.visual.kind == 'texture':
            visual = original.visual.copy()
            vertex_multiplier = texture_vertex_color_multiplier(original.visual, len(part.vertices), path)
            if vertex_multiplier is not None:
                # trimesh的GLB导出固定写u8 COLOR_0，先显式量化，避免float被直接截断。
                visual.vertex_attributes['color'] = np.rint(vertex_multiplier * 255).astype(np.uint8)
            source_material = original.visual.material
            key = id(source_material)
            if key not in material_cache:
                material = source_material.copy()
                for property_name in properties:
                    image = getattr(source_material, property_name, None)
                    if image is None:
                        continue
                    source_key = hashlib.sha256(image.mode.encode() + str(image.size).encode() + image.tobytes()).hexdigest()
                    reference = {'material': source_material.name, 'property': property_name}
                    if source_key not in image_cache:
                        width, height = image.size
                        factor = min(1, texture_max_size / max(width, height))
                        size = (max(1, round(width * factor)), max(1, round(height * factor)))
                        resized = image.copy() if size == image.size else image.resize(size, Image.Resampling.LANCZOS)
                        record = {'sourcePixelSha256': source_key, 'sourceSize': list(image.size),
                                  'outputSize': list(size), 'resampling': 'LANCZOS' if size != image.size else 'unchanged',
                                  'references': []}
                        image_cache[source_key] = (resized, record)
                        texture_records.append(record)
                    resized, record = image_cache[source_key]
                    record['references'].append(reference)
                    setattr(material, property_name, resized)
                material_cache[key] = material
            visual.material = material_cache[key]
            part.visual = visual
        # copy默认不带缓存；不能让已验证的作者法线再次被重算。
        part.vertex_normals = mesh.vertex_normals.copy()
        exported.append(part)
    return exported, texture_records


def deduplicate_material_images(tree):
    """trimesh已复用相同BIN数据；合并其重复image/texture引用。"""
    images, image_map, seen = [], {}, {}
    for index, image in enumerate(tree.get('images', [])):
        key = json.dumps(image, sort_keys=True)
        if key not in seen:
            seen[key] = len(images)
            images.append(image)
        image_map[index] = seen[key]
    if 'images' in tree:
        tree['images'] = images
    textures, texture_map, seen = [], {}, {}
    for index, texture in enumerate(tree.get('textures', [])):
        if 'source' in texture:
            texture['source'] = image_map[texture['source']]
        key = json.dumps(texture, sort_keys=True)
        if key not in seen:
            seen[key] = len(textures)
            textures.append(texture)
        texture_map[index] = seen[key]
    if 'textures' in tree:
        tree['textures'] = textures
    for material in tree.get('materials', []):
        for container in (material, material.get('pbrMetallicRoughness', {})):
            for key in ('baseColorTexture', 'metallicRoughnessTexture', 'normalTexture',
                        'occlusionTexture', 'emissiveTexture'):
                if key in container:
                    container[key]['index'] = texture_map[container[key]['index']]


def prepare(mesh_path, image_path, asset_id, height_mm, up_axis='z', front_axis='-y', method='provided-mesh', license_name='unknown', resolution=384, provenance=None, vertex_color_space="auto", orientation_matrix=None, material_mode='vertex-color', texture_max_size=256):
    if material_mode not in ('vertex-color', 'preserve-pbr'):
        raise ValueError('材质模式仅支持vertex-color、preserve-pbr')
    if (not isinstance(texture_max_size, int) or isinstance(texture_max_size, bool)
            or not 32 <= texture_max_size <= 2048):
        raise ValueError('贴图最大尺寸需要32到2048之间的整数')
    import trimesh
    meshes, alignment = normalized_meshes(mesh_path, up_axis, front_axis, vertex_color_space, orientation_matrix)
    if alignment['triangles'] > 250000:
        raise ValueError('三角面超过250000，请明确简化源网格后重试')
    def neutral_materials(tree):
        tree["materials"] = [{"name": "家具中性粗糙材质", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": .8}, "doubleSided": True}]
        for mesh in tree.get("meshes", []):
            for primitive in mesh.get("primitives", []):
                primitive["material"] = 0
    export_meshes, texture_records = meshes, []
    if material_mode == 'preserve-pbr':
        export_meshes, texture_records = pbr_export_meshes(mesh_path, meshes, alignment, texture_max_size)
        alignment['colorMode'] = 'source-pbr-uv-materials;top-view-linear-vertex-colors'
    alignment['materialMode'] = material_mode
    glb = trimesh.exchange.gltf.export_glb(trimesh.Scene(export_meshes), include_normals=True,
                                        tree_postprocessor=neutral_materials if material_mode == 'vertex-color' else deduplicate_material_images)
    top = render_top_view(meshes, resolution)
    info = copy.deepcopy(provenance or {})
    info.update({
        'method': method,
        'license': license_name,
        'sourceImageSha256': hashlib.sha256(Path(image_path).read_bytes()).hexdigest(),
        'sourceImageName': Path(image_path).name,
        'sourceMeshSha256': hashlib.sha256(Path(mesh_path).read_bytes()).hexdigest(),
        'sourceMeshName': Path(mesh_path).name,
        'geometryNotice': '几何来自所附网格；库模型只代表所选近似款，照片不可见的结构未经确认。最终尺寸来自规格、用户输入或声明的估计，不能视为照片实测或原款精确复刻。',
        'colorNotice': ('3D保留源PBR/UV，贴图按记录缩小；2D仍为同几何顶点采样图，不保留全部细纹理。'
                        if material_mode == 'preserve-pbr' else '原始纹理采样为顶点色，细纹理会简化。'),
        'materialProcessing': {'mode': material_mode, 'textureMaxSize': texture_max_size,
                               'textureResizes': texture_records,
                               'topViewMode': 'same-geometry-vertex-color-sampling'},
    })
    if orientation_matrix is not None:
        info['orientation'] = {'mode': alignment['orientationMode'], 'rotation': alignment['sourceToCanonicalRotation'], 'evidence': alignment['orientationEvidence']}
    asset = {'id': asset_id, 'format': 'glb', 'dataUrl': data_url('model/gltf-binary', glb), 'topViewDataUrl': data_url('image/png', top), 'defaultHeightMm': height_mm, 'alignment': alignment, 'provenance': info}
    report = validate_asset(asset)
    return asset, {'ok': True, **report, 'alignment': alignment, 'provenance': info}


def attach(plan, asset, item_id):
    from plan_tools import validate
    validate(plan)
    validate_asset(asset)
    result = copy.deepcopy(plan)
    furniture = next((item for item in result['furniture'] if item['id'] == item_id), None)
    if furniture is None:
        raise ValueError('找不到要替换的家具: ' + item_id)
    assets = result.setdefault('furnitureAssets', {})
    existing = assets.get(asset['id'])
    if existing is not None and existing != asset:
        raise ValueError('资产id已被不同资产使用，请换一个新id，避免影响其他家具')
    assets[asset['id']] = copy.deepcopy(asset)
    furniture['assetId'] = asset['id']
    furniture.setdefault('height', asset['defaultHeightMm'])
    validate(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--mesh', required=True)
    p.add_argument('--source-image', required=True)
    p.add_argument('--asset-id', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--height-mm', required=True, type=float)
    p.add_argument('--up-axis', choices=['x', 'y', 'z', '-x', '-y', '-z'], default='z')
    p.add_argument('--front-axis', choices=['+x', '-x', '+y', '-y', '+z', '-z'], default='-y')
    p.add_argument('--orientation-matrix', help='有evidence依据的3×3纯旋转JSON，覆盖上轴/正面轴选项')
    p.add_argument('--vertex-color-space', choices=['auto', 'srgb', 'linear'], default='auto')
    p.add_argument('--material-mode', choices=['vertex-color', 'preserve-pbr'], default='vertex-color')
    p.add_argument('--texture-max-size', type=int, default=256)
    p.add_argument('--method', default='provided-mesh')
    p.add_argument('--license', default='unknown')
    p.add_argument('--resolution', type=int, default=384)
    p.add_argument('--provenance-json')
    p.add_argument('--report')
    p = commands.add_parser('attach')
    p.add_argument('--plan', required=True)
    p.add_argument('--asset', required=True)
    p.add_argument('--item-id', required=True)
    p.add_argument('--output', required=True)
    p = commands.add_parser('preprocess')
    p.add_argument('--source-image', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--background', choices=['auto', 'alpha', 'none'], default='auto')
    p = commands.add_parser('inspect')
    p.add_argument('asset')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            if not 32 <= args.resolution <= 1024:
                raise ValueError('俯视分辨率需为32～1024')
            provenance = json.loads(Path(args.provenance_json).read_text()) if args.provenance_json else None
            orientation = json.loads(Path(args.orientation_matrix).read_text()) if args.orientation_matrix else None
            asset, report = prepare(args.mesh, args.source_image, args.asset_id, args.height_mm, args.up_axis, args.front_axis, args.method, args.license, args.resolution, provenance, args.vertex_color_space, orientation_matrix=orientation, material_mode=args.material_mode, texture_max_size=args.texture_max_size)
            write_json(args.output, asset)
            write_json(args.report or str(Path(args.output).with_suffix('.report.json')), report)
            Path(args.output).with_suffix('.top.png').write_bytes(base64.b64decode(asset['topViewDataUrl'].split(',', 1)[1]))
            Path(args.output).with_suffix('.glb').write_bytes(base64.b64decode(asset['dataUrl'].split(',', 1)[1]))
            print(json.dumps(report, ensure_ascii=False))
        elif args.command == 'attach':
            plan = json.loads(Path(args.plan).read_text())
            asset = json.loads(Path(args.asset).read_text())
            result = attach(plan, asset, args.item_id)
            write_json(args.output, result)
            print(json.dumps({'ok': True, 'itemId': args.item_id, 'assetId': asset['id'], 'output': args.output}, ensure_ascii=False))
        elif args.command == 'preprocess':
            from image_to_furniture import prepare_input
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            report = prepare_input(args.source_image, output, args.background)
            report.update(ok=True, status='needs_visual_review', sourceImageSha256=hashlib.sha256(Path(args.source_image).read_bytes()).hexdigest(), inferencePerformed=False)
            write_json(output.with_suffix('.report.json'), report)
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(json.dumps(validate_asset(json.loads(Path(args.asset).read_text())), ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, ImportError) as exc:
        parser.exit(1, '家具资产处理失败: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
