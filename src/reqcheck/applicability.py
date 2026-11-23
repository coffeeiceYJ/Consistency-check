"""Typed scope policy. It checks declared dimensions, not natural-language truth."""
from copy import deepcopy

DIMENSIONS={
    'function':'功能目标', 'actor_boundary':'角色与系统边界', 'authorization':'角色权限',
    'structure':'对象属性关联', 'data_constraint':'数量和数据约束',
    'behavior_order':'行为先后', 'branch_path':'条件分支及异常路径',
    'state_transition':'状态迁移', 'timing':'时限和时间事件',
}
CAPABILITIES={
    'usecase':{'function','actor_boundary','authorization'},
    'class':{'structure','data_constraint'},
    'activity':{'function','authorization','data_constraint','behavior_order','branch_path','timing'},
    'state':{'function','authorization','data_constraint','behavior_order','branch_path','state_transition','timing'},
    'sequence':{'function','actor_boundary','authorization','data_constraint','behavior_order','branch_path','timing'},
    'system_sequence':{'function','actor_boundary','authorization','data_constraint','behavior_order','branch_path','timing'},
}

def supports(model,dimension):
    if not isinstance(dimension,str) or dimension not in DIMENSIONS: return None
    if model.diagram_type not in CAPABILITIES: return None
    return dimension in CAPABILITIES[model.diagram_type]

def policy(models):
    return {'dimensions':DIMENSIONS,'models':[{'model_id':m.id,'allowed_dimensions':sorted(CAPABILITIES.get(m.diagram_type,set())),
        'unknown_type':m.diagram_type not in CAPABILITIES} for m in models]}

def all_outside(checks,models):
    return bool(checks) and all(all(supports(m,c['dimension']) is False for m in models) for c in checks)

def apply_policy(response, requirements, models):
    """Normalize scopes before publishing. Never turn unsupported checks into omissions."""
    result=deepcopy(response); decisions=[]; exclusions=[]; errors=[]
    if not isinstance(result,dict): raise ValueError('Invalid response')
    rows=result.get('assessments')
    if not isinstance(rows,list): return result,decisions,exclusions,[{'reason':'Missing assessments for applicability'}]
    reqs={r.id:r for r in requirements}; available={m.id:m for m in models}; checks_by_req={}
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('requirement_id'),str) or row['requirement_id'] not in reqs: continue
        rid=row['requirement_id']; checks=row.get('checks')
        if not isinstance(checks,list) or not checks:
            errors.append({'requirement_id':rid,'reason':'Missing typed checks'})
            row.update(coverage='部分覆盖',consistency='无法判断',checks=[])
            continue
        clean=[]; seen=set()
        for c in checks:
            if (not isinstance(c,dict) or not isinstance(c.get('id'),str) or not c['id'].startswith(rid+'.') or c['id'] in seen
                or not isinstance(c.get('dimension'),str) or c['dimension'] not in DIMENSIONS or not isinstance(c.get('description'),str) or not c['description'].strip()
                or c.get('applicability') not in ('applicable','out_of_scope','uncertain')
                or c.get('verdict') not in ('一致','存在冲突','无法判断','未核查')
                or not isinstance(c.get('reason'),str) or not c['reason'].strip()
                or not isinstance(c.get('missing_content',''),str)):
                errors.append({'requirement_id':rid,'reason':'Invalid checkpoint dimension or fields'}); continue
            seen.add(c['id']); item=deepcopy(c)
            supported=[m.id for m in models if supports(m,c['dimension']) is True]
            uncertain=any(supports(m,c['dimension']) is None for m in models)
            if not supported and not uncertain:
                item.update(applicability='out_of_scope',verdict='未核查',missing_content='',
                            reason='本轮提供的图类型均不承担该维度；未核查，不据此报告遗漏。',decision_source='program-capability-policy')
            elif not supported or item['applicability']=='uncertain':
                item.update(applicability='uncertain',verdict='无法判断',decision_source='uncertain')
            else: item['decision_source']='model-scope-assessment'
            item['supported_model_ids']=supported
            clean.append(item); decisions.append({'requirement_id':rid,**item,'provided_model_ids':list(available)})
        row['checks']=clean; checks_by_req[rid]={c['id']:c for c in clean}
        active=[c for c in clean if c['applicability']=='applicable']
        skipped=[c for c in clean if c['applicability']=='out_of_scope']
        uncertain=[c for c in clean if c['applicability']=='uncertain']
        row['coverage_scope']='适用检查点'; row['unchecked_dimensions']=[c['description'] for c in skipped]
        row['missing_dimensions']=[c['missing_content'] for c in active if c.get('missing_content')]
        if clean and len(skipped)==len(clean):
            row.update(coverage='不适用',consistency='无法判断',missing_dimensions=[],
                       summary='本次图职责之外，未核查：'+'；'.join(c['description'] for c in skipped))
        elif active:
            verdicts=[c['verdict'] for c in active]
            row['consistency']='存在冲突' if '存在冲突' in verdicts else ('一致' if all(v=='一致' for v in verdicts) and not uncertain else '无法判断')
            if row['missing_dimensions'] or row['consistency']=='无法判断':
                row['coverage']='部分覆盖'
            if skipped:
                row['summary']='适用检查点：'+'；'.join(c['description'] for c in active)+'。未核查（职责外）：'+'；'.join(c['description'] for c in skipped)
        else:
            row.update(coverage='部分覆盖',consistency='无法判断')
        if len(clean)!=len(checks): row.update(coverage='部分覆盖',consistency='无法判断')
    raw=result.get('issues',[]); accepted=[]
    if not isinstance(raw,list): return result,decisions,exclusions,errors
    for i,issue in enumerate(raw):
        if not isinstance(issue,dict): accepted.append(issue); continue
        rids=issue.get('requirement_ids',[]); refs=issue.get('check_ids'); dim=issue.get('dimension')
        if (not isinstance(rids,list) or not rids or not all(isinstance(r,str) and r in reqs for r in rids)
            or not isinstance(refs,list) or not refs or not all(isinstance(r,str) for r in refs)
            or not isinstance(dim,str) or dim not in DIMENSIONS):
            errors.append({'index':i,'reason':'Issue missing valid dimension/check_ids'}); continue
        linked=[c for rid in rids for cid,c in checks_by_req.get(rid,{}).items() if cid in refs]
        if len({c['id'] for c in linked})!=len(set(refs)):
            errors.append({'index':i,'reason':'Issue references unknown/inconsistent checkpoints'}); continue
        # A path fault may involve both order and state checkpoints. The primary
        # dimension must be present, but other valid references need not match it.
        primary=[c for c in linked if c['dimension']==dim]
        if not primary:
            # check_ids is the authoritative reference. Avoid maintaining two
            # conflicting copies of the same classification. Normalize a redundant
            # label only when every referenced check is applicable to this diagram.
            target=available.get(issue.get('model_id')) if isinstance(issue.get('model_id'),str) else None
            if not target or not all(c['applicability']=='applicable' and supports(target,c['dimension']) is True for c in linked):
                errors.append({'index':i,'reason':'Issue dimension disagrees with scope-unsupported checks'}); continue
            issue['declared_dimension']=dim
            dim=linked[0]['dimension']; issue['dimension']=dim
            issue['dimension_source']='referenced-checks'; primary=[c for c in linked if c['dimension']==dim]
        mid=issue.get('model_id'); target=available.get(mid) if isinstance(mid,str) else None
        if all(c['applicability']=='out_of_scope' for c in primary) or target and supports(target,dim) is False:
            exclusions.append({'title':issue.get('title',''),'requirement_ids':rids,'model_id':mid,'dimension':dim,
                               'reason':'所指图不承担此检查维度，未发布为业务错误或遗漏。'}); continue
        if any(c['applicability']=='uncertain' for c in linked):
            errors.append({'index':i,'reason':'Issue applicability uncertain; requires review'}); continue
        accepted.append(issue)
    result['issues']=accepted
    return result,decisions,exclusions,errors
