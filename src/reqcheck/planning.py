"""Bound response size without dropping model or requirement context."""
from .issues import merge_issues


def batches(requirements, size):
    if type(size) is not int or size < 1:
        raise ValueError('requirement_batch_size must be a positive integer')
    return [requirements[i:i+size] for i in range(0, len(requirements), size)]


def review_batches(requirements, passes, size):
    """Keep requirements linked by an issue together, even above the soft size."""
    batches(requirements, size)  # validate even for empty input
    parent = {r.id:r.id for r in requirements}
    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    for p in passes:
        for issue in p.get('issues', []):
            ids = [r for r in issue['requirement_ids'] if r in parent]
            for rid in ids[1:]: parent[root(rid)] = root(ids[0])
    groups = {}
    for req in requirements: groups.setdefault(root(req.id), []).append(req)
    result, current = [], []
    for group in groups.values():
        if current and len(current)+len(group)>size:
            result.append(current); current=[]
        if len(group)>size:
            result.append(group)
        else:
            current.extend(group)
    if current: result.append(current)
    return result


def compact_history(passes, requirements):
    """Full text is already in source inputs; history needs references, not copies."""
    ids={r.id for r in requirements}
    observations=merge_issues([i for p in passes if p['stage']!='review' for i in p.get('issues',[])
                               if ids.intersection(i['requirement_ids'])])
    def refs(evidence):
        return [{k:e[k] for k in ('artifact_id','start_line','end_line')} for e in evidence]
    compact=[]
    for issue in observations:
        item={k:v for k,v in issue.items() if k not in ('evidence','observed_in','status','location')}
        item['location']={k:v for k,v in (issue['location'] or {}).items() if k!='file'} if issue['location'] else None
        item['evidence_refs']=refs(issue['evidence'])
        compact.append(item)
    assessment_rows=[]
    for p in passes:
        if p['stage']=='review': continue
        for row in p.get('assessments',[]):
            if row['requirement_id'] not in ids: continue
            item={k:row[k] for k in ('requirement_id','coverage','consistency','summary','missing_dimensions','review_notes')}
            for key in ('checks','coverage_scope','unchecked_dimensions'):
                if key in row: item[key]=row[key]
            item.update(stage=p['stage'],evidence_refs=refs(row['evidence']))
            assessment_rows.append(item)
    return [{'stage':'collected','issues':compact,'assessments':assessment_rows}]
