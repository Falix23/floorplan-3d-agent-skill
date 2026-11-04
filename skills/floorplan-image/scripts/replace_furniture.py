"""把 Agent 已选定的家具资产事务式替换到户型；不负责图像识别或在线检索。"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from asset_validation import validate_asset
from furniture_assets import attach, write_json
from plan_tools import build, validate
from reconstruct import refine_plan

SET_FIELDS = {'type', 'name', 'cx', 'cy', 'w', 'd', 'height', 'rot', 'color', 'placement'}
DIMENSION_FIELDS = {'w', 'd', 'height'}
PLACEMENT_FIELDS = {'cx', 'cy', 'rot', 'placement'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_plan(path):
    """同时接收计划 JSON 与页面导出的 {plan: ...} 完整备份。"""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and 'plan' in data and 'schemaVersion' not in data:
        require(not data.get('demolished') and not data.get('measures'),
                '完整导出含拆墙/测量状态，事务入口不能丢弃这些状态；请保留原备份，改在已有页面导入资产完成替换')
        data = data['plan']
    validate(data)
    return data


def replacement_rows(manifest, plan):
    require(isinstance(manifest, dict) and manifest.get('schemaVersion') == 1,
            '替换清单需要 schemaVersion:1')
    require(set(manifest) <= {'schemaVersion', 'replacements'}, '替换清单存在未知字段')
    rows = manifest.get('replacements')
    require(isinstance(rows, list) and bool(rows), 'replacements 需要非空数组')
    known = {f['id'] for f in plan['furniture']}
    seen = set()
    allowed = {'itemId', 'decision', 'asset', 'reason', 'set', 'dimensionsEvidence',
               'placementEvidence', 'allowOverlap'}
    for row in rows:
        require(isinstance(row, dict) and not set(row) - allowed, '替换条目不是对象或存在未知字段')
        fid = row.get('itemId')
        require(isinstance(fid, str) and fid in known and fid not in seen,
                '家具 itemId 不存在或在清单中重复: ' + str(fid))
        seen.add(fid)
        require(isinstance(row.get('reason'), str) and bool(row['reason'].strip()),
                fid + ' 缺少选择或保留原家具的 reason')
        require(row.get('decision') in ('replace', 'keep'), fid + ' decision 需要 replace/keep')
        if row['decision'] == 'keep':
            require(set(row) <= {'itemId', 'decision', 'reason'}, fid + ' keep 不能同时修改字段或绑定资产')
            continue
        require(isinstance(row.get('asset'), str) and bool(row['asset'].strip()), fid + ' 缺少资产 JSON 路径')
        changes = row.get('set', {})
        require(isinstance(changes, dict) and not set(changes) - SET_FIELDS,
                fid + ' set 含不支持字段；不能直接修改 id/assetId 或其他实例')
        if DIMENSION_FIELDS & set(changes):
            require(isinstance(row.get('dimensionsEvidence'), str) and bool(row['dimensionsEvidence'].strip()),
                    fid + ' 改尺寸需要 dimensionsEvidence')
        if PLACEMENT_FIELDS & set(changes):
            require(isinstance(row.get('placementEvidence'), str) and bool(row['placementEvidence'].strip()),
                    fid + ' 改位置/朝向/贴墙规则需要 placementEvidence')
        overlap = row.get('allowOverlap', [])
        require(isinstance(overlap, list), fid + ' allowOverlap 需要数组')
        others = set()
        for entry in overlap:
            require(isinstance(entry, dict) and set(entry) == {'with', 'reason'},
                    fid + ' 允许叠放条目需要 with/reason')
            other = entry['with']
            require(isinstance(other, str) and other in known and other != fid and other not in others,
                    fid + ' 叠放对象不存在、自指或重复')
            require(isinstance(entry['reason'], str) and bool(entry['reason'].strip()), fid + ' 叠放缺少依据')
            others.add(other)
    return rows


def aspect_notice(item, asset):
    """仅报告相对源网格的逐轴缩放，不把工作尺寸写成照片测量。"""
    extents = asset.get('alignment', {}).get('sourceExtents')
    if not (isinstance(extents, list) and len(extents) == 3 and
            all(isinstance(x, (int, float)) and not isinstance(x, bool)
                and math.isfinite(x) and x > 0 for x in extents)):
        return {'status': 'unknown', 'notice': '资产缺少原始比例；必须在实际页面检查形变。'}
    factors = [item['w'] / extents[0], item['height'] / extents[1], item['d'] / extents[2]]
    ratio = max(factors) / min(factors)
    return {'status': 'uniform' if ratio <= 1.03 else 'nonuniform',
            'maxMinScaleRatio': ratio,
            'notice': '尺寸为调用方的工作尺寸；非均匀缩放需视觉复核，不能冒充原款尺寸。'}


def replace_plan(plan, manifest, asset_base):
    """只在整批预检通过时返回新计划；失败返回 None 和可追溯报告，输入始终不变。"""
    report = {'ok': False, 'status': 'rejected', 'items': [], 'errors': [], 'warnings': [],
              'inferencePerformed': False, 'onlineSearchPerformed': False,
              'browserVerification': {'status': 'not_run', 'required': [
                  '照片与候选结构、左右和材质差异复核', '实际2D/3D占地、朝向及落地',
                  '移动/旋转/尺寸编辑与撤销', '完整JSON导出导入与刷新保存结果']}}
    try:
        report['baselineWarnings'] = validate(plan)
        rows = replacement_rows(manifest, plan)
        base, _ = refine_plan(plan)
        require(base == plan, '基础计划尚有未应用的贴墙校正；请先单独校正、复核并保存基线，不在替换时移动其他家具')
        result = copy.deepcopy(plan)
        targets, allowances = set(), {}
        originals = {f['id']: f for f in plan['furniture']}
        for row in rows:
            fid = row['itemId']
            record = {'itemId': fid, 'decision': row['decision'], 'reason': row['reason']}
            report['items'].append(record)
            if row['decision'] == 'keep':
                record['status'] = 'kept'
                continue
            record['status'] = 'validating'
            asset_path = Path(row['asset'])
            if not asset_path.is_absolute():
                asset_path = Path(asset_base) / asset_path
            asset_bytes = asset_path.read_bytes()
            asset = json.loads(asset_bytes)
            record['assetSummary'] = validate_asset(asset)
            record['assetJsonSha256'] = hashlib.sha256(asset_bytes).hexdigest()
            result = attach(result, asset, fid)
            item = next(f for f in result['furniture'] if f['id'] == fid)
            item.update(copy.deepcopy(row.get('set', {})))
            validate(result)
            record.update(assetId=asset['id'], status='prepared',
                          dimensionsEvidence=row.get('dimensionsEvidence'),
                          placementEvidence=row.get('placementEvidence'))
            record['aspect'] = aspect_notice(item, asset)
            if record['aspect']['status'] != 'uniform':
                report['warnings'].append(fid + ': ' + record['aspect']['notice'])
            if asset['provenance']['license'].strip().lower() == 'unknown':
                report['warnings'].append(fid + ': 模型许可未知，不能据此认定可公开再分发')
            targets.add(fid)
            allowances[fid] = {v['with']: v['reason'] for v in row.get('allowOverlap', [])}
        result, adjustments = refine_plan(result)
        require(all(f == originals[f['id']] for f in result['furniture'] if f['id'] not in targets),
                '贴墙校正会修改非目标家具；请先建立已复核的共同基线')
        report['anchorAdjustments'] = [a for a in adjustments if a['id'] in targets]
        report['warnings'].extend(validate(result))
        if targets:
            from furniture_placement import screen_replacements
            screening = screen_replacements(result, targets, allowances)
            report['placementScreen'] = screening
            report['warnings'].extend(screening.get('warnings', []))
            if not screening['ok']:
                report['errors'].extend(screening['errors'])
                return None, report
        else:
            report['placementScreen'] = {'ok': True, 'checks': [], 'notice': '本次全部保留原家具，无几何变化'}
        for record in report['items']:
            if record['decision'] != 'replace':
                continue
            after = next(f for f in result['furniture'] if f['id'] == record['itemId'])
            before = originals[record['itemId']]
            record['changes'] = {k: {'before': before.get(k), 'after': after.get(k)}
                                 for k in set(before) | set(after) if before.get(k) != after.get(k)}
            record['status'] = 'validated_pending_browser'
        report.update(ok=True, status='validated_pending_browser',
                      unchangedNonTargetCount=len(originals) - len(targets))
        report['warnings'] = list({json.dumps(w, ensure_ascii=False, sort_keys=True): w
                                   for w in report['warnings']}.values())
        return result, report
    except (ValueError, KeyError, TypeError, OSError, ImportError) as exc:
        report['errors'].append({'kind': 'validation', 'detail': str(exc)})
        return None, report


def write_bundle(plan, result, manifest, report, output):
    """新目录整体落盘；构建失败不留下貌似成功的半套 plan/HTML。"""
    target = Path(output)
    require(not target.exists(), '输出目录已存在；请选择新目录，避免覆盖用户方案')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.' + target.name + '-', dir=str(target.parent)))
    try:
        build(result, temporary / 'index.html')
        payload = re.search(r'/\* PLAN_DATA_START \*/(.*?)/\* PLAN_DATA_END \*/',
                            (temporary / 'index.html').read_text(), re.S)
        require(payload is not None and json.loads(payload.group(1)) == result,
                'HTML构建修改了已验证计划；拒绝输出JSON/HTML不一致的方案')
        write_json(temporary / 'before.plan.json', plan)
        write_json(temporary / 'plan.json', result)
        # 清单必须可随产物移动并重新执行，不能继续依赖原目录中的相对资产路径。
        portable_manifest = copy.deepcopy(manifest)
        bundled_assets = set()
        for row in portable_manifest['replacements']:
            if row['decision'] != 'replace':
                continue
            item = next(f for f in result['furniture'] if f['id'] == row['itemId'])
            aid = item['assetId']
            relative = 'assets/' + aid + '.json'
            write_json(temporary / relative, result['furnitureAssets'][aid])
            bundled_assets.add(relative)
            row['asset'] = relative
        write_json(temporary / 'manifest.json', portable_manifest)
        report = copy.deepcopy(report)
        report['outputFiles'] = ['before.plan.json', 'plan.json', 'index.html',
                                 'manifest.json', 'replacement-report.json'] + sorted(bundled_assets)
        report['output'] = str(target)
        report['status'] = 'written_pending_browser'
        write_json(temporary / 'replacement-report.json', report)
        require(not target.exists(), '输出目录已存在；不覆盖其他方案')
        os.rename(temporary, target)
        return report
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, help='计划JSON或实际HTML导出的完整JSON')
    parser.add_argument('--manifest', required=True, help='Agent记录的replace/keep清单，相对资产路径以此文件为基准')
    parser.add_argument('--output', required=True, help='不存在的新目录，失败不产生半套输出')
    parser.add_argument('--dry-run', action='store_true', help='只预检并输出报告，不写任何文件')
    args = parser.parse_args()
    report = {'ok': False, 'status': 'rejected', 'errors': []}
    try:
        require(not Path(args.output).exists(), '输出目录已存在；请选择新目录')
        plan = read_plan(args.plan)
        manifest = json.loads(Path(args.manifest).read_text())
        result, report = replace_plan(plan, manifest, Path(args.manifest).resolve().parent)
        if report['ok'] and not args.dry_run:
            report = write_bundle(plan, result, manifest, report, args.output)
        elif report['ok']:
            report['status'] = 'dry_run_passed_pending_browser'
    except (ValueError, KeyError, TypeError, OSError, ImportError) as exc:
        report.update(ok=False, status='rejected')
        report.setdefault('errors', []).append({'kind': 'validation', 'detail': str(exc)})
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
