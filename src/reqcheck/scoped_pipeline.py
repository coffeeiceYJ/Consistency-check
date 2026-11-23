"""Candidate-first workflow. One-diagram audits recover possible retrieval misses."""
import hashlib
import json
import time
import uuid
from copy import deepcopy
from . import prompts
from .pipeline import validate_response_parts, unknown
from .issues import validate_issues, reconcile_issues, issue_summary, checked_evidence
from .model_client import ModelError
from .scoping import select_models, scope_groups, recover_links, model_batches
from .applicability import apply_policy, all_outside
from .planning import compact_history
from .activity import check_before, UnsupportedActivity


def check_scoped(request, retriever, client, progress=None, cancelled=None):
    start=time.monotonic(); passes=[]; diagnostics=[]; records=[]; links=[]; recovered=[]; rules=[]
    scope_decisions=[]; scope_exclusions=[]
    stopped=False; calls=0; audit_ok=set(); not_required={r.id:[] for r in request.requirements}
    progress=progress or (lambda e:None); cancelled=cancelled or (lambda:False)
    def event(phase,**data): progress({'phase':phase,**data})
    def diagnostic(code,message,**data): diagnostics.append({'code':code,'message':message,**data})
    def stop():
        nonlocal stopped
        stopped=stopped or bool(cancelled()); return stopped
    reqs={r.id:r for r in request.requirements}; models={m.id:m for m in request.models}
    if not reqs or not models or len(set(reqs)|set(models))!=len(request.requirements)+len(request.models):
        raise ValueError('Empty inputs or duplicate artifact IDs')
    max_calls=request.options.get('max_calls',30); max_chars=request.options.get('max_context_chars',120000)
    if type(max_calls)!=int or max_calls<1 or type(max_chars)!=int or max_chars<1000:
        raise ValueError('Invalid call/context budget')
    retries=request.options.get('validation_retries',1)
    if type(retries)!=int or retries not in (0,1): raise ValueError('validation_retries must be 0 or 1')
    event('retrieval')
    if not stop():
        try:
            links=retriever.retrieve(request)
            if not isinstance(links,list) or any(not isinstance(l,dict) or l.get('requirement_id') not in reqs or l.get('model_id') not in models for l in links):
                raise ValueError('Invalid retrieval links')
        except Exception as exc:
            links=[]; diagnostic('retrieval_failed',f'检索失败（{type(exc).__name__}）；转入逐图反向补漏，不全量展开每条需求。')
    initial_links=deepcopy(links)
    for r in request.requirements:
        if not select_models([r],request.models,links):
            diagnostic('no_candidates','没有候选，暂不逐需求检查；由反向检查尝试补回关联，不直接判遗漏。',requirement_id=r.id)
    for m in request.models:
        if m.diagram_type=='unknown': diagnostic('unknown_diagram_type','图类型无法识别，仅能检查原始文本。',model_id=m.id)
        if any(l.strip().startswith(('!include','!define','!function','!procedure','!if')) for l in m.text.splitlines()):
            diagnostic('external_or_preprocessed_source','未展开预处理/外部引用，不据此确认完整。',model_id=m.id)

    def consume(stage,requirements,scope,response,reverse=False,supplemental=False):
        ids=[r.id for r in requirements]; mids=[m.id for m in scope]; before_diagnostics=len(diagnostics)
        if 'issues' not in response:
            diagnostic('issue_contract_missing','回答缺少 issues 数组。',stage=stage,call=calls,requirement_ids=ids)
        target=requirements
        if reverse:
            schema_step='related_requirement_ids'
            related=response.get('related_requirement_ids')
            if not isinstance(related,list) or any(not isinstance(i,str) or i not in reqs for i in related) or len(set(related))!=len(related):
                raise ValueError('Invalid related_requirement_ids')
            target=[reqs[r] for r in related]
        response,decisions,excluded,scope_errors=apply_policy(response,target,scope)
        scope_decisions.extend(dict(d,stage=stage,call=calls) for d in decisions)
        scope_exclusions.extend(dict(d,stage=stage,call=calls) for d in excluded)
        for error in scope_errors:
            diagnostic('applicability_contract_invalid','Invalid typed applicability contract; requires review.',stage=stage,call=calls,requirement_ids=ids,model_ids=mids,detail=error)
        # Enforce the actual provided material boundary, not the global model list.
        issue_rows,rejected=validate_issues(response.get('issues',[]),target,scope)
        for i in issue_rows: i['observed_in']=[{'stage':stage,'call':calls}]
        for rejection in rejected:
            diagnostic('invalid_issue','问题定位或证据校验失败。',stage=stage,call=calls,requirement_ids=ids,**rejection)
        saved={'stage':stage,'call':calls,'model_ids':mids,'requirement_ids':ids,'issues':issue_rows,'assessments':[],
               'dismissed_issues':response.get('dismissed_issues',[]),'supplemental':supplemental,'model_reverse':reverse}
        if not isinstance(saved['dismissed_issues'],list):
            saved['dismissed_issues']=[]; diagnostic('invalid_issue','排除结果格式错误。',stage=stage,call=calls,requirement_ids=ids)
        bounded_dismissals=[]
        for decision in saved['dismissed_issues']:
            try:
                ev=decision.get('evidence') if isinstance(decision,dict) else None
                cited=[e.get('artifact_id') for e in ev if isinstance(e,dict) and e.get('artifact_id') in {r.id for r in target}] if isinstance(ev,list) else []
                checked_evidence(ev,target,scope,cited)
                bounded_dismissals.append(decision)
            except ValueError:
                diagnostic('invalid_issue','排除问题引用未提供的图或无效原文。',stage=stage,call=calls,requirement_ids=ids)
        saved['dismissed_issues']=bounded_dismissals
        passes.append(saved)
        schema_step='assessment_rows'
        rows,errors=validate_response_parts(response,target,scope); saved['assessments']=rows
        for error in errors:
            diagnostic('assessment_row_invalid',error['reason'],stage=stage,call=calls,requirement_ids=[error['requirement_id']] if error['requirement_id'] else ids)
        for row in rows:
            row['scope_model_ids']=mids
            if row['validation_errors']:
                diagnostic('invalid_evidence','引用未通过原文校验，结论降为无法判断。',stage=stage,call=calls,requirement_ids=[row['requirement_id']])
            relevant=[i for i in issue_rows if row['requirement_id'] in i['requirement_ids']]
            if row['consistency']=='存在冲突' and not any(i['type'] in ('表达错误','跨图冲突') for i in relevant) or row['missing_dimensions'] and not any(i['type'] in ('疑似遗漏','信息不足') for i in relevant):
                diagnostic('missing_issue_details','差异/缺失维度没有可校验的逐问题描述，保留未定位观察。',stage=stage,call=calls,requirement_ids=[row['requirement_id']])
        if target and not rows: raise ValueError('No valid assessment rows')
        status='partial' if len(diagnostics)>before_diagnostics else 'completed'
        if reverse and status=='completed': audit_ok.update(mids)
        event('model_result',stage=stage,call=calls,issues=len(issue_rows),model_ids=mids,status=status)
        saved['status']=status
        return saved

    def run_once(stage, requirements, scope, previous=None, reverse=False, supplemental=False, repair=None):
        nonlocal calls
        ids=[r.id for r in requirements]; mids=[m.id for m in scope]
        if stop(): return None
        if not scope: return None
        if calls>=max_calls:
            diagnostic('call_budget_exceeded','调用预算耗尽，本轮未执行。',stage=stage,requirement_ids=ids,model_ids=mids); return None
        data=prompts.payload(stage,requirements,scope,previous)
        data.update(check_strategy='candidate_first',model_reverse=reverse,supplemental=supplemental,
                    candidate_model_ids=mids,provided_model_ids=mids,
                    excluded_model_ids=[m for m in models if m not in mids],
                    scope_note='只能引用实际提供的图。局部未发现不等于整套模型遗漏；其他图正确不能消除本图冲突或职责内缺失。')
        data['scope_contract']=prompts.SCOPE_CONTRACT
        if repair:
            data['validation_repair']=repair
            data['repair_instruction']='本次是同一范围的结构纠正：重读完整原文，返回完整结果；根据 validation_repair 修正字段。check_ids 必须指向本次返回的 checks；issue 主 dimension 至少匹配一个引用检查点。location 只标有完整逐字证据覆盖的最小位置，不把未引用的相邻 else/endif 行包括进来。不得为了通过格式校验改动业务事实。'
        if reverse:
            data['task']=prompts.REVERSE_TASK
            data['response_contract']={'model_audits':'One independent audit per provided model; see scope_contract'}
        elif stage=='holistic':
            data['task']='对本轮关联需求与关联子模型联合检查条件、职责和业务结果；仅提供的图可作证据，不代表全系统所有图。检查跨图联合表达，同时保留单图自身冲突和职责内缺失。'
        if supplemental:
            data['task']=prompts.TASKS['requirement']+' 本次补查反向发现的新候选，仅检查该新增关联，不改写其他图已发现的问题。'
        size=len(prompts.SYSTEM)+len(json.dumps(data,ensure_ascii=False))
        if size>max_chars:
            diagnostic('context_budget_exceeded','包含系统提示词的上下文超限；没有静默截断。',stage=stage,requirement_ids=ids,model_ids=mids,input_chars=size); return None
        calls+=1; t=time.monotonic(); before=dict(getattr(client,'usage',{}))
        record={'call':calls,'stage':stage,'requirement_ids':ids,'model_ids':mids,'input_chars':size,
                'model_chars':sum(len(m.text) for m in scope),'status':'started','supplemental':supplemental}
        records.append(record); event('model_check',stage=stage,call=calls,requirement_ids=ids,model_ids=mids,supplemental=supplemental)
        if repair: record['repair_of_call']=repair['call']
        schema_step='transport'
        try:
            response=client.complete(prompts.SYSTEM,data)
            schema_step='response_object'
            if not isinstance(response,dict): raise ValueError('Expected response object')
            if reverse:
                schema_step='model_audits'
                raw=response.get('model_audits')
                if not isinstance(raw,list):
                    if len(scope)==1 and 'related_requirement_ids' in response:
                        raw=[dict(response,model_id=scope[0].id)]
                    else: raise ValueError('Missing per-model audits')
                if any(not isinstance(a,dict) or a.get('model_id') not in mids for a in raw):
                    raise ValueError('Unknown audit model')
                audits=[]
                for m in scope:
                    matches=[a for a in raw if a['model_id']==m.id]
                    try:
                        if len(matches)!=1: raise ValueError('Missing/duplicate audit')
                        audits.append(consume(stage,requirements,[m],matches[0],True,supplemental))
                    except (ValueError,TypeError,KeyError):
                        diagnostic('model_audit_invalid','Per-model audit validation failed.',stage=stage,call=calls,requirement_ids=ids,model_ids=[m.id])
                record['validated_model_ids']=[m.id for m in scope if m.id in audit_ok]
                saved={'audits':audits}
            else:
                schema_step='assessment_rows'
                saved=consume(stage,requirements,scope,response,False,supplemental)
            record['status']='partial' if any(d.get('call')==calls for d in diagnostics) else 'completed'
            saved['call']=calls; saved['status']=record['status']
            return saved
        except Exception as exc:
            safe=exc.diagnostic if isinstance(exc,ModelError) else {'failure_kind':'response_schema' if isinstance(exc,ValueError) else 'unexpected_error'}
            diagnostic('model_call_failed',f'调用或结构校验失败（{type(exc).__name__}）。',stage=stage,call=calls,requirement_ids=ids,model_ids=mids,validation_step=schema_step,**safe)
            record.update(status='failed',validation_step=schema_step,**safe); event('model_failed',stage=stage,call=calls,validation_step=schema_step,**safe); return None
        finally:
            record['elapsed_seconds']=round(time.monotonic()-t,3)
            info=dict(getattr(client,'last_response_info',{})); record['response_info']=info
            after=getattr(client,'usage',{})
            record['usage_delta']=({k:after[k]-before.get(k,0) for k in after if type(after[k]) is int and type(before.get(k,0)) is int} if info.get('usage_reported') else None)

    def run(stage, requirements, scope, previous=None, reverse=False, supplemental=False):
        result=run_once(stage,requirements,scope,previous,reverse,supplemental)
        if not retries or not result or result['status']!='partial' or calls>=max_calls: return result
        original=result['call']
        problems=[d for d in diagnostics if d.get('call')==original]
        correction=run_once(stage,requirements,scope,previous,reverse,supplemental,repair={'call':original,'errors':problems})
        if correction and correction['status']=='completed':
            # Keep the first attempt and all valid findings. Only replace its
            # broken assessment appendix after an independently validated retry.
            for d in problems: d['resolved_by_call']=correction['call']
            for p in passes:
                if p.get('call')==original:
                    p['superseded_assessments']=p['assessments']; p['assessments']=[]
                    p['superseded_by_call']=correction['call']
        return correction or result

    reverse_batches=model_batches(request.models,request.options.get('reverse_batch_size',4),request.options.get('reverse_batch_chars',24000))
    event('check_plan',strategy='candidate_first',initial_requirement_calls=sum(bool(select_models([r],request.models,links)) for r in request.requirements),
          reverse_audit_calls=len(reverse_batches),max_calls=max_calls,note='之后按补回关联和关联组安排补查、整体检查、必要复核。')
    for r in request.requirements:
        scope=select_models([r],request.models,links)
        if scope: run('requirement',[r],scope)
    # Short diagrams share transport; each retains its own validation boundary.
    for batch in reverse_batches:
        result=run('model',request.requirements,batch,reverse=True)
        for audit in (result or {}).get('audits',[]):
            m=models[audit['model_ids'][0]]
            extra=recover_links(audit,links,m); links.extend(extra); recovered.extend(extra)
            for link in extra: event('recovered_link',requirement_id=link['requirement_id'],model_id=link['model_id'])
    # One supplemental call per requirement with newly recovered diagrams; do not repeat old diagrams.
    for r in request.requirements:
        scope=select_models([r],request.models,recovered)
        if scope: run('requirement',[r],scope,supplemental=True)
    final={}
    for group in scope_groups(request.requirements,request.models,links,passes):
        outside=[]
        for r in group:
            history=[a for p in passes for a in p['assessments'] if a['requirement_id']==r.id]
            if history and all(not a['validation_errors'] and all_outside(a.get('checks',[]),request.models) for a in history):
                final[r.id]=deepcopy(history[-1]); not_required[r.id]=['holistic','review']; outside.append(r.id)
        group=[r for r in group if r.id not in outside]
        if not group: continue
        scope=select_models(group,request.models,links,passes)
        if not scope:
            for r in group:
                row=unknown(r,['候选检索及逐图反向检查未建立有证据的关联，交人工复核；不认定遗漏。'])
                row['evidence']=[{'artifact_id':r.id,'start_line':r.start_line,'end_line':r.end_line,'quote':r.text,'source':r.source(),'verified':True}]
                final[r.id]=row; not_required[r.id]=['requirement','holistic','review']
            continue
        holistic=run('holistic',group,scope)
        ids={r.id for r in group}
        observations=[row for p in passes for row in p['assessments'] if row['requirement_id'] in ids]
        need_review=any(ids.intersection(i['requirement_ids']) for p in passes for i in p.get('issues',[]))
        need_review=need_review or any(row['missing_dimensions'] or row['validation_errors'] or row['consistency']!='一致' and row['coverage']!='不适用' for row in observations)
        if need_review:
            reviewed=run('review',group,select_models(group,request.models,links,passes),compact_history(passes,group))
            if reviewed: final.update({r['requirement_id']:r for r in reviewed['assessments']})
        elif holistic:
            for row in holistic['assessments']:
                # A failed earlier call still needs an explicit unfinished scope.
                final[row['requirement_id']]=deepcopy(row); not_required[row['requirement_id']].append('review')

    # Explicit rules remain an optional additional source of evidence.
    for rule in request.rules:
        if stop(): break
        try:
            out=check_before(rule,reqs[rule['requirement_id']],models[rule['model_id']]); rules.append({**out,'requirement_id':rule['requirement_id'],'model_id':rule['model_id']})
            if out['verdict']=='存在冲突':
                m=models[rule['model_id']]; anchor=[e for e in out['evidence'] if e['artifact_id']==m.id][-1]
                raw={'type':'表达错误','title':out['summary'],'expected':'满足用户显式规则；与自然语言对应关系需人工确认。','observed':out['summary'],'recommendation':'修正显式规则所指出的路径。',
                     'requirement_ids':[rule['requirement_id']],'model_id':m.id,'searched_model_ids':[m.id],
                     'location':{'kind':'错误位置','start_line':anchor['start_line'],'end_line':anchor['end_line'],'element':'显式规则反例路径'},
                     'path':[n['label'] for n in out['path']],'missing_content':'','evidence':out['evidence']}
                items,_=validate_issues([raw],request.requirements,request.models); passes.append({'stage':'explicit_rule','issues':items,'assessments':[]})
        except (ValueError,KeyError,TypeError,UnsupportedActivity): diagnostic('invalid_rule','规则引用或语法不支持。')
    issues,dismissed,errors=reconcile_issues(passes,request.requirements,request.models)
    for e in errors: diagnostic('issue_review_incomplete',e,stage='review')
    if any(i['status']=='待复核' for i in issues): diagnostic('issue_review_incomplete','仍有待复核问题；未清除已有发现。',stage='review')
    assessments=[]; scopes=[]; unlocalized=[]
    bad_codes={'applicability_contract_invalid','model_audit_invalid','retrieval_failed','model_call_failed','invalid_evidence','invalid_rule','context_budget_exceeded','call_budget_exceeded','invalid_issue','missing_issue_details','issue_review_incomplete','issue_contract_missing','unknown_diagram_type','external_or_preprocessed_source','assessment_row_invalid','assessment_issue_disagreement'}
    for r in request.requirements:
        history=[a for p in passes for a in p['assessments'] if a['requirement_id']==r.id]
        row=deepcopy(final.get(r.id))
        if row is None:
            row=unknown(r,['检查未完成，已有具体问题仍保留。'])
            if history:
                prior=deepcopy(history[-1]); row.update(preliminary_assessment=prior,evidence=prior['evidence'],model_ids=prior['model_ids'],missing_dimensions=prior['missing_dimensions'],coverage='部分覆盖' if prior['coverage']=='完全覆盖' else prior['coverage'])
        row['assessment_status']=('未发现关联，待人工复核' if not select_models([r],request.models,links,passes) else
                                  '无需问题复核' if 'review' in not_required[r.id] else ('模型复核完成' if r.id in final else '待复核'))
        if row['coverage']=='不适用': row['assessment_status']='职责外，未核查'
        failures=[d for d in diagnostics if d['code'] in bad_codes and not d.get('resolved_by_call') and (not d.get('requirement_ids') or r.id in d['requirement_ids'])]
        conflict_history=any(a['consistency']=='存在冲突' for a in history)
        if row['consistency']=='一致' and (audit_ok!=set(models) or failures or any(a.get('validation_errors') for a in history) or conflict_history or any(r.id in i['requirement_ids'] for i in issues)):
            row['consistency']='无法判断'; row['review_notes'].append('存在未完成检查、轮次分歧或未排除问题，需人工确认。')
            if row['coverage']=='完全覆盖': row['coverage']='部分覆盖'
        actual=sorted({m for rec in records if r.id in rec['requirement_ids'] and rec['stage']!='model' for m in rec['model_ids']})
        row['requirement_source']=r.source(); row['check_scope']={'requirements':[r.id],'models':actual,'reverse_scanned_models':sorted(audit_ok)}
        assessments.append(row)
        completed={p['stage'] for p in passes if any(a['requirement_id']==r.id and not a['validation_errors'] for a in p['assessments'])}
        if audit_ok==set(models): completed.add('model')
        scopes.append({'requirement_id':r.id,'model_ids':actual,'reverse_scanned_models':sorted(audit_ok),'completed_stages':sorted(completed),
                       'not_required_stages':not_required[r.id],'unfinished_stages':[s for s in ('requirement','model','holistic','review') if s not in completed and s not in not_required[r.id]]})
    for p in passes:
        for row in p['assessments']:
            relevant=[i for i in p['issues'] if row['requirement_id'] in i['requirement_ids']]
            if row['consistency']=='存在冲突' and not any(i['type'] in ('表达错误','跨图冲突') for i in relevant) or row['missing_dimensions'] and not any(i['type'] in ('疑似遗漏','信息不足') for i in relevant):
                unlocalized.append({'requirement_id':row['requirement_id'],'stage':p['stage'],'summary':row['summary'],'missing_dimensions':row['missing_dimensions'],'reason':'缺少可校验的逐问题描述；保留观察，不编造位置。'})
    status='cancelled' if stopped else ('partial' if len(final)!=len(reqs) or any(d['code'] in bad_codes and not d.get('resolved_by_call') for d in diagnostics) or any(s['unfinished_stages'] for s in scopes) else 'completed')
    summary=issue_summary(issues); summary['unlocalized_observations']=len(unlocalized)
    event('complete',run_status=status)
    return {'schema_version':'1.0','output_design_version':'2.2','run_id':request.run_id or uuid.uuid4().hex,'run_status':status,
            'links':links,'initial_links':initial_links,'recovered_links':recovered,'assessments':assessments,'issues':issues,'findings':issues,
            'issue_summary':summary,'dismissed_issues':dismissed,'unlocalized_observations':unlocalized,
            'scope_decisions':scope_decisions,'scope_exclusions':scope_exclusions,
            'execution_failures':[d for d in diagnostics if d['code'] in bad_codes and not d.get('resolved_by_call')],
            'resolved_validation_failures':[d for d in diagnostics if d.get('resolved_by_call')],'check_scope':scopes,'rule_checks':rules,
            'passes':passes,'diagnostics':diagnostics,'call_records':records,
            'inputs':[{'id':a.id,'text':a.text,'source':a.source(),'diagram_type':a.diagram_type,'sha256':hashlib.sha256(a.text.encode()).hexdigest()} for a in request.requirements+request.models],
            'provenance':{'version':'0.2.0','check_strategy':'candidate_first','prompt_version':prompts.VERSION,'mode':getattr(client,'provenance',{}).get('mode','injected-client-unverified'),
                          'prompt_sha256':hashlib.sha256((prompts.SYSTEM+prompts.SCOPE_CONTRACT+prompts.REVERSE_TASK+json.dumps(prompts.TASKS,ensure_ascii=False)).encode()).hexdigest(),
                          'retriever':getattr(retriever,'provenance',{}),'model':getattr(client,'provenance',{}),'calls':calls,'usage':getattr(client,'usage',{}),
                          'elapsed_seconds':round(time.monotonic()-start,3),'model_source_chars_sent':sum(r['model_chars'] for r in records),
                          'limits':['候选关联不是满足性结论；反向补回关联尚非人工确认。','结构化图解析仍不完整；语义判断依赖模型。','局部缺失不能据此确认全局遗漏；其他图正确不能抵消单图冲突。']}}
