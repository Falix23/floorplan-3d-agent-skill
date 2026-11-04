"""编排本地 TripoSR：家具图片→GLB→俯视资产→可选替换户型。依赖在独立推理环境安装。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
from pathlib import Path
import subprocess
import sys
import time

from furniture_assets import prepare, attach, write_json


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_input(image_path, destination, background='auto', foreground_ratio=.85):
    """抠图后按同一边界居中并合成灰底，供官方 --no-remove-bg 入口使用。"""
    from PIL import Image, ImageOps
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert('RGBA')
    alpha_min, _ = image.getchannel('A').getextrema()
    method = 'existing-alpha'
    if alpha_min == 255:
        if background == 'alpha':
            raise ValueError('--background alpha要求图片已含透明背景')
        if background == 'none':
            image.convert('RGB').save(destination)
            return {'backgroundMethod': 'none-user-prepared', 'notice': '用户负责输入灰底、主体大小及背景干净程度'}
        from rembg import new_session, remove
        image = remove(image, session=new_session('u2netp'))
        if not isinstance(image, Image.Image):
            import io
            image = Image.open(io.BytesIO(image))
        image = image.convert('RGBA')
        method = 'rembg-u2netp'
    box = image.getchannel('A').getbbox()
    if box is None:
        raise ValueError('抠图后为空，保留原家具，不继续替换')
    crop = image.crop(box)
    size = 512
    available = int(size * foreground_ratio)
    ratio = available / max(crop.size)
    crop = crop.resize((max(1, round(crop.width * ratio)), max(1, round(crop.height * ratio))), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (size, size), (128, 128, 128, 255))
    canvas.alpha_composite(crop, ((size - crop.width) // 2, (size - crop.height) // 2))
    canvas.convert('RGB').save(destination)
    return {'backgroundMethod': method, 'sourceForegroundBoxPx': list(box), 'foregroundRatio': foreground_ratio, 'inputSizePx': [size, size], 'backgroundColor': '#808080'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--triposr-root', required=True)
    parser.add_argument('--weights', required=True, help='含config.yaml/model.ckpt的本地目录')
    parser.add_argument('--output', required=True, help='新的独立产物目录')
    parser.add_argument('--asset-id', required=True)
    parser.add_argument('--height-mm', required=True, type=float)
    parser.add_argument('--up-axis', choices=['x', 'y', 'z', '-x', '-y', '-z'], default='z')
    parser.add_argument('--front-axis', choices=['+x', '-x', '+y', '-y', '+z', '-z'], help='实际核对模型前面，不能仅从照片猜轴；负值写成--front-axis=-x')
    parser.add_argument('--prepared-image', help='已经Agent视觉复核的灰底预处理图；原图仍由--image记录')
    parser.add_argument('--background', choices=['auto', 'alpha', 'none'], default='auto')
    parser.add_argument('--foreground-ratio', type=float, default=.85)
    parser.add_argument('--mc-resolution', type=int, default=128)
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--timeout-seconds', type=int, default=900)
    parser.add_argument('--code-revision', default='unknown')
    parser.add_argument('--model-revision', default='unknown')
    parser.add_argument('--image-license', default='unknown')
    parser.add_argument('--plan')
    parser.add_argument('--item-id')
    args = parser.parse_args()
    try:
        if not 0.4 <= args.foreground_ratio <= .95 or not 64 <= args.mc_resolution <= 256:
            raise ValueError('foreground-ratio需0.4～0.95，mc-resolution需64～256')
        if not 1 <= args.height_mm <= 10000:
            raise ValueError('height-mm需1～10000，来源于用户尺寸或明确的估计')
        if bool(args.plan) != bool(args.item_id):
            raise ValueError('--plan和--item-id需要同时提供')
        from furniture_assets import axis_vector
        import numpy as np
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", args.asset_id) is None:
            raise ValueError("asset-id无效")
        if args.front_axis and abs(float(np.dot(axis_vector(args.up_axis), axis_vector(args.front_axis)))) > .001:
            raise ValueError('上轴与正面轴必须正交')
        image = Path(args.image).resolve()
        repo = Path(args.triposr_root).resolve()
        weights = Path(args.weights).resolve()
        out = Path(args.output).resolve()
        if not image.is_file() or not (repo / 'run.py').is_file() or not all((weights / name).is_file() for name in ('config.yaml', 'model.ckpt')):
            raise ValueError('输入图、官方TripoSR源码或本地权重未就绪；先按参考文档准备隔离环境')
        if out.exists() and any(out.iterdir()):
            raise ValueError('请使用新的空产物目录，保留之前的重建记录')
        plan = None
        if args.plan:
            from plan_tools import validate
            plan = json.loads(Path(args.plan).read_text())
            validate(plan)
            if not any(item['id'] == args.item_id for item in plan['furniture']):
                raise ValueError('目标家具不存在，不启动GPU推理')
        out.mkdir(parents=True, exist_ok=True)
        original_copy = out / ('input-original' + image.suffix.lower())
        shutil.copyfile(image, original_copy)
        staged = out / 'input-prepared.png'
        if args.prepared_image:
            prepared = Path(args.prepared_image).resolve()
            pre = prepare_input(prepared, staged, 'none', args.foreground_ratio)
            pre.update(backgroundMethod='agent-reviewed-prepared-image', preparedImageSha256=sha256(prepared))
        else:
            pre = prepare_input(image, staged, args.background, args.foreground_ratio)
        if pre['backgroundMethod'] == 'rembg-u2netp':
            pending = {'ok': True, 'status': 'needs_preprocessing_review', 'inferencePerformed': False, 'sourceImageSha256': sha256(image), 'preprocessing': pre, 'preparedImage': str(staged), 'nextStep': 'Agent查看原图与预处理图，检查镂空/细腿；合格后用新产物目录及--prepared-image继续，未替换原家具'}
            write_json(out / 'run.json', pending)
            print(json.dumps(pending, ensure_ascii=False))
            return
        raw = out / 'reconstruction'
        # 官方run.py --no-remove-bg分支不创建编号目录，适配器提前创建。
        (raw / '0').mkdir(parents=True)
        command = [sys.executable, str(repo / 'run.py'), str(staged), '--pretrained-model-name-or-path', str(weights), '--output-dir', str(raw), '--model-save-format', 'glb', '--mc-resolution', str(args.mc_resolution), '--device', args.device, '--no-remove-bg']
        metadata = {
            'method': 'TripoSR', 'sourceRepository': 'https://github.com/VAST-AI-Research/TripoSR',
            'sourceCodeCommit': args.code_revision, 'modelRepository': 'https://huggingface.co/stabilityai/TripoSR',
            'modelRevision': args.model_revision, 'modelWeightsSha256': sha256(weights / 'model.ckpt'),
            'inputImageLicense': args.image_license, 'sourceImageSha256': sha256(image), 'inferencePerformed': False,
            'preprocessing': pre, 'command': command,
            'scaleStatus': 'user-provided-or-estimated-not-measured-from-photo',
            'startedAt': datetime.now(timezone.utc).isoformat(),
        }
        write_json(out / 'run.json', metadata)
        started = time.monotonic()
        with (out / 'inference.log').open('w') as log:
            subprocess.run(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=args.timeout_seconds)
        mesh = raw / '0/mesh.glb'
        if not mesh.is_file():
            raise ValueError('推理没有生成真实mesh.glb，未替换原家具；请查看inference.log')
        metadata.update(inferencePerformed=True, inferenceSeconds=round(time.monotonic() - started, 3))
        if not args.front_axis:
            metadata.update(ok=True, status="needs_alignment", mesh=str(mesh), nextStep="检查网格正面后，使用furniture_assets.py prepare/attach；尚未替换户型")
            write_json(out / 'run.json', metadata)
            print(json.dumps(metadata, ensure_ascii=False))
            return
        asset_provenance = {k: v for k, v in metadata.items() if k not in ("command", "startedAt")}
        asset, report = prepare(mesh, image, args.asset_id, args.height_mm, args.up_axis, args.front_axis, 'TripoSR', 'MIT', provenance=asset_provenance, vertex_color_space='srgb')
        write_json(out / 'asset.json', asset)
        write_json(out / 'asset.report.json', report)
        import base64
        (out / 'asset.glb').write_bytes(base64.b64decode(asset['dataUrl'].split(',', 1)[1]))
        (out / 'asset.top.png').write_bytes(base64.b64decode(asset['topViewDataUrl'].split(',', 1)[1]))
        if plan is not None:
            result = attach(plan, asset, args.item_id)
            from plan_tools import build
            write_json(out / 'plan.json', result)
            build(result, out / 'index.html')
            metadata['targetItemId'] = args.item_id
        metadata.update(ok=True, output=str(out), assetId=asset['id'], assetReport='asset.report.json')
        write_json(out / 'run.json', metadata)
        print(json.dumps(metadata, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, ImportError, subprocess.SubprocessError) as exc:
        parser.exit(1, '图片转家具失败，原计划未被覆盖: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
