"""Stable checkpoint, task and source-reference contracts."""
from copy import deepcopy
import hashlib
import json
from .applicability import DIMENSIONS, apply_policy
from .contracts import COVERAGE, CONSISTENCY
from .issues import validate_issues


def hydrate_evidence(refs, artifacts):
    by_id={a.id:a for a in artifacts}; out=[]
    if not isinstance(refs,list): raise ValueError('Evidence must be an array')
    for ref in refs:
        if not isinstance(ref,dict) or not isinstance(ref.get('artifact_id'),str): raise ValueError('Invalid evidence reference')
        a=by_id.get(ref['artifact_id']); start=ref.get('start_line'); end=ref.get('end_line')
        if a is None or type(start) is not int or type(end) is not int or not a.start_line<=start<=end<=a.end_line:
            raise ValueError('Evidence reference outside provided source')
        out.append({'artifact_id':a.id,'start_line':start,'end_line':end,
                    'quote':'\n'.join(a.text.splitlines()[start-a.start_line:end-a.start_line+1])})
    return out


def prepare_checkpoints(response, requirements):
    rows=response.get('requirements') if isinstance(response,dict) else None
    if not isinstance(rows,list): raise ValueError('Missing requirement preparation')
    reqs={r.id:r for r in requirements}; seen=set(); points=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('requirement_id'),str): raise ValueError('Invalid requirement ID')
        rid=row['requirement_id']
        if rid not in reqs or rid in seen: raise ValueError('Duplicate/unknown prepared requirement')
        seen.add(rid); checks=row.get('checks')
        if not isinstance(checks,list) or not checks: raise ValueError('Missing checkpoints')
        for n,c in enumerate(checks,1):
            if not isinstance(c,dict) or not isinstance(c.get('dimension'),str) or c['dimension'] not in DIMENSIONS or not isinstance(c.get('text'),str) or not c['text'].strip():
                raise ValueError('Invalid checkpoint')
            evidence=hydrate_evidence([{'artifact_id':rid,'start_line':c.get('start_line'),'end_line':c.get('end_line')}],[reqs[rid]])
            points.append({'id':rid+'.c'+str(n),'requirement_id':rid,'text':c['text'],
                           'dimension':c['dimension'],'evidence':evidence})
    if seen!=set(reqs): raise ValueError('Preparation omitted requirements')
    return points


def plan_tasks(requirements, models, links, options, max_requirements=None, max_chars=None):
    max_requirements=max_requirements or options.get('task_requirements',6)
    max_chars=max_chars or options.get('task_source_chars',30000)
    if type(max_requirements) is not int or max_requirements<1 or type(max_chars) is not int or max_chars<100:
        raise ValueError('Invalid task limits')
    tasks=[]
    for r in requirements:
        mids={l['model_id'] for l in links if l['requirement_id']==r.id}
        selected=[m for m in models if m.id in mids]
        if not selected: continue
        # Keep a requirement's candidate context together. Never trim a long graph.
        size=len(r.text)+sum(len(m.text) for m in selected)
        key=tuple(m.id for m in selected)
        match=next((t for t in tasks if tuple(t['model_ids'])==key and len(t['requirement_ids'])<max_requirements
                    and t['source_chars']+len(r.text)<=max_chars),None)
        if match:
            match['requirement_ids'].append(r.id); match['source_chars']+=len(r.text)
        else:
            tasks.append({'id':'TASK-'+str(len(tasks)+1),'requirement_ids':[r.id],'model_ids':list(key),
                          'source_chars':size,'oversized':size>max_chars})
    return tasks


def parse_task_result(response, checkpoints, requirements, models):
    if not isinstance(response,dict): raise ValueError('Expected task result object')
    points={c['id']:c for c in checkpoints}; reqs={r.id:r for r in requirements}; mids={m.id for m in models}
    errors=[]; results=[]; seen=set(); artifacts=requirements+models
    raw_results=response.get('results')
    if not isinstance(raw_results,list): raise ValueError('Missing results array')
    for index,row in enumerate(raw_results):
        try:
            if not isinstance(row,dict) or not isinstance(row.get('check_id'),str): raise ValueError('Invalid result')
            cid=row['check_id']; p=points.get(cid)
            if p is None: raise ValueError('Unknown checkpoint result')
            if row.get('coverage') not in COVERAGE or row.get('consistency') not in CONSISTENCY: raise ValueError('Invalid result labels')
            if not isinstance(row.get('summary'),str) or not row['summary'].strip(): raise ValueError('Missing result summary')
            ids=row.get('model_ids')
            if not isinstance(ids,list) or not all(isinstance(m,str) and m in mids for m in ids): raise ValueError('Invalid result model IDs')
            if type(row.get('needs_context',False)) is not bool: raise ValueError('Invalid context request')
            ev=hydrate_evidence(row.get('evidence',[]),artifacts)
            if row['coverage']!='不适用' and not any(e['artifact_id'] in mids for e in ev): raise ValueError('Missing model evidence')
            value={**deepcopy(row),'evidence':p['evidence']+ev}
            if cid in seen:
                previous=next(r for r in results if r['check_id']==cid)
                if any(previous[k]!=value[k] for k in ('coverage','consistency')):
                    raise ValueError('Duplicate checkpoint verdicts disagree')
                previous['model_ids']=sorted(set(previous['model_ids']+value['model_ids']))
                previous['needs_context']=previous.get('needs_context',False) or value.get('needs_context',False)
                if value['summary']!=previous['summary']:previous['summary']+='；'+value['summary']
                for e in value['evidence']:
                    if e not in previous['evidence']:previous['evidence'].append(e)
                previous['combined_result_rows']=previous.get('combined_result_rows',1)+1
            else:results.append(value);seen.add(cid)
        except (ValueError,TypeError) as exc:
            detail={'section':'results','index':index,'reason':str(exc)}
            if isinstance(row,dict) and isinstance(row.get('check_id'),str) and row['check_id'] in points:detail['check_id']=row['check_id']
            errors.append(detail)
    for cid in points.keys()-seen: errors.append({'section':'results','check_id':cid,'reason':'Missing valid checkpoint result'})
    issues=[]
    raw_issues=response.get('issues')
    if not isinstance(raw_issues,list):
        errors.append({'section':'issues','reason':'Missing issues array'});raw_issues=[]
    for index,item in enumerate(raw_issues):
        try:
            if not isinstance(item,dict): raise ValueError('Invalid issue')
            cids=item.get('check_ids')
            if not isinstance(cids,list) or not cids or not all(isinstance(c,str) and c in points for c in cids): raise ValueError('Unknown issue checkpoints')
            rids=sorted({points[c]['requirement_id'] for c in cids})
            if 'requirement_ids' in item and sorted(item['requirement_ids'])!=rids: raise ValueError('Issue requirement IDs disagree with stable checkpoints')
            mid=item.get('model_id');kind=item.get('type'); loc=None
            if mid is not None:
                loc={'kind':'错误位置' if kind in ('表达错误','跨图冲突') else '建议补充位置',
                     'start_line':item.get('start_line'),'end_line':item.get('end_line'),'element':item.get('element','')}
            ev=[]
            for cid in cids:
                for e in points[cid]['evidence']:
                    if e not in ev: ev.append(e)
            ev+=hydrate_evidence(item.get('evidence',[]),artifacts)
            ev=[e for n,e in enumerate(ev) if e not in ev[:n]]
            row={**deepcopy(item),'requirement_ids':rids,'expected':'；'.join(points[c]['text'] for c in cids),
                 'location':loc,'evidence':ev,'searched_model_ids':sorted(mids),'missing_content':item.get('missing_content',''),
                 'path':item.get('path',[]),'origin_ids':[],'dimension':points[cids[0]]['dimension']}
            valid,rejected=validate_issues([row],requirements,models)
            if rejected: raise ValueError(rejected[0]['reason'])
            value=valid[0];value['status']='主检查发现，未经人工确认'
            issues.append(value)
        except (ValueError,TypeError) as exc: errors.append({'section':'issues','index':index,'reason':str(exc)})
    # Reuse typed scope checks, but only for declared requirements and actual issues.
    assessments=[]
    for r in requirements:
        relevant=[p for p in checkpoints if p['requirement_id']==r.id]
        checks=[]
        for p in relevant:
            row=next((a for a in results if a['check_id']==p['id']),None)
            if not row: continue
            checks.append({'id':p['id'],'dimension':p['dimension'],'description':p['text'],
                           'applicability':'out_of_scope' if row['coverage']=='不适用' else 'applicable',
                           'verdict':'未核查' if row['coverage']=='不适用' else row['consistency'],
                           'reason':row['summary'],'missing_content':p['text'] if row['coverage']=='未发现覆盖' else ''})
        if checks:
            assessments.append({'requirement_id':r.id,'checks':checks,'coverage':'部分覆盖','consistency':'无法判断','summary':'',
                                'model_ids':sorted(mids),'evidence':[],'missing_dimensions':[],'review_notes':[]})
    scoped,decisions,exclusions,scope_errors=apply_policy({'assessments':assessments,'issues':issues},requirements,models)
    errors.extend({'section':'scope',**e} for e in scope_errors)
    typed={c['id']:c for a in scoped['assessments'] for c in a.get('checks',[])}
    for row in results:
        c=typed.get(row['check_id'])
        if c and c['applicability']=='out_of_scope': row.update(coverage='不适用',consistency='无法判断')
    issues=scoped['issues']
    for row in results:
        if row['consistency']=='存在冲突' and not any(row['check_id'] in i['check_ids'] for i in issues):
            errors.append({'section':'issues','check_id':row['check_id'],'reason':'Conflict has no valid located issue'})
    notes=[];advisory_errors=[]
    for index,note in enumerate(response.get('quality_notes',[]) if isinstance(response.get('quality_notes',[]),list) else []):
        try:
            if not isinstance(note,dict) or note.get('model_id') not in mids: raise ValueError('Invalid quality model')
            if note.get('principle') not in ('actor_goal','essential','terse','black_box','abstraction','design'): raise ValueError('Invalid quality principle')
            if not all(isinstance(note.get(k),str) and note[k].strip() for k in ('summary','recommendation')): raise ValueError('Invalid quality explanation')
            ev=hydrate_evidence(note.get('evidence',[]),artifacts)
            if not any(e['artifact_id']==note['model_id'] for e in ev): raise ValueError('Quality suggestion needs model source')
            notes.append({**deepcopy(note),'evidence':ev,'status':'建模建议，待人工判断'})
        except (ValueError,TypeError) as exc: advisory_errors.append({'section':'quality','index':index,'reason':str(exc)})
    return {'results':results,'issues':issues,'quality_notes':notes,'errors':errors,'advisory_errors':advisory_errors,'scope_decisions':decisions,'scope_exclusions':exclusions}
