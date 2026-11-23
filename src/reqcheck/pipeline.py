"""Core orchestration; runtime-independent and dependency-injected."""
import hashlib
import json
import time
import uuid
from copy import deepcopy
from .contracts import COVERAGE, CONSISTENCY
from .activity import check_before, UnsupportedActivity
from . import prompts
from .issues import validate_issues, reconcile_issues, issue_summary
from .model_client import ModelError
from .planning import batches, review_batches, compact_history

def validate_response(response, requirements, models):
    if not isinstance(response,dict) or not isinstance(response.get('assessments'),list): raise ValueError('Missing assessments array')
    expected={a.id for a in requirements}; artifacts={a.id:a for a in requirements+models}
    result={}
    for row in response['assessments']:
        if not isinstance(row,dict): raise ValueError('Invalid assessment')
        rid=row.get('requirement_id')
        if not isinstance(rid,str): raise ValueError('Invalid requirement_id')
        if rid not in expected or rid in result: raise ValueError('Unknown or duplicate requirement_id')
        if row.get('coverage') not in COVERAGE or row.get('consistency') not in CONSISTENCY: raise ValueError('Invalid verdict')
        if not isinstance(row.get('summary'),str) or not row['summary'].strip(): raise ValueError('Missing summary')
        mids=row.get('model_ids')
        valid_models={a.id for a in models}
        if not isinstance(mids,list) or not all(isinstance(m,str) and m in valid_models for m in mids): raise ValueError('Invalid model_ids')
        for key in ('missing_dimensions','review_notes'):
            if not isinstance(row.get(key),list) or not all(isinstance(v,str) for v in row[key]): raise ValueError(f'Invalid {key}')
        if not isinstance(row.get('evidence'),list): raise ValueError('Missing evidence array')
        valid=[]; errors=[]
        for e in row['evidence']:
            if not isinstance(e,dict): errors.append('证据不是对象'); continue
            aid=e.get('artifact_id'); a=artifacts.get(aid) if isinstance(aid,str) else None
            start,end=e.get('start_line'),e.get('end_line')
            if a is None or (aid in expected and aid!=rid) or (aid in valid_models and aid not in mids):
                errors.append('证据引用未知或无关条目'); continue
            if type(start)!=int or type(end)!=int or not a.start_line<=start<=end<=a.end_line:
                errors.append('证据行号越界'); continue
            quote='\n'.join(a.text.splitlines()[start-a.start_line:end-a.start_line+1])
            if not isinstance(e.get('quote'),str) or e['quote'].replace('\r\n','\n')!=quote or not quote.strip():
                errors.append('证据原文与指定行不匹配'); continue
            valid.append({**e,'source':{'file':a.file,'start_line':start,'end_line':end},'verified':True})
        if not any(e['artifact_id']==rid for e in valid): errors.append('缺少有效需求证据')
        if (row['consistency']!='无法判断' or row['coverage']=='完全覆盖') and not any(e['artifact_id'] in valid_models for e in valid):
            errors.append('缺少有效模型证据')
        if row['coverage']=='完全覆盖' and (row['missing_dimensions'] or row['consistency']=='无法判断'):
            errors.append('完全覆盖与缺失内容或无法判断相矛盾')
        clean={k:row[k] for k in ('requirement_id','coverage','consistency','summary','model_ids','missing_dimensions','review_notes')}
        for key in ('checks','coverage_scope','unchecked_dimensions'):
            if key in row: clean[key]=deepcopy(row[key])
        clean['evidence']=valid
        clean['validation_errors']=errors
        if errors:
            clean['consistency']='无法判断'
            clean['coverage']='部分覆盖' if any(e['artifact_id'] in valid_models for e in valid) else '未发现覆盖'
            clean['review_notes']=clean['review_notes']+errors
        result[rid]=clean
    if set(result)!=expected: raise ValueError('Model did not assess every requested requirement')
    return list(result.values())


def validate_response_parts(response, requirements, models):
    """One broken appendix row must not erase independently valid rows."""
    if not isinstance(response,dict) or not isinstance(response.get('assessments'),list):
        raise ValueError('Missing assessments array')
    valid, rejected = [], []
    expected={r.id for r in requirements}
    raw=response['assessments']
    if any(not isinstance(row,dict) or not isinstance(row.get('requirement_id'),str)
           or row['requirement_id'] not in expected for row in raw):
        rejected.append({'requirement_id':None,'reason':'回答包含未知或无效需求编号。'})
    for req in requirements:
        rows=[row for row in raw if isinstance(row,dict) and row.get('requirement_id')==req.id]
        if len(rows)!=1:
            rejected.append({'requirement_id':req.id,'reason':'本需求的覆盖行缺失或重复。'})
            continue
        try:
            valid.extend(validate_response({'assessments':rows},[req],models))
        except (ValueError,TypeError,KeyError):
            rejected.append({'requirement_id':req.id,'reason':'本需求的覆盖行格式校验失败。'})
    return valid,rejected

def unknown(requirement,notes,models=()):
    return {'requirement_id':requirement.id,'coverage':'未发现覆盖','consistency':'无法判断',
            'summary':'尚无足够证据形成需求满足结论。','model_ids':list(models),'evidence':[],
            'missing_dimensions':[],'review_notes':list(notes)}

def check(request,retriever,client=None,progress=None,cancelled=None):
    """Return a serializable report. Credentials and filesystem writes are outside this function."""
    strategy=request.options.get('check_strategy','candidate_first')
    if strategy not in ('candidate_first','full_context'): raise ValueError('Invalid check_strategy')
    if client is not None and strategy=='candidate_first':
        from .scoped_pipeline import check_scoped
        return check_scoped(request,retriever,client,progress,cancelled)
    start=time.monotonic(); diagnostics=[]; passes=[]; rule_checks=[]; calls=0; stopped=False; call_records=[]
    progress=progress or (lambda event:None); cancelled=cancelled or (lambda:False)
    def event(phase,**data): progress({'phase':phase,**data})
    def diagnostic(code,message,**data): diagnostics.append({'code':code,'message':message,**data})
    def is_cancelled():
        nonlocal stopped
        stopped=stopped or bool(cancelled())
        return stopped
    artifacts=request.requirements+request.models
    if len({a.id for a in artifacts})!=len(artifacts): raise ValueError('Duplicate artifact IDs')
    if not request.requirements or not request.models: raise ValueError('Empty requirements/models')
    event('retrieval')
    links=[]
    if not is_cancelled():
        try:
            links=retriever.retrieve(request)
            if not isinstance(links,list): raise ValueError('Invalid retriever output')
            req_ids={r.id for r in request.requirements}; model_ids={m.id for m in request.models}
            for link in links:
                if link.get('requirement_id') not in req_ids or link.get('model_id') not in model_ids:
                    raise ValueError('Retriever returned unknown artifact')
        except Exception as exc:
            links=[]
            diagnostic('retrieval_failed',f'检索失败（{type(exc).__name__}），扩大到所有模型；详见独立检索验证。')
    # Full diagrams are used across global/review passes even when a link was not retrieved.
    for req in request.requirements:
        if not any(l['requirement_id']==req.id for l in links):
            diagnostic('no_candidates','未找到候选，不判定遗漏；检查范围扩大到全部模型。',requirement_id=req.id)
    reqs={a.id:a for a in request.requirements}; models={a.id:a for a in request.models}
    for model in request.models:
        if model.diagram_type=='unknown': diagnostic('unknown_diagram_type','图类型无法识别，仅能检查原始文本。',model_id=model.id)
        if any(line.strip().startswith(('!include','!define','!function','!procedure','!if')) for line in model.text.splitlines()):
            diagnostic('external_or_preprocessed_source','包含预处理/外部引用；未展开内容不可据此判定完整。',model_id=model.id)
    for rule in request.rules:
        if is_cancelled(): break
        event('rule_check')
        if not isinstance(rule,dict) or rule.get('requirement_id') not in reqs or rule.get('model_id') not in models:
            diagnostic('invalid_rule','规则引用的需求或模型不存在。'); continue
        try:
            out=check_before(rule,reqs[rule['requirement_id']],models[rule['model_id']])
            rule_checks.append({'requirement_id':rule['requirement_id'],'model_id':rule['model_id'],
                    'rule':rule,**out,'scope':'用户显式规则/支持的活动图语法；不自动解释自然语言需求'})
        except (ValueError,RecursionError) as exc:
            diagnostic('unsupported_rule_or_syntax',str(exc),requirement_id=rule['requirement_id'],model_id=rule['model_id'])
    max_calls=request.options.get('max_calls',30); max_chars=request.options.get('max_context_chars',120000)
    if type(max_calls)!=int or max_calls<1 or type(max_chars)!=int or max_chars<1000: raise ValueError('Invalid call/context budget')
    batch_size=request.options.get('requirement_batch_size',4)
    initial_batches=batches(request.requirements,batch_size)
    event('check_plan',requirement_batch_size=batch_size,baseline_calls=len(request.requirements)+3*len(initial_batches),
          max_calls=max_calls,note='复核分组会保留关联需求，实际调用次数可能调整。')
    def run(stage,requirements,previous=None):
        nonlocal calls
        if is_cancelled(): return None
        if calls>=max_calls:
            diagnostic('call_budget_exceeded','模型调用预算耗尽，本轮未执行。',stage=stage,requirement_ids=[r.id for r in requirements]); return None
        selected={l['model_id'] for l in links if l['requirement_id'] in {r.id for r in requirements}}
        ordered=sorted(request.models,key=lambda a:a.id not in selected) if stage=='requirement' else request.models
        data=prompts.payload(stage,requirements,ordered,previous,request.requirements)
        data['candidate_model_ids']=sorted(selected)
        data['retrieval_note']='候选优先阅读；补充图仍完整提供，避免检索漏掉联合证据。'
        size=len(json.dumps(data,ensure_ascii=False))
        if size+len(prompts.SYSTEM)>max_chars:
            diagnostic('context_budget_exceeded','包含系统提示词的完整上下文超限，本轮未执行；未静默截断。',stage=stage,
                       requirement_ids=[r.id for r in requirements],input_chars=size+len(prompts.SYSTEM),max_context_chars=max_chars); return None
        calls+=1; event('model_check',stage=stage,call=calls,requirement_ids=[r.id for r in requirements])
        call_start=time.monotonic()
        usage_before=dict(getattr(client,'usage',{}))
        record={'call':calls,'stage':stage,'requirement_ids':[r.id for r in requirements],
                'input_chars':size+len(prompts.SYSTEM),'payload_chars':size,'status':'started'}
        call_records.append(record)
        try:
            response=client.complete(prompts.SYSTEM,data)
            if not isinstance(response,dict) or 'issues' not in response:
                diagnostic('issue_contract_missing','回答缺少新的逐问题 issues 数组；覆盖结论仍保留，但问题输出未完成。',
                           stage=stage,call=calls,requirement_ids=[r.id for r in requirements])
            issue_rows,rejected=validate_issues(response.get('issues',[]) if isinstance(response,dict) else [],request.requirements,request.models)
            target_ids={r.id for r in requirements}
            unrelated=[i for i in issue_rows if not target_ids.intersection(i['requirement_ids'])]
            issue_rows=[i for i in issue_rows if target_ids.intersection(i['requirement_ids'])]
            if unrelated: rejected.append({'index':None,'reason':'问题完全不涉及本批目标需求。'})
            for issue in issue_rows: issue['observed_in']=[{'stage':stage,'call':calls}]
            for rejection in rejected:
                diagnostic('invalid_issue','问题定位或证据校验失败，未作为有效问题发布。',stage=stage,call=calls,
                           requirement_ids=[r.id for r in requirements],**rejection)
            # Preserve independently valid issues even if the coverage appendix is malformed.
            saved={'stage':stage,'call':calls,'issues':issue_rows,'assessments':[],
                   'dismissed_issues':response.get('dismissed_issues',[]) if isinstance(response,dict) else []}
            if not isinstance(saved['dismissed_issues'],list):
                saved['dismissed_issues']=[]
                diagnostic('invalid_issue','排除问题结果不是数组。',stage=stage,call=calls)
            passes.append(saved)
            rows,row_errors=validate_response_parts(response,requirements,request.models)
            saved['assessments']=rows
            for error in row_errors:
                scope=[error['requirement_id']] if error['requirement_id'] else [r.id for r in requirements]
                diagnostic('assessment_row_invalid',error['reason'],stage=stage,call=calls,requirement_ids=scope)
            if not rows: raise ValueError('No valid assessment rows')
            for row in rows:
                if row.get('validation_errors'): diagnostic('invalid_evidence','AI 证据未通过原文校验，结论已降为无法判断。',stage=stage,requirement_id=row['requirement_id'])
                if row['consistency']=='存在冲突' and not any(row['requirement_id'] in i['requirement_ids'] and i['type'] in ('表达错误','跨图冲突') for i in issue_rows):
                    diagnostic('missing_issue_details','发现冲突但未返回可校验的逐问题定位，保留为未定位观察。',
                               stage=stage,call=calls,requirement_id=row['requirement_id'])
                if row['missing_dimensions'] and not any(row['requirement_id'] in i['requirement_ids'] and i['type'] in ('疑似遗漏','信息不足') for i in issue_rows):
                    diagnostic('missing_issue_details','指出缺失维度但未返回对应问题，保留为未定位观察，不静默漏掉。',
                               stage=stage,call=calls,requirement_id=row['requirement_id'])
            event('model_result',stage=stage,call=calls,issues=len(issue_rows),elapsed_seconds=round(time.monotonic()-call_start,3))
            record['status']='partial' if row_errors or rejected or any(d.get('call')==calls for d in diagnostics) or any(r.get('validation_errors') for r in rows) else 'completed'
            return rows
        except Exception as exc:
            # Exception messages may contain provider headers or secrets: do not serialize them.
            safe=exc.diagnostic if isinstance(exc,ModelError) else {'failure_kind':'response_schema' if isinstance(exc,ValueError) else 'unexpected_error'}
            diagnostic('model_call_failed',f'模型调用或结构校验失败（{type(exc).__name__}）。',stage=stage,call=calls,
                       requirement_ids=[r.id for r in requirements],elapsed_seconds=round(time.monotonic()-call_start,3),**safe)
            event('model_failed',stage=stage,call=calls,failure_kind=safe['failure_kind'])
            record.update(status='failed',**safe)
            return None
        finally:
            record['elapsed_seconds']=round(time.monotonic()-call_start,3)
            usage_after=getattr(client,'usage',{})
            record['response_info']=dict(getattr(client,'last_response_info',{}))
            record['usage_delta']=({k:usage_after[k]-usage_before.get(k,0) for k in usage_after
                                   if type(usage_after[k]) is int and type(usage_before.get(k,0)) is int}
                                  if record['response_info'].get('usage_reported') else None)
    final=None
    if client is not None:
        for group in initial_batches: run('holistic',group)
        for req in request.requirements: run('requirement',[req])
        for group in initial_batches: run('model',group)
        final=[]
        for group in review_batches(request.requirements,passes,batch_size):
            if len(group)>batch_size:
                diagnostic('linked_review_group','有关联的问题需求保留在同批复核，超过目标批大小；未拆断依据。',
                           stage='review',requirement_ids=[r.id for r in group])
            rows=run('review',group,compact_history(passes,group))
            if rows: final.extend(rows)
    else: diagnostic('rules_only','当前只执行显式规则；自然语言、多图联合满足情况尚未由模型检查。')
    final_map={r['requirement_id']:r for r in final or []}
    assessments=[]
    for req in request.requirements:
        row=deepcopy(final_map.get(req.id))
        history=[r for p in passes if p['stage']!='review' for r in p['assessments'] if r['requirement_id']==req.id]
        if row is None:
            row=unknown(req,['未完成最终模型复核。'],[m.id for m in request.models])
            preferred=[r for p in passes if p['stage']=='requirement' for r in p['assessments'] if r['requirement_id']==req.id]
            prior=(preferred or history)
            if prior:
                preliminary=deepcopy(prior[-1])
                row.update(model_ids=preliminary['model_ids'],evidence=preliminary['evidence'],
                           missing_dimensions=preliminary['missing_dimensions'],preliminary_assessment=preliminary,
                           coverage='部分覆盖' if preliminary['coverage']=='完全覆盖' else preliminary['coverage'],
                           summary='最终复核未完成。初步检查（待复核）：'+preliminary['summary'])
            row['assessment_status']='待复核'
        else: row['assessment_status']='模型复核完成'
        related=[d for d in diagnostics if (req.id in d.get('requirement_ids',[]) or d.get('requirement_id')==req.id
                 or not d.get('requirement_ids') and not d.get('requirement_id'))]
        if req.id in final_map and row['consistency']=='一致':
            # A positive verdict requires all planned perspectives to complete and agree.
            if len(history)!=3 or any(r['consistency']!='一致' or r.get('validation_errors') for r in history) or any(d['code'] in ('model_call_failed','context_budget_exceeded','call_budget_exceeded','external_or_preprocessed_source','unknown_diagram_type','assessment_row_invalid') for d in related):
                row['consistency']='无法判断'; row['review_notes'].append('不同轮次不一致、检查未完成或输入存在未展开内容，需要人工复核。')
                if row['coverage']=='完全覆盖': row['coverage']='部分覆盖'
        checks=[c for c in rule_checks if c['requirement_id']==req.id]
        conflicts=[c for c in checks if c['verdict']=='存在冲突']
        if conflicts:
            c=conflicts[0]
            row.update(consistency='存在冲突',coverage='部分覆盖',summary=c['summary'])
            row['model_ids']=sorted(set(row['model_ids']+[c['model_id']]))
            row['evidence']=row['evidence']+c['evidence']
            row['review_notes'].append('结论来自显式规则的模型路径；规则与自然语言的对应关系需人工确认。')
        elif checks:
            row['review_notes'].append('显式规则检查通过只能支持对应规则，不能据此确认原始需求整体满足。')
            passed=[c for c in checks if c['verdict']=='一致']
            if passed and final is None:
                row.update(coverage='部分覆盖',summary='模型已表达显式规则中的顺序和条件；原始需求整体满足情况仍待检查。')
                row['evidence']=passed[0]['evidence']
        row['requirement_source']=req.source()
        row['check_scope']={'requirements':[req.id],'models':[m.id for m in request.models]}
        assessments.append(row)
    # Rules are already explicit issue-level checks; keep their evidence/path scope.
    for rule in rule_checks:
        if rule['verdict']!='存在冲突': continue
        model=models[rule['model_id']]
        model_ev=[e for e in rule['evidence'] if e['artifact_id']==model.id]
        if not model_ev: continue
        anchor=model_ev[-1]
        raw={'type':'表达错误','title':rule['summary'],'requirement_ids':[rule['requirement_id']],
             'model_id':model.id,'location':{'kind':'错误位置','start_line':anchor['start_line'],'end_line':anchor['end_line'],'element':'显式规则反例路径'},
             'expected':'满足用户提供的显式 before 规则；规则与自然语言需求的对应仍需人工确认。','observed':rule['summary'],
             'recommendation':'修正反例路径中的顺序或守卫，阻止规则所禁止的路径。','searched_model_ids':[model.id],
             'path':[n['label'] for n in rule['path']],'missing_content':'','evidence':rule['evidence']}
        rule_issues,_=validate_issues([raw],request.requirements,request.models)
        passes.append({'stage':'explicit_rule','issues':rule_issues,'assessments':[]})
    issues,dismissed,review_errors=reconcile_issues(passes,request.requirements,request.models)
    for message in review_errors: diagnostic('issue_review_incomplete',message,stage='review')
    if any(p['stage']=='review' for p in passes) and any(i['status']=='待复核' for i in issues):
        diagnostic('issue_review_incomplete','存在未处理的旧问题；保留待复核，不由空问题列表清除。',stage='review')
    unlocalized=[]
    for p in passes:
        for row in p['assessments']:
            related_issues=[i for i in p.get('issues',[]) if row['requirement_id'] in i['requirement_ids']]
            lacks_conflict=row['consistency']=='存在冲突' and not any(i['type'] in ('表达错误','跨图冲突') for i in related_issues)
            lacks_omission=bool(row['missing_dimensions']) and not any(i['type'] in ('疑似遗漏','信息不足') for i in related_issues)
            if lacks_conflict or lacks_omission:
                unlocalized.append({'requirement_id':row['requirement_id'],'stage':p['stage'],'summary':row['summary'],
                                    'missing_dimensions':row['missing_dimensions'],
                                    'reason':'冲突或缺失维度没有可校验的逐问题描述，不能自动猜成具体图上的错误或遗漏。'})
    for row in assessments:
        if row['consistency']=='一致' and any(row['requirement_id'] in i['requirement_ids'] for i in issues):
            row['consistency']='无法判断'
            if row['coverage']=='完全覆盖': row['coverage']='部分覆盖'
            diagnostic('assessment_issue_disagreement','覆盖附表称一致，但仍有未排除问题，降为无法判断。',requirement_id=row['requirement_id'])
    bad_codes={'retrieval_failed','model_call_failed','invalid_evidence','unsupported_rule_or_syntax','invalid_rule','context_budget_exceeded','call_budget_exceeded','invalid_issue','missing_issue_details','issue_review_incomplete','issue_contract_missing','unknown_diagram_type','external_or_preprocessed_source','assessment_row_invalid','assessment_issue_disagreement'}
    status='cancelled' if stopped else ('partial' if client is None or len(final_map)!=len(reqs) or any(d['code'] in bad_codes for d in diagnostics) else 'completed')
    summary=issue_summary(issues)
    summary['unlocalized_observations']=len(unlocalized)
    report={'schema_version':'1.0','output_design_version':'2.0','run_id':request.run_id or uuid.uuid4().hex,'run_status':status,'links':links,
            'assessments':assessments,'issues':issues,'findings':issues,'issue_summary':summary,'dismissed_issues':dismissed,
            'unlocalized_observations':unlocalized,'execution_failures':[d for d in diagnostics if d['code'] in bad_codes],
            'check_scope':[{'requirement_id':r.id,'model_ids':[m.id for m in request.models],
                            'completed_stages':sorted({p['stage'] for p in passes if any(a['requirement_id']==r.id and not a.get('validation_errors') for a in p['assessments'])}),
                            'unfinished_stages':[s for s in ('holistic','requirement','model','review') if not any(p['stage']==s and any(a['requirement_id']==r.id and not a.get('validation_errors') for a in p['assessments']) for p in passes)]}
                           for r in request.requirements],
            'rule_checks':rule_checks,'passes':passes,'diagnostics':diagnostics,'call_records':call_records,
            'inputs':[{'id':a.id,'text':a.text,'source':a.source(),'diagram_type':a.diagram_type,'sha256':hashlib.sha256(a.text.encode()).hexdigest()} for a in artifacts],
            'provenance':{'version':'0.1.0','prompt_version':prompts.VERSION,'mode':getattr(client,'provenance',{}).get('mode','injected-client-unverified') if client else 'explicit-rules-only',
                'retriever':getattr(retriever,'provenance',{'name':type(retriever).__name__}),
                'model':getattr(client,'provenance',None),'calls':calls,'usage':getattr(client,'usage',{}),'elapsed_seconds':round(time.monotonic()-start,3),
                'requirement_batch_size':batch_size,'baseline_planned_calls':len(request.requirements)+3*len(initial_batches),
                'limits':['逐字证据校验只验证引用真实，不证明 AI 推理正确。','未支持的行为语法不做路径证明。','本版模型逐项检查使用完整源码由 AI 提取，未实现通用 UML 解析器。']}}
    event('complete',run_status=status)
    return report
