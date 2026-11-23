"""Issue-level contracts. Exact citations validate provenance, not reasoning."""
from copy import deepcopy
import hashlib
import json

ISSUE_TYPES = ('表达错误', '疑似遗漏', '跨图冲突', '信息不足')


def checked_evidence(rows, requirements, models, required_ids, required_models=()):
    artifacts = {a.id: a for a in requirements+models}
    requirement_ids = {a.id for a in requirements}
    if not isinstance(rows, list):
        raise ValueError('evidence must be an array')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid evidence')
        aid = row.get('artifact_id')
        if not isinstance(aid,str):
            raise ValueError('Invalid evidence artifact ID')
        a = artifacts.get(aid)
        start, end = row.get('start_line'), row.get('end_line')
        if a is None or (aid in requirement_ids and aid not in required_ids):
            raise ValueError('Unknown or unrelated evidence artifact')
        if type(start) is not int or type(end) is not int or not a.start_line <= start <= end <= a.end_line:
            raise ValueError('Evidence line out of range')
        quote = '\n'.join(a.text.splitlines()[start-a.start_line:end-a.start_line+1])
        if not isinstance(row.get('quote'), str) or row['quote'].replace('\r\n', '\n') != quote or not quote.strip():
            raise ValueError('Evidence quote mismatch')
        result.append({'artifact_id': aid, 'start_line': start, 'end_line': end, 'quote': quote,
                       'verified': True, 'source': {'file': a.file, 'start_line': start, 'end_line': end}})
    cited = {e['artifact_id'] for e in result}
    if not set(required_ids).issubset(cited) or not set(required_models).issubset(cited):
        raise ValueError('Missing required source evidence')
    return result


def validate_issues(raw, requirements, models):
    """Quarantine invalid issues individually, retaining valid neighbours."""
    if not isinstance(raw, list):
        return [], [{'index': None, 'reason': 'issues must be an array'}]
    valid, rejected = [], []
    reqs = {a.id: a for a in requirements}
    diagrams = {a.id: a for a in models}
    for index, row in enumerate(raw):
        try:
            if not isinstance(row, dict):
                raise ValueError('Issue must be an object')
            kind = row.get('type')
            if kind not in ISSUE_TYPES:
                raise ValueError('Invalid issue type')
            rids = row.get('requirement_ids')
            if not isinstance(rids, list) or not rids or not all(isinstance(r, str) and r in reqs for r in rids):
                raise ValueError('Invalid issue requirement_ids')
            searched = row.get('searched_model_ids')
            if not isinstance(searched, list) or not searched or not all(isinstance(m, str) and m in diagrams for m in searched):
                raise ValueError('Invalid searched_model_ids')
            for field in ('title', 'expected', 'observed', 'recommendation'):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError('Missing issue '+field)
            missing = row.get('missing_content', '')
            if not isinstance(missing, str) or (kind == '疑似遗漏' and not missing.strip()):
                raise ValueError('Missing omitted content description')
            path = row.get('path', [])
            if not isinstance(path, list) or not all(isinstance(s, str) for s in path):
                raise ValueError('Invalid issue path')
            origins = row.get('origin_ids', [])
            if not isinstance(origins, list) or not all(isinstance(s, str) for s in origins):
                raise ValueError('Invalid origin_ids')
            mid, loc = row.get('model_id'), row.get('location')
            if mid is None:
                if kind in ('表达错误', '跨图冲突') or loc is not None:
                    raise ValueError('Conflict requires a diagram location')
                location = None
            else:
                if not isinstance(mid, str) or mid not in diagrams or mid not in searched or not isinstance(loc, dict):
                    raise ValueError('Invalid primary model/location')
                a = diagrams[mid]
                start, end = loc.get('start_line'), loc.get('end_line')
                if type(start) is not int or type(end) is not int or not a.start_line <= start <= end <= a.end_line:
                    raise ValueError('Issue location out of range')
                location_kind = '错误位置' if kind in ('表达错误', '跨图冲突') else '建议补充位置'
                if loc.get('kind') != location_kind:
                    raise ValueError('Omission anchor must not be an error line')
                element = loc.get('element', '')
                if not isinstance(element, str):
                    raise ValueError('Invalid element description')
                location = {'kind': location_kind, 'file': a.file, 'start_line': start, 'end_line': end,
                            'element': element, 'diagram_type': a.diagram_type}
            evidence = checked_evidence(row.get('evidence'), requirements, models, rids, [mid] if mid else [])
            covered_lines={line for e in evidence if e['artifact_id']==mid for line in range(e['start_line'],e['end_line']+1)}
            if location and not set(range(location['start_line'],location['end_line']+1)).issubset(covered_lines):
                raise ValueError('Primary location lacks covering evidence')
            item = {k: row[k] for k in ('type', 'title', 'expected', 'observed', 'recommendation')}
            for key in ('dimension','check_ids','declared_dimension','dimension_source'):
                if key in row: item[key]=deepcopy(row[key])
            item.update(requirement_ids=sorted(set(rids)), model_id=mid, location=location,
                        searched_model_ids=sorted(set(searched)), path=path, missing_content=missing,
                        evidence=evidence, origin_ids=sorted(set(origins)), status='待复核')
            fingerprint = [mid, location and [location['kind'], location['start_line'], location['end_line']],
                           kind, item['expected'].strip(), item['observed'].strip(), missing.strip()]
            item['id'] = 'ISSUE-'+hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False).encode()).hexdigest()[:12]
            valid.append(item)
        except ValueError as exc:
            # These errors are locally generated constants, never provider text.
            rejected.append({'index': index, 'reason': str(exc)})
    return valid, rejected


def merge_issues(issues):
    """Only exact semantic/position matches merge; no name-based guessing."""
    merged = {}
    for original in issues:
        item = deepcopy(original)
        if item['id'] not in merged:
            merged[item['id']] = item
            continue
        old = merged[item['id']]
        for key in ('requirement_ids', 'searched_model_ids', 'origin_ids'):
            old[key] = sorted(set(old[key]+item[key]))
        for key in ('evidence', 'observed_in'):
            for entry in item.get(key, []):
                if entry not in old.setdefault(key, []):
                    old[key].append(entry)
    return list(merged.values())


def reconcile_issues(passes, requirements, models):
    """A failed/empty review cannot silently erase earlier supported findings."""
    observations = merge_issues([i for p in passes if p['stage'] != 'review' for i in p.get('issues', [])])
    known = {i['id']: i for i in observations}
    reviews = [p for p in passes if p['stage'] == 'review']
    if not reviews:
        return observations, [], []
    review = {'issues':[i for p in reviews for i in p.get('issues',[])],
              'dismissed_issues':[i for p in reviews for i in p.get('dismissed_issues',[])]}
    output, dismissed, errors, handled = [], [], [], set()
    for issue in merge_issues(review.get('issues', [])):
        origins = set(issue['origin_ids'])
        if issue['id'] in known:
            origins.add(issue['id'])
        if not origins.issubset(known):
            errors.append('复核问题引用了未知来源问题ID。')
            continue
        covered = [known[o] for o in origins]
        required = set(issue['requirement_ids']) | {r for i in covered for r in i['requirement_ids']}
        if not required.issubset({e['artifact_id'] for e in issue['evidence']}):
            errors.append('合并问题未保留全部需求依据。')
            continue
        handled.update(origins)
        issue['status'] = '模型复核完成'
        issue['origin_ids'] = sorted(origins)
        output.append(issue)
    for decision in review.get('dismissed_issues', []):
        try:
            if not isinstance(decision, dict):
                raise ValueError('Invalid dismissal')
            origins = decision.get('origin_ids')
            if not isinstance(origins, list) or not origins or not all(isinstance(o, str) and o in known and o not in handled for o in origins):
                raise ValueError('Unknown or conflicting dismissal origins')
            if not isinstance(decision.get('reason'), str) or not decision['reason'].strip():
                raise ValueError('Missing dismissal reason')
            rids = {r for o in origins for r in known[o]['requirement_ids']}
            mids = {known[o]['model_id'] for o in origins if known[o]['model_id']}
            ev = checked_evidence(decision.get('evidence'), requirements, models, rids, mids)
            dismissed.append({'origin_ids': origins, 'reason': decision['reason'], 'evidence': ev})
            handled.update(origins)
        except ValueError:
            errors.append('排除旧问题缺少有效来源ID、理由或原文证据，旧问题仍保留。')
    for oid, issue in known.items():
        if oid not in handled:
            issue['status'] = '待复核'
            output.append(issue)
    return merge_issues(output), dismissed, errors


def issue_summary(issues):
    return {'total': len(issues),
            'conflicts': sum(i['type'] in ('表达错误', '跨图冲突') for i in issues),
            'suspected_omissions': sum(i['type'] == '疑似遗漏' for i in issues),
            'information_gaps': sum(i['type'] == '信息不足' for i in issues),
            'pending_review': sum(i['status'] == '待复核' for i in issues),
            'reviewed': sum(i['status'] == '模型复核完成' for i in issues)}
