"""Select actual request boundaries; retrieval links are candidates, not truth."""

def select_models(requirements, models, links, passes=()):
    ids={r.id for r in requirements}
    selected={l['model_id'] for l in links if l['requirement_id'] in ids}
    for p in passes:
        for issue in p.get('issues',[]):
            if ids.intersection(issue['requirement_ids']):
                selected.update(e['artifact_id'] for e in issue['evidence'])
                if issue.get('model_id'): selected.add(issue['model_id'])
        for row in p.get('assessments',[]):
            if row['requirement_id'] in ids and row['coverage']!='不适用': selected.update(row['model_ids'])
    return [m for m in models if m.id in selected]


def scope_groups(requirements, models, links, passes=()):
    """Keep shared diagrams/issues together; do not pack unrelated groups."""
    parent={r.id:r.id for r in requirements}
    def root(r):
        while parent[r]!=r:
            parent[r]=parent[parent[r]]; r=parent[r]
        return r
    by_model={}
    for r in requirements:
        for m in select_models([r],models,links,passes):
            if m.id in by_model: parent[root(r.id)]=root(by_model[m.id])
            else: by_model[m.id]=r.id
    for p in passes:
        for issue in p.get('issues',[]):
            ids=[r for r in issue['requirement_ids'] if r in parent]
            for rid in ids[1:]: parent[root(rid)]=root(ids[0])
    groups={}
    for r in requirements: groups.setdefault(root(r.id),[]).append(r)
    return list(groups.values())


def recover_links(audit, known_links, model):
    """Only source-verified observations can suggest an omitted candidate link."""
    known={(l['requirement_id'],l['model_id']) for l in known_links}
    evidence={}
    for row in audit.get('assessments',[]):
        if not row.get('validation_errors') and row['coverage']!='不适用' and model.id in row['model_ids']:
            evidence.setdefault(row['requirement_id'],[]).extend(row['evidence'])
    for issue in audit.get('issues',[]):
        for rid in issue['requirement_ids']: evidence.setdefault(rid,[]).extend(issue['evidence'])
    recovered=[]
    for rid,rows in evidence.items():
        if (rid,model.id) in known: continue
        if not {rid,model.id}.issubset({e['artifact_id'] for e in rows if e.get('verified')}): continue
        recovered.append({'requirement_id':rid,'model_id':model.id,'score':None,
                          'method':'model-reverse-recovery','status':'反向检查补充候选，非人工确认的关联',
                          'evidence':[e for e in rows if e['artifact_id'] in (rid,model.id)]})
    return recovered


def model_batches(models, max_models=4, max_source_chars=24000):
    """Pack whole short diagrams; never truncate or fragment their paths."""
    if type(max_models)!=int or max_models<1 or type(max_source_chars)!=int or max_source_chars<1:
        raise ValueError('Invalid reverse batch limits')
    result=[]; current=[]; size=0
    for model in models:
        if current and (len(current)>=max_models or size+len(model.text)>max_source_chars):
            result.append(current); current=[]; size=0
        current.append(model); size+=len(model.text)
    if current: result.append(current)
    return result
