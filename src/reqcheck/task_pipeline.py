"""Prepared requirements -> classified links -> bounded tasks -> targeted follow-up."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import re
import time
import uuid
from .contracts import CheckRequest
from . import task_prompts as tp
from .prompts import artifact_data
from .applicability import DIMENSIONS, policy
from .issues import issue_summary
from .stage_runner import StageRunner
from .task_data import prepare_checkpoints, parse_task_result, plan_tasks, hydrate_evidence
from .relation_classifier import LiSSARelationClassifier
from .prompt_profiles import PROFILES, CROSS_VIEW
from .model_context import activity_context


def candidate_pairs(request, retriever):
    """Two retrieval directions; neither is an LLM full-requirement audit."""
    options={**request.options,'top_k':request.options.get('candidate_top_k',20)}
    forward=retriever.retrieve(CheckRequest(request.requirements,request.models,options=options))
    reverse=retriever.retrieve(CheckRequest(request.models,request.requirements,options={**options,'top_k':request.options.get('reverse_top_k',20)}))
    reqs={r.id for r in request.requirements}; models={m.id for m in request.models}; pairs={}
    for rows,inverse in ((forward,False),(reverse,True)):
        for row in rows:
            rid,mid=(row['model_id'],row['requirement_id']) if inverse else (row['requirement_id'],row['model_id'])
            if rid not in reqs or mid not in models: raise ValueError('Invalid retrieved artifact IDs')
            item=pairs.setdefault((rid,mid),{'requirement_id':rid,'model_id':mid,'directions':[],'score':row.get('score')})
            item['directions'].append('model_to_requirement' if inverse else 'requirement_to_model')
    return list(pairs.values())


def finding_key(issue):
    loc=issue.get('location') or {}
    value=[issue['model_id'],issue['type'],sorted(issue['check_ids']),loc.get('start_line'),loc.get('end_line')]
    return 'FINDING-'+hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:12]


def resolve_findings(response, findings, requirements, models):
    """Atomic resolution: rejected/circular merge decisions cannot delete originals."""
    known={i['id']:i for i in findings}; decisions=response.get('decisions') if isinstance(response,dict) else None
    if not isinstance(decisions,list): raise ValueError('Missing resolution decisions')
    valid={};errors=[]
    for d in decisions:
        try:
            fid=d.get('finding_id') if isinstance(d,dict) else None
            if not isinstance(fid,str) or fid not in known or fid in valid: raise ValueError('Invalid/duplicate finding ID')
            action=d.get('action')
            if action not in ('keep','false_positive','merge','uncertain') or not isinstance(d.get('reason'),str) or not d['reason'].strip():
                raise ValueError('Invalid resolution action/reason')
            ev=hydrate_evidence(d.get('evidence',[]),requirements+models)
            old=known[fid]; cited={e['artifact_id'] for e in ev}
            if not set(old['requirement_ids']).issubset(cited) or old['model_id'] not in cited: raise ValueError('Resolution lacks requirement/model evidence')
            valid[fid]={**d,'evidence':ev}
        except (ValueError,TypeError) as exc: errors.append({'reason':str(exc)})
    output=[];actions=[]
    for fid,original in known.items():
        item=deepcopy(original);d=valid.get(fid)
        if not d:
            item['status']='待复核';output.append(item);errors.append({'finding_id':fid,'reason':'Missing valid resolution'});continue
        if d['action']=='merge':
            target=known.get(d.get('target_id')); target_decision=valid.get(d.get('target_id'))
            if (not target or target['id']==fid or target['model_id']!=item['model_id'] or target['type']!=item['type']
                or not target_decision or target_decision['action']!='keep'):
                item['status']='待复核';output.append(item);errors.append({'finding_id':fid,'reason':'Merge has no independently retained target'});continue
            # Target must be backed by all requirement/checkpoint sources before merging.
            cited={e['artifact_id'] for e in target['evidence']+target_decision['evidence']}
            if not set(item['requirement_ids']).issubset(cited):
                item['status']='待复核';output.append(item);errors.append({'finding_id':fid,'reason':'Merge target loses requirement basis'});continue
        if d['action'] in ('merge','false_positive'): actions.append({'finding_id':fid,**d});continue
        item['status']='模型复核完成' if d['action']=='keep' else '待复核'
        item['resolution']=d;output.append(item)
    for action in actions:
        if action['action']=='merge':
            target=next(i for i in output if i['id']==action['target_id'])
            original=known[action['finding_id']]
            target['merged_ids']=sorted(set(target.get('merged_ids',[])+[original['id']]))
            for field in ('check_ids','requirement_ids'):
                target[field]=sorted(set(target[field]+original[field]))
            if original.get('expected') and original['expected']!=target.get('expected'):
                target['expected']=target.get('expected','')+'；'+original['expected']
            for e in original['evidence']:
                if e not in target['evidence']: target['evidence'].append(e)
    return {'issues':output,'actions':actions,'errors':errors}


def check_tasks(request,retriever,client,progress=None,cancelled=None,relation_classifier=None,cache_dir=None):
    started=time.monotonic(); diagnostics=[]; passes=[]; findings={}; notes=[]; point_results={}; tasks=[]; points=[]
    classifications=[]; candidates=[]; links=[]; actions=[]; scope_decisions=[];scope_exclusions=[]
    progress=progress or (lambda e:None);cancelled=cancelled or (lambda:False)
    runner=StageRunner(client,request.options,progress,cancelled,cache_dir)
    retries=request.options.get('validation_retries',1)
    if type(retries) is not int or retries not in (0,1):raise ValueError('validation_retries must be 0 or 1')
    reqs={r.id:r for r in request.requirements}; models={m.id:m for m in request.models}
    structures={m.id:activity_context(m) for m in request.models if m.diagram_type=='activity'}
    if not reqs or not models or len(reqs)+len(models)!=len(request.requirements)+len(request.models): raise ValueError('Empty/duplicate inputs')
    def failure(code,message,**extra): diagnostics.append({'code':code,'message':message,**extra})
    for model in request.models:
        if model.diagram_type=='unknown': failure('unknown_diagram_type','图类型未识别，原始文本检查不能确认完整性。',model_id=model.id,stage='input')
        if re.search(r'^\s*!(?:include|define|function|procedure|if)\b',model.text,re.M):
            failure('external_or_preprocessed_source','存在未展开的预处理或外部引用；不能确认完整输入。',model_id=model.id,stage='input')
    if request.rules:
        failure('explicit_rules_not_executed','任务式流程不执行旧版活动图显式规则；请单独使用rules模式验证。',stage='rules')
    def invoke(stage,payload,validator,system=tp.SYSTEM):
        try: return runner.run(stage,system,payload,validator)
        except Exception as exc:
            failure('stage_failed',stage+'未完成；保留已有效返回，不据此判业务遗漏。',stage=stage,call=len(runner.records),failure_kind=type(exc).__name__,requirement_ids=payload.get('requirement_ids',[]))
            return None
    size=request.options.get('preparation_batch_size',12)
    if type(size) is not int or size<1: raise ValueError('Invalid preparation batch size')
    for i in range(0,len(request.requirements),size):
        group=request.requirements[i:i+size]
        data={'stage':'prepare','task':tp.PREPARE,'dimensions':DIMENSIONS,'requirement_ids':[r.id for r in group],
              'requirements':[artifact_data(r) for r in group]}
        prepared=invoke('prepare',data,lambda v,g=group:prepare_checkpoints(v,g))
        if prepared: points.extend(prepared)
    prepared_ids={c['requirement_id'] for c in points}
    active=[r for r in request.requirements if r.id in prepared_ids]
    requested_routing=request.options.get('routing_mode','auto')
    if requested_routing not in ('auto','classified','all'): raise ValueError('Invalid routing_mode')
    compact=(len(active)<=request.options.get('task_requirements',6) and
             sum(len(a.text) for a in active+request.models)<=request.options.get('task_source_chars',30000))
    routing=('all' if compact else 'classified') if requested_routing=='auto' else requested_routing
    routing_reason=('完整输入可放进一个任务，跳过关联分类成本。' if compact else '完整输入超出单任务预算，先筛选相关范围。') if requested_routing=='auto' else '用户显式指定路由，用于运行或受控对照。'
    if active:
        if routing=='all':
            links=[{'requirement_id':r.id,'model_id':m.id,'score':None,'decision':'unclassified',
                    'reason':'直接提供完整材料，未进行关联分类','method':'direct-material-no-filter',
                    'status':'检查输入范围，不是追踪关系'} for r in active for m in request.models]
        else:
            try: candidates=candidate_pairs(CheckRequest(active,request.models,options=request.options),retriever)
            except Exception:
                failure('retrieval_failed','双向候选检索失败；不将检索失败解释为无关联。',stage='retrieval')
            # A requirement with no lexical candidates gets a disclosed fallback once.
            missing=[r for r in active if not any(p['requirement_id']==r.id for p in candidates)]
            for r in missing:
                candidates.extend({'requirement_id':r.id,'model_id':m.id,'score':None,'directions':['no-candidate-fallback']} for m in request.models)
            pairs=[]
            for p in candidates:
                rid=p['requirement_id'];mid=p['model_id']
                target=artifact_data(models[mid]);target.pop('theory_profile',None)
                pairs.append({'source':{**artifact_data(reqs[rid]),'checkpoints':[c for c in points if c['requirement_id']==rid]},'target':target})
            classifier=relation_classifier or LiSSARelationClassifier(retriever)
            # Classification cannot spend the calls needed for main checks and one repair each.
            reserve=min(runner.max_calls-runner.calls,2*len(active)+1)
            runner.stage_call_ceiling['relation']=max(runner.calls,runner.max_calls-reserve)
            try:
                classifications=classifier.classify(pairs,runner)
                expected={(p['source']['id'],p['target']['id']) for p in pairs}
                if len(classifications)!=len(expected) or {(p['requirement_id'],p['model_id']) for p in classifications}!=expected:
                    raise ValueError('Relation backend omitted candidates')
                if any(p.get('decision') not in ('yes','no','uncertain') for p in classifications): raise ValueError('Invalid relation backend labels')
                for f in getattr(classifier,'failures',[]): failure('relation_failed','关联分类失败的候选按不确定保留。',stage='relation',requirement_ids=[f['requirement_id']],model_id=f['model_id'])
            except Exception:
                failure('relation_backend_failed','关联模块未完成，候选按不确定保留，不冒称已完成LiSSA分类。',stage='relation')
                classifications=[{**p,'decision':'uncertain','reason':'分类未完成，保守保留','method':'failed-classifier-fallback'} for p in candidates]
        if routing!='all':
            links=[{**p,'status':'相关候选，非满足结论' if p['decision']=='yes' else '关联不确定，保留核查'} for p in classifications if p['decision']!='no']

    def consume(parsed,task,stage):
        passes.append({'stage':stage,'call':len(runner.records),'task_id':task['id'],
                       'requirement_ids':task['requirement_ids'],'model_ids':task['model_ids'],**parsed})
        for row in parsed['results']: point_results[row['check_id']]=deepcopy(row)
        for issue in parsed['issues']:
            key=finding_key(issue);issue['id']=key
            if key in findings:
                existing=findings[key]
                for e in issue['evidence']:
                    if e not in existing['evidence']:existing['evidence'].append(e)
                existing.setdefault('observed_calls',[]).append(len(runner.records))
            else:
                issue['observed_calls']=[len(runner.records)];findings[key]=issue
        for note in parsed['quality_notes']:
            if note not in notes: notes.append(note)
        scope_decisions.extend(parsed['scope_decisions']);scope_exclusions.extend(parsed['scope_exclusions'])

    def run_task(task,stage='check',repair=None,selected_points=None):
        rs=[reqs[r] for r in task['requirement_ids']];ms=[models[m] for m in task['model_ids']]
        ps=selected_points or [c for c in points if c['requirement_id'] in task['requirement_ids']]
        if task['oversized']:
            failure('task_too_large','相关完整图超出任务预算，未截断或声称已检查。',stage=stage,requirement_ids=task['requirement_ids'],model_ids=task['model_ids']);return
        payload={'stage':stage,'task':tp.CHECK,'task_id':task['id'],'requirement_ids':task['requirement_ids'],'model_ids':task['model_ids'],
                 'checkpoints':ps,'requirements':[artifact_data(r) for r in rs],'models':[artifact_data(m) for m in ms],
                 'applicability_policy':policy(ms),'scope_note':'仅核查本轮已提供模型；不宣称全项目无遗漏。'}
        payload['source_structure']=[structures[m.id] for m in ms if m.id in structures]
        if repair: payload['repair']={'errors':repair['errors'],'instruction':'只纠正给定检查点的无效或缺失结果；不得为通过校验改动业务事实。'}
        parsed=invoke(stage,payload,lambda v:parse_task_result(v,ps,rs,ms))
        initial_call=len(runner.records)
        if parsed: consume(parsed,task,stage)
        if parsed and parsed['errors'] and not repair and request.options.get('validation_retries',1):
            failed={e.get('check_id') for e in parsed['errors'] if e.get('check_id')}
            targets=[p for p in ps if p['id'] in failed] if failed else ps
            corrected=run_task(task,'repair',repair=parsed,selected_points=targets)
            if corrected and not corrected['errors']:
                parsed['resolved_by_call']=len(runner.records)
                runner.records[initial_call-1]['resolved_by_call']=len(runner.records)
                return corrected
        if parsed and parsed['errors']:
            failure('invalid_task_parts','部分返回未通过校验；有效发现保留，失败范围未当作成功。',stage=stage,call=len(runner.records),requirement_ids=task['requirement_ids'],errors=parsed['errors'])
        return parsed

    tasks=plan_tasks(active,request.models,links,request.options)
    for task in tasks: run_task(task)
    # One bounded expansion only for absent links or explicit context requests.
    expanded=[]
    for r in active:
        selected={l['model_id'] for l in links if l['requirement_id']==r.id}
        needs=not selected or any(v.get('needs_context') for k,v in point_results.items() if k.startswith(r.id+'.'))
        if needs and selected!=set(models) and request.options.get('expand_missing_context',True):
            extra=set(models)-selected
            if len(extra)>request.options.get('max_expansion_models',8):
                failure('expansion_budget','需扩大范围但超过补查图数量预算，交人工复核。',stage='expand',requirement_ids=[r.id]);continue
            added=[{'requirement_id':r.id,'model_id':m,'score':None,'decision':'uncertain','reason':'缺少候选或请求上下文后的有界补查','method':'context-expansion','status':'补查候选'} for m in sorted(extra)]
            links.extend(added);expanded.extend(added)
            t=plan_tasks([r],request.models,[l for l in links if l['requirement_id']==r.id],request.options)[0]
            t['id']='EXPAND-'+r.id;tasks.append(t);run_task(t,'expand')

    # Signals, not a mandatory repeated review of every task.
    review_policy=request.options.get('review_policy','signals')
    if review_policy not in ('signals','all_findings','none'): raise ValueError('Invalid review policy')
    disputed=set()
    values=list(findings.values())
    for i,a in enumerate(values):
        for b in values[i+1:]:
            if a['model_id']==b['model_id'] and (set(a['check_ids'])&set(b['check_ids']) or
                a.get('location') and b.get('location') and max(a['location']['start_line'],b['location']['start_line'])<=min(a['location']['end_line'],b['location']['end_line'])):
                disputed.update((a['id'],b['id']))
    if review_policy=='all_findings': disputed=set(findings)
    for review_mid in sorted({i['model_id'] for i in values if i['id'] in disputed},key=str) if review_policy!='none' else []:
        review_items=[i for i in findings.values() if i['id'] in disputed and i['model_id']==review_mid]
        rids=sorted({r for i in review_items for r in i['requirement_ids']});mids=sorted({i['model_id'] for i in review_items if i['model_id']})
        rs=[reqs[r] for r in rids];ms=[models[m] for m in mids]
        payload={'stage':'resolve','task':tp.RESOLVE,'requirement_ids':rids,'model_ids':mids,
                 'findings':review_items,'checkpoints':[c for c in points if c['requirement_id'] in rids],
                 'requirements':[artifact_data(r) for r in rs],'models':[artifact_data(m) for m in ms]}
        resolved=invoke('resolve',payload,lambda v:resolve_findings(v,review_items,rs,ms))
        if resolved:
            for item in review_items:findings.pop(item['id'],None)
            for i in resolved['issues']:findings[i['id']]=i
            actions.extend(resolved['actions'])
            if resolved['errors']: failure('resolution_incomplete','定向复核未完整处理，保留未解决发现。',stage='resolve',errors=resolved['errors'])

    # A rejected/invalid finding cannot remain a published conflict through an old row.
    # Keep the original prediction in passes, and do not infer consistency from rejection.
    for cid,row in point_results.items():
        if row['consistency']=='存在冲突' and not any(cid in i['check_ids'] and i['type'] in ('表达错误','跨图冲突') for i in findings.values()):
            row.update(initial_consistency='存在冲突',consistency='无法判断',
                       resolution_note='对应冲突发现未保留或未通过校验，不能据此发布冲突或认定一致。')
    assessments=[];scopes=[]
    for r in request.requirements:
        ps=[c for c in points if c['requirement_id']==r.id];rows=[point_results[p['id']] for p in ps if p['id'] in point_results]
        related=[i for i in findings.values() if r.id in i['requirement_ids']]
        coverage=('不适用' if rows and len(rows)==len(ps) and all(a['coverage']=='不适用' for a in rows) else
                  '完全覆盖' if rows and len(rows)==len(ps) and all(a['coverage'] in ('完全覆盖','不适用') for a in rows) else
                  '未发现覆盖' if rows and len(rows)==len(ps) and all(a['coverage'] in ('未发现覆盖','不适用') for a in rows) else
                  '部分覆盖' if rows else '未发现覆盖')
        conflicts=[i for i in related if i['type'] in ('表达错误','跨图冲突')]
        consistency='存在冲突' if conflicts else ('一致' if not related and rows and len(rows)==len(ps) and all(a['consistency']=='一致' or a['coverage']=='不适用' for a in rows) and coverage!='不适用' else '无法判断')
        # A discarded finding does not prove the whole checkpoint is now correct.
        if not related and any(a['consistency']=='存在冲突' for a in rows):consistency='无法判断'
        source_models=sorted({m for p in passes if r.id in p['requirement_ids'] for m in p['model_ids']})
        evidence_models=sorted({e['artifact_id'] for a in rows for e in a['evidence'] if e['artifact_id'] in models}|
                               {i['model_id'] for i in related if i.get('model_id')})
        evidence=[]
        for a in rows:
            for e in a['evidence']:
                if e not in evidence:evidence.append(e)
        unfinished=[]
        if not ps:unfinished.append('prepare')
        if len(rows)!=len(ps) or not rows:unfinished.append('check')
        if any(i['status']=='待复核' for i in related):unfinished.append('resolve')
        assessments.append({'requirement_id':r.id,'coverage':coverage,'consistency':consistency,
            'summary':'；'.join(a['summary'] for a in rows) or '已检查范围内没有足够结果，不认定确定遗漏。',
            'model_ids':evidence_models,'evidence':evidence,'requirement_source':r.source(),
            'missing_dimensions':[p['text'] for p in ps if not any(a['check_id']==p['id'] for a in rows)],
            'review_notes':['结论仅针对列出的检查范围；需求拆解和语义判断仍需人工复核。'],
            'checks':ps,'unchecked_dimensions':[points_by_id['text'] for points_by_id in ps if any(a['check_id']==points_by_id['id'] and a['coverage']=='不适用' for a in rows)],
            'coverage_scope':'本次相关模型中的适用检查点'})
        scopes.append({'requirement_id':r.id,'model_ids':source_models,'unfinished_stages':unfinished,
                       'unprovided_model_ids':sorted(set(models)-set(source_models)),'not_required_stages':['reverse','holistic']})
    cancelled_run=bool(cancelled())
    status='cancelled' if cancelled_run else 'partial' if diagnostics or any(s['unfinished_stages'] for s in scopes) else 'completed'
    quality=[]
    for note in notes:
        key=(note['model_id'],note['principle'],note['summary'])
        if not any((n['model_id'],n['principle'],n['summary'])==key for n in quality):quality.append(note)
    evidence_links=[]
    for c in points:
        row=point_results.get(c['id'])
        if not row:continue
        for mid in sorted({e['artifact_id'] for e in row['evidence'] if e['artifact_id'] in models}):
            evidence_links.append({'requirement_id':c['requirement_id'],'check_id':c['id'],'model_id':mid,
                'status':'语义检查引用的模型证据，非满足证明',
                'evidence':[e for e in row['evidence'] if e['artifact_id']==mid]})
    report={'schema_version':'2.0','output_design_version':'3.0','run_id':request.run_id or uuid.uuid4().hex,'run_status':status,
        'issues':list(findings.values()),'findings':list(findings.values()),'quality_notes':quality,'assessments':assessments,
        'issue_summary':issue_summary(list(findings.values())),'checkpoints':points,'checkpoint_results':list(point_results.values()),
        'candidate_links':candidates,'classified_links':classifications,'initial_links':candidates,'links':links,'recovered_links':expanded,
        'evidence_links':evidence_links,
        'tasks':tasks,'check_scope':scopes,'scope_decisions':scope_decisions,'scope_exclusions':scope_exclusions,
        'model_structure':list(structures.values()),
        'passes':passes,'diagnostics':diagnostics,'execution_failures':diagnostics,'resolved_validation_failures':[],
        'advisory_errors':[{'call':p['call'],**e} for p in passes for e in p.get('advisory_errors',[])],
        'dismissed_issues':[],'resolution_actions':actions,'unlocalized_observations':[],'rule_checks':[],
        'call_records':runner.records,'debug_artifacts':bool(request.options.get('debug_artifacts',False)),
        'options':{k:v for k,v in request.options.items() if k in ('check_strategy','routing_mode','max_calls','max_context_chars','task_requirements','task_source_chars','candidate_top_k','reverse_top_k','validation_retries','expand_missing_context','max_expansion_models','review_policy','max_output_tokens','model_timeout_seconds','model_thinking')},
        'export_csv':bool(request.options.get('export_csv',False)),
        'inputs':[{'id':a.id,'text':a.text,'source':a.source(),'diagram_type':a.diagram_type,'sha256':hashlib.sha256(a.text.encode()).hexdigest()} for a in request.requirements+request.models],
        'provenance':{'version':'1.0.0','prompt_version':tp.VERSION,'check_strategy':'task_based','requested_routing':requested_routing,'routing_mode':routing,'routing_reason':routing_reason,
            'prompt_sha256':hashlib.sha256((tp.SYSTEM+tp.PREPARE+tp.RELATION+tp.CHECK+tp.RESOLVE+json.dumps(PROFILES,sort_keys=True,ensure_ascii=False)+str(CROSS_VIEW)).encode()).hexdigest(),
            'mode':getattr(client,'provenance',{}).get('mode','injected-client-unverified'),'model':getattr(client,'provenance',{}),
            'retriever':getattr(retriever,'provenance',{}),'retrieval_executed':bool(active and routing=='classified'),
            'relation_backend':classifications[0].get('method') if classifications else 'not-executed',
            'calls':runner.calls,'cache_hits':sum(bool(c['cache_hit']) for c in runner.records),
            'usage':{k:sum(c.get('usage_delta',{}).get(k,0) for c in runner.records) for k in ('prompt_tokens','completion_tokens','total_tokens')},
            'elapsed_seconds':round(time.monotonic()-started,3),
            'limits':['当前完整图为主要检查单元；未完整解析六类UML结构。','关联分类和检查点拆分仍依赖模型；不是形式化证明。','未提供的模型不能据此宣称已排除全局遗漏。']}}
    progress({'phase':'complete','run_status':status})
    return report
