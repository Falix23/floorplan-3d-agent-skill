"""记录跨层图像的配准及独立复核残差；不修改建筑或自动认定结构对应关系。"""
import argparse
import base64
import hashlib
import html
import json
import math
from pathlib import Path
import re


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def point(value):
    return isinstance(value, list) and len(value) == 2 and all(finite(v) for v in value)


def image_info(spec, base):
    from PIL import Image
    if not isinstance(spec, dict) or not re.fullmatch(r'[A-Za-z0-9_-]+', spec.get('floorId', '')):
        raise ValueError('图像需要有效floorId')
    path = (base / spec['imageFile']).resolve()
    with Image.open(path) as image:
        size = image.size
        fmt = image.format
    if fmt not in ('PNG', 'JPEG', 'WEBP'):
        raise ValueError('配准原图仅支持PNG/JPEG/WebP')
    return dict(spec, path=str(path), width=size[0], height=size[1],
                mime={'PNG':'image/png', 'JPEG':'image/jpeg', 'WEBP':'image/webp'}[fmt],
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def validate_matrix(matrix, np):
    try:
        m = np.asarray(matrix, dtype=float)
    except (ValueError, TypeError) as exc:
        raise ValueError('plannedTransform须为有限3×3相似变换矩阵') from exc
    if (m.shape != (3, 3) or not np.isfinite(m).all()
            or not np.allclose(m[2], [0, 0, 1], atol=1e-10)
            or abs(m[0, 0]-m[1, 1]) > 1e-8 or abs(m[0, 1]+m[1, 0]) > 1e-8
            or np.linalg.det(m[:2, :2]) <= 1e-12):
        raise ValueError('plannedTransform必须是非退化、无镜像/剪切的相似变换')
    return m


def metrics(transform, rows, mm_per_pixel=None):
    import numpy as np
    source = np.array([r['movingPx'] for r in rows], dtype=float)
    target = np.array([r['referencePx'] for r in rows], dtype=float)
    predicted = transform(source)
    residuals = transform.residuals(source, target)
    points = []
    for row, output, error in zip(rows, predicted, residuals):
        item = dict(row, predictedReferencePx=output.tolist(),
                    errorVectorPx=(output-np.array(row['referencePx'])).tolist(), errorPx=float(error))
        if mm_per_pixel is not None:
            item['drawingEquivalentMm'] = float(error*mm_per_pixel)
        points.append(item)
    result = {'count':len(rows), 'rmsPx':float(np.sqrt(np.mean(residuals**2))),
              'maxPx':float(np.max(residuals)), 'p95Px':float(np.percentile(residuals, 95)), 'points':points}
    if mm_per_pixel is not None:
        result['rmsDrawingEquivalentMm'] = result['rmsPx']*mm_per_pixel
        result['maxDrawingEquivalentMm'] = result['maxPx']*mm_per_pixel
    return result


def building_transform(pair, base, ref, moving, np):
    """从当前建筑推导变换，并核对内嵌原图；不靠手填拟合矩阵冒充已应用。"""
    path = (base / pair['buildingFile']).resolve()
    raw = path.read_bytes(); building = json.loads(raw)
    if building.get('schemaVersion') != 2:
        raise ValueError('buildingFile需要多层v2建筑')
    records = []
    for image in (ref, moving):
        floors = [f for f in building['floors'] if f['id'] == image['floorId']]
        if len(floors) != 1:
            raise ValueError('配准楼层在buildingFile中缺失或重复')
        f = floors[0]; r = f['plan'].get('reference', {})
        if not point(r.get('originPx')) or not point(f.get('offset')) or not finite(r.get('mmPerPixel')) or r['mmPerPixel'] <= 0:
            raise ValueError('建筑楼层缺少有效像素标定或offset')
        try:
            embedded = base64.b64decode(r['dataUrl'].split(',',1)[1],validate=True)
        except (KeyError, ValueError, IndexError) as exc:
            raise ValueError('建筑楼层缺少可核对的内嵌原图') from exc
        if hashlib.sha256(embedded).hexdigest() != image['sha256'] or (r['width'],r['height']) != (image['width'],image['height']):
            raise ValueError('建筑内嵌原图与配准输入不一致')
        records.append((f,r))
    (rf,rr),(mf,mr) = records
    if ref.get('mmPerPixel') is not None and abs(ref['mmPerPixel']-rr['mmPerPixel']) > 1e-8:
        raise ValueError('报告毫米比例与当前建筑参考图不一致')
    scale = mr['mmPerPixel']/rr['mmPerPixel']
    tx,ty = [rr['originPx'][k]-scale*mr['originPx'][k]+(mf['offset'][k]-rf['offset'][k])/rr['mmPerPixel'] for k in (0,1)]
    matrix = np.array([[scale,0,tx],[0,scale,ty],[0,0,1]])
    if 'plannedTransform' in pair and not np.allclose(validate_matrix(pair['plannedTransform'],np),matrix,atol=1e-8,rtol=0):
        raise ValueError('声明的plannedTransform与当前建筑实际标定/offset不一致')
    return matrix, {'kind':'building_derived','buildingFile':str(path),'buildingSha256':hashlib.sha256(raw).hexdigest(),
                    'embeddedSourcesMatch':True}


def analyze(manifest, base):
    try:
        import numpy as np
        import skimage
        from skimage.transform import SimilarityTransform
    except ImportError as exc:
        raise ValueError('请先安装requirements-alignment.txt；此工具不需要模型权重/GPU') from exc
    pairs = manifest.get('pairs')
    if manifest.get('schemaVersion') != 1 or not isinstance(pairs, list) or not pairs:
        raise ValueError('配准清单需要schemaVersion:1和非空pairs')
    reports = []
    ids = set()
    for pair in pairs:
        pid = pair.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', pid) or pid in ids:
            raise ValueError('配准pair id无效或重复')
        ids.add(pid)
        ref, moving = [image_info(pair[k], base) for k in ('reference', 'moving')]
        if ref['floorId'] == moving['floorId']:
            raise ValueError('跨层配准不能引用同一楼层')
        mm = ref.get('mmPerPixel')
        if mm is not None and (not finite(mm) or mm <= 0 or not ref.get('scaleEvidence')):
            raise ValueError('mmPerPixel需要正值与scaleEvidence；否则只报告像素残差')
        limits = pair.get('limits', {})
        for key in ('rmsCheckPx', 'maxCheckPx'):
            if not finite(limits.get(key)) or limits[key] <= 0:
                raise ValueError('必须显式指定正的rmsCheckPx/maxCheckPx图像工作阈值')
        used_ids = set()
        used_ref, used_moving = set(), set()
        for kind in ('fitPoints', 'checkPoints'):
            rows = pair.get(kind)
            if not isinstance(rows, list) or len(rows) < 2:
                raise ValueError(kind+'至少需要两个点；复核点不得参与拟合')
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                    raise ValueError('对应点需要非空id')
                if row['id'] in used_ids:
                    raise ValueError('拟合/复核点id重复，不能把同一证据重复计分')
                used_ids.add(row['id'])
                for key, image, used in [('referencePx', ref, used_ref), ('movingPx', moving, used_moving)]:
                    p = row.get(key)
                    if not point(p) or not 0 <= p[0] < image['width'] or not 0 <= p[1] < image['height']:
                        raise ValueError('对应点坐标无效或超出原图')
                    if tuple(p) in used:
                        raise ValueError('拟合/复核坐标重复，不能换名后作为独立证据')
                    used.add(tuple(p))
        fit, checks = pair['fitPoints'], pair['checkPoints']
        src = np.array([r['movingPx'] for r in fit]); dst = np.array([r['referencePx'] for r in fit])
        if min(np.max(np.linalg.norm(p-p[0], axis=1)) for p in (src, dst)) < 1:
            raise ValueError('拟合点基线过短或退化')
        # 留出点须覆盖拟合基线以外的结构，避免只核对同一条边而漏掉镜像/竖向错误。
        combined = np.array([r['referencePx'] for r in fit+checks], dtype=float)
        moving_combined = np.array([r['movingPx'] for r in fit+checks], dtype=float)
        if any(np.linalg.matrix_rank(p-p.mean(axis=0), tol=1e-6) < 2 for p in (combined, moving_combined)):
            raise ValueError('拟合与复核点整体共线，需补充另一个方向的结构点')
        model = SimilarityTransform()
        if not model.estimate(src, dst) or not np.isfinite(model.params).all():
            raise ValueError('相似变换拟合失败')
        fit_metrics = metrics(model, fit, mm)
        check_metrics = metrics(model, checks, mm)
        def within(m):
            return m['rmsPx'] <= limits['rmsCheckPx'] and m['maxPx'] <= limits['maxCheckPx']
        proposal_pass = within(fit_metrics) and within(check_metrics)
        current = None
        if 'plannedTransform' in pair or 'buildingFile' in pair:
            matrix, provenance = (building_transform(pair,base,ref,moving,np) if 'buildingFile' in pair else
                                  (validate_matrix(pair['plannedTransform'],np),{'kind':'declared_only'}))
            planned = SimilarityTransform(matrix=matrix)
            current = {'matrixMovingToReference':planned.params.tolist(),
                       'fit':metrics(planned, fit, mm), 'checks':metrics(planned, checks, mm), 'provenance':provenance}
            current['withinWorkingLimits'] = within(current['fit']) and within(current['checks'])
        status = ('within_working_limits' if current['withinWorkingLimits'] and proposal_pass
                  else 'review_required') if current else 'proposal_only'
        reports.append({'id':pid, 'reference':ref, 'moving':moving, 'limits':limits, 'status':status,
                        'fit':fit_metrics, 'checks':check_metrics, 'currentAlignment':current,
                        'proposal':{'matrixMovingToReference':model.params.tolist(), 'scale':float(model.scale),
                                    'rotationDegrees':math.degrees(model.rotation),
                                    'translationReferencePx':model.translation.tolist(),
                                    'withinWorkingLimits':proposal_pass, 'appliedToBuilding':False},
                        'evidence':pair.get('evidence', ''),
                        'caution':'记录人工确认的源图对应点一致性；不是识图准确率、现场毫米精度或楼层结构相同的证明'})
    return {'schemaVersion':1, 'library':{'name':'scikit-image', 'version':skimage.__version__,
            'method':'SimilarityTransform.estimate/residuals; fit/check split; no nonrigid deformation'},
            'status':'within_working_limits' if all(p['status']=='within_working_limits' for p in reports)
                     else 'review_required', 'pairs':reports, 'automaticallyApplied':False,
            'scope':'仅审计指定源图结构点；不自动识别同名结构，不修改计划/图片/楼层偏移'}


def write_review(report, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    for pair in report['pairs']:
        ref, moving = pair['reference'], pair['moving']
        images = []
        for image in (ref, moving):
            data = base64.b64encode(Path(image['path']).read_bytes()).decode()
            images.append(f'data:{image["mime"]};base64,{data}')
        for kind, transform in [('current', pair['currentAlignment']), ('proposed', pair['proposal'])]:
            if transform is None:
                continue
            m = transform['matrixMovingToReference']
            matrix = f'{m[0][0]} {m[1][0]} {m[0][1]} {m[1][1]} {m[0][2]} {m[1][2]}'
            svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{ref["width"]}" height="{ref["height"]}" viewBox="0 0 {ref["width"]} {ref["height"]}">',
                   f'<rect width="100%" height="100%" fill="white"/><image href="{images[0]}" width="{ref["width"]}" height="{ref["height"]}" opacity=".5"/>',
                   f'<image href="{images[1]}" width="{moving["width"]}" height="{moving["height"]}" opacity=".5" transform="matrix({matrix})"/>']
            for row in pair['checks']['points'] if kind=='proposed' else transform['checks']['points']:
                x, y = row['referencePx']; u, v = row['predictedReferencePx']
                svg += [f'<circle cx="{x}" cy="{y}" r="4" fill="none" stroke="#159456" stroke-width="2"/>',
                        f'<circle cx="{u}" cy="{v}" r="2" fill="#cc5933"/><path d="M{x},{y}L{u},{v}" stroke="#cc5933" stroke-width="2"/>',
                        f'<text x="{x+6}" y="{y-5}" font-size="12" fill="#805020">{html.escape(row["id"])}</text>']
            svg.append('</svg>')
            target = output.with_name(pair['id']+'-'+kind+'.svg'); target.write_text('\n'.join(svg))
        current = pair['currentAlignment']
        rows = ''.join(f'<tr><td>{html.escape(r["id"])}</td><td>{r["errorPx"]:.3f}px</td></tr>' for r in pair['checks']['points'])
        current_text = (f'{"当前建筑" if current["provenance"]["kind"]=="building_derived" else "声明变换（未核对建筑）"}复核 RMS {current["checks"]["rmsPx"]:.3f}px，最大 {current["checks"]["maxPx"]:.3f}px'
                        if current else '未提供当前计划变换，只有建议，不能验收现有楼层对齐')
        sections.append(f'<section><h2>{html.escape(pair["id"])} · {pair["status"]}</h2><p>{current_text}</p>'
                        f'<p>拟合残差 RMS {pair["fit"]["rmsPx"]:.3f}px；未参与拟合的复核点 RMS {pair["checks"]["rmsPx"]:.3f}px，最大 {pair["checks"]["maxPx"]:.3f}px。</p>'
                        f'<p>建议比例 {pair["proposal"]["scale"]:.6f}，旋转 {pair["proposal"]["rotationDegrees"]:.4f}°；仅记录，没有自动应用。</p>'
                        f'<table><tr><th>复核点</th><th>建议变换后残差</th></tr>{rows}</table>'
                        f'<div class="pair">'+('' if current is None else f'<div>当前计划配准<img src="{pair["id"]}-current.svg"></div>')
                        +f'<div>建议相似变换<img src="{pair["id"]}-proposed.svg"></div></div>'
                        f'<p>{html.escape(pair["evidence"])}</p><small>{html.escape(pair["caution"])}</small></section>')
    output.write_text('<!doctype html><meta charset="utf-8"><title>多层原图配准审计</title>'
                     '<style>body{background:#f4f1e9;color:#343831;font:16px/1.7 system-ui;max-width:1300px;margin:30px auto;padding:20px}section{background:white;border:1px solid #ddd;padding:20px;margin:20px 0}.pair{display:flex;gap:20px}.pair>div{width:50%}img{width:100%}td,th{padding:4px 20px;text-align:left}small{color:#875333}@media(max-width:700px){.pair{display:block}.pair>div{width:100%}}</style>'
                     '<h1>多层原图配准：拟合、复核与当前计划分开记录</h1><p>绿色圆圈为参考图观测点，橙色点为另一层变换后的对应点。图内保留原图标注；不同楼层的室内布局本来可以不同。像素阈值是审核工作值，不能据此承诺现场精度；不使用局部形变强迫不同楼层吻合。</p>'
                     +''.join(sections))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest'); parser.add_argument('--report', required=True)
    parser.add_argument('--html')
    args = parser.parse_args(); source = Path(args.manifest)
    report = analyze(json.loads(source.read_text()), source.resolve().parent)
    report['manifestSha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    target = Path(args.report); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    if args.html:
        write_review(report, args.html)
    print(report['status'])
    # 审计报告仍保留，但流水线不能把待复核状态当成通过。
    return 0 if report['status'] == 'within_working_limits' else 2


if __name__ == '__main__':
    raise SystemExit(main())
