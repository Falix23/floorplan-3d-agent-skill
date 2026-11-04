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
    meshes = []
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node]
        geometry = scene.geometry[geometry_name]
        if not isinstance(geometry, trimesh.Trimesh) or len(geometry.faces) == 0:
            continue
        mesh = geometry.copy()
        mesh.apply_transform(transform)
        mesh.vertices = np.asarray(mesh.vertices) @ rotation.T
        if not np.isfinite(mesh.vertices).all():
            raise ValueError('原始网格包含非有限顶点')
        # 首版统一为顶点色，避免材质扩展/外部纹理依赖，保留已采样的颜色。
        visual = mesh.visual
        colors = visual.to_color().vertex_colors if visual.kind == 'texture' else visual.vertex_colors
        colors = np.asarray(colors).copy()
        space = vertex_color_space if vertex_color_space != "auto" else ("linear" if visual.kind != "texture" and Path(path).suffix.lower() in (".glb", ".gltf") else "srgb")
        if space == "srgb":
            rgb = colors[:, :3].astype(float) / 255
            rgb = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
            colors[:, :3] = np.rint(rgb * 255).astype(np.uint8)
        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=colors)
        meshes.append(mesh)
    if not meshes:
        raise ValueError('文件没有可用三角网格')
    all_vertices = np.concatenate([mesh.vertices for mesh in meshes])
    lower, upper = all_vertices.min(axis=0), all_vertices.max(axis=0)
    extent = upper - lower
    if np.min(extent) <= 1e-8:
        raise ValueError('网格宽深高不能为零')
    origin = np.array([(lower[0] + upper[0]) / 2, lower[1], (lower[2] + upper[2]) / 2])
    for mesh in meshes:
        mesh.vertices = (mesh.vertices - origin) / extent
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


def prepare(mesh_path, image_path, asset_id, height_mm, up_axis='z', front_axis='-y', method='provided-mesh', license_name='unknown', resolution=384, provenance=None, vertex_color_space="auto", orientation_matrix=None):
    import trimesh
    meshes, alignment = normalized_meshes(mesh_path, up_axis, front_axis, vertex_color_space, orientation_matrix)
    if alignment['triangles'] > 250000:
        raise ValueError('三角面超过250000，请在推理阶段降低marching-cubes分辨率或明确简化网格后重试')
    def neutral_materials(tree):
        tree["materials"] = [{"name": "家具中性粗糙材质", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": .8}, "doubleSided": True}]
        for mesh in tree.get("meshes", []):
            for primitive in mesh.get("primitives", []):
                primitive["material"] = 0
    glb = trimesh.exchange.gltf.export_glb(trimesh.Scene(meshes), include_normals=True, tree_postprocessor=neutral_materials)
    top = render_top_view(meshes, resolution)
    info = copy.deepcopy(provenance or {})
    info.update({
        'method': method,
        'license': license_name,
        'sourceImageSha256': hashlib.sha256(Path(image_path).read_bytes()).hexdigest(),
        'sourceImageName': Path(image_path).name,
        'sourceMeshSha256': hashlib.sha256(Path(mesh_path).read_bytes()).hexdigest(),
        'sourceMeshName': Path(mesh_path).name,
        'geometryNotice': '几何来自所附网格；照片重建的不可见面属于推测，最终尺寸来自用户输入或类别估计，不能视为原款精确测量。',
        'colorNotice': '首版将原始纹理采样为顶点色，细纹理会简化。',
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
            asset, report = prepare(args.mesh, args.source_image, args.asset_id, args.height_mm, args.up_axis, args.front_axis, args.method, args.license, args.resolution, provenance, args.vertex_color_space, orientation_matrix=orientation)
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
