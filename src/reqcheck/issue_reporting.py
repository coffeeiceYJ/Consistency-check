"""Primary user report: per-diagram actionable issues, then execution failures."""
import json
from html import escape
from pathlib import Path
from .issues import issue_summary


def text(value):
    return escape(str(value), quote=False)


def issue_markdown(report):
    issues = report.get('issues', [])
    counts = issue_summary(issues)
    failures = report.get('execution_failures', [])
    inputs = {a['id']: a for a in report['inputs']}
    task_based=report.get('output_design_version')=='3.0'
    lines = ['# 模型问题清单', '',
             f"运行状态：{report['run_status']}；模式：{report['provenance']['mode']}。", '',
             f"业务冲突 {counts['conflicts']} 项；疑似遗漏 {counts['suspected_omissions']} 项；信息不足 {counts['information_gaps']} 项；待复核 {counts['pending_review']} 项。", '',
             f"执行失败或检查限制 {len(failures)} 项。模型复核完成不等于人工确认；原文校验只确认引用真实。", '',
             ('主表按图列出需求一致性问题；建模质量建议单独列出，不计入业务冲突。完整范围与调用记录见 report.json。' if task_based else
              '主表按图定位问题；[需求覆盖附表](coverage.md)和[需求模型对照附表](comparison.md)单独查看。'), '']
    if task_based:
        selected=len([p for p in report.get('classified_links',[]) if p.get('decision')!='no'])
        lines += [f"策略：任务式检查；路由：{report['provenance'].get('routing_mode')}；真实模型调用 {report['provenance']['calls']} 次；缓存命中 {report['provenance'].get('cache_hits',0)} 次。", '',
                  report['provenance'].get('routing_reason',''), '',
                  (f"初筛候选 {len(report.get('candidate_links',[]))} 对；相关或不确定保留 {selected} 对。关联不表示满足，未选材料不能据此宣称已核查。" if report['provenance'].get('routing_mode')=='classified' else
                   '本次直接提供完整材料，未进行关联分类；输入范围不标作全关联。实际证据对应关系见report.json的evidence_links。'), '']
    if report['run_status'] != 'completed':
        lines += ['**本次检查未完成，保留已发现的问题供复核；问题数为 0 也不能表示模型正确。**', '']
    if report.get('provenance',{}).get('check_strategy')=='candidate_first':
        lines += ['检查策略：逐需求仅检查候选图；每张图反向走查补充关联；关联子模型整体检查；仅相关问题复核。', '',
                  f"初始候选 {len(report.get('initial_links',[]))} 对，反向补充候选 {len(report.get('recovered_links',[]))} 对。详见 retrieval-audit.json；实际输入范围见 call-records.json。", '']
    if not issues:
        lines += [('本次已检查范围内未发现需求一致性问题。职责外内容见未核查范围，结论不覆盖未提供的材料。' if report['run_status']=='completed' else
                   '当前没有通过定位及证据校验的逐问题结果。请查看下方执行失败和未定位观察；不将调用失败写成模型遗漏。'), '']
    grouped = {}
    for issue in issues:
        grouped.setdefault(issue['model_id'], []).append(issue)
    for mid, group in sorted(grouped.items(), key=lambda pair: str(pair[0] or '~')):
        model = inputs.get(mid, {})
        loc = group[0]['location']
        label = f"{Path(loc['file']).name}（{model.get('diagram_type','')}，{mid}）" if loc else '尚未确定应修改哪张图'
        lines += ['## '+text(label), '']
        for issue in group:
            location = issue['location']
            lines += ['### '+text(issue['id']+'：'+issue['title']), '',
                      '**类型：** '+issue['type']+'；**复核状态：** '+issue['status'], '']
            if location:
                lines += [f"**{location['kind']}：** {text(location['file'])}，第 {location['start_line']}–{location['end_line']} 行。", '']
                if location['element']: lines += ['**涉及元素：** '+text(location['element']), '']
            else:
                lines += ['**位置：** 未定位，不虚构错误行号；已查图：'+text('、'.join(issue['searched_model_ids'])), '']
            lines += ['**相关需求：** '+text('、'.join(issue['requirement_ids'])), '',
                      '**需求要求：** '+text(issue['expected']), '',
                      '**模型实际／具体差异：** '+text(issue['observed']), '']
            if issue['missing_content'] and issue['type']=='疑似遗漏':
                lines += ['**未发现表达：** '+text(issue['missing_content'])+'（疑似遗漏，不是已确认缺失）。', '']
            if issue['path']:
                lines += ['**反例路径／条件：** '+text(' → '.join(issue['path'])), '']
            lines += ['**修改建议：** '+text(issue['recommendation']), '', '**原文证据：**', '']
            for e in issue['evidence']:
                lines += [f"- {text(e['artifact_id'])}：{text(e['source']['file'])}，第 {e['start_line']}–{e['end_line']} 行。"]
                lines += ['  > '+text(line) for line in e['quote'].splitlines()]
            lines += ['']
    if report.get('quality_notes'):
        lines += ['## 建模质量建议（不计入业务冲突）', '', '以下为依据通用建模准则的模型建议，不是已确认业务错误，需人工判断。', '']
        for note in report['quality_notes']:
            lines += ['### '+text(note['model_id']+'：'+note['summary']),'',
                      '准则：'+text(note['principle']),'','建议：'+text(note['recommendation']),'']
            for e in note['evidence']:
                source=e.get('source') or inputs.get(e['artifact_id'],{}).get('source',{})
                lines += [f"- {text(e['artifact_id'])}：{text(source.get('file',''))}，第 {e['start_line']}–{e['end_line']} 行。"]
                lines += ['  > '+text(line) for line in e['quote'].splitlines()]
            lines += ['']
    elif task_based:
        lines += ['## 建模质量建议', '', '本次没有有效返回的建模质量建议；不表示模型质量已证明无问题。', '']
    if task_based and report.get('advisory_errors'):
        lines += [f"有 {len(report['advisory_errors'])} 条建议未通过格式或来源校验，未发布；不为可选建议重跑已通过校验的业务检查。详情见report.json。",'']
    if task_based:
        lines += ['## 需求检查范围', '']
        for row in report['assessments']:
            lines += ['- '+text(row['requirement_id']+'：'+row['coverage']+'／'+row['consistency']+'；涉及图：'+'、'.join(row['model_ids']))]
        lines += ['']
        limited=[s for s in report.get('model_structure',[]) if s['status']!='complete-supported-subset']
        if limited:
            lines += ['## 结构辅助分析限制','']
            lines += ['- '+text(s['model_id']+'：'+s['status']+'；'+s['reason']+'。仍须以完整源码人工复核。') for s in limited]
            lines += ['']
    if report.get('unlocalized_observations'):
        lines += ['## 未定位观察（未计入具体图的问题数）', '',
                  '这些返回没有有效的逐问题位置，不能把一段综合说明随意拆给多张图。', '']
        for item in report['unlocalized_observations']:
            lines += ['- '+text(f"{item['requirement_id']}／{item['stage']}：{item['summary']}"),
                      '  '+text(item['reason'])]
            if item.get('missing_dimensions'):
                lines += ['  未发现表达：'+text('；'.join(item['missing_dimensions']))]
        lines += ['']
    if report.get('scope_decisions'):
        lines += ['## 图职责与未核查内容', '', '覆盖和一致性只针对适用检查点；职责外内容不算遗漏。判定记录见 '+('report.json。' if task_based else 'scope-audit.json。'), '']
        for row in report['assessments']:
            if row.get('unchecked_dimensions'):
                lines += ['- '+text(row['requirement_id']+'：职责外，未核查：'+'；'.join(row['unchecked_dimensions']))]
        if report.get('scope_exclusions'):
            lines += ['', f"另有 {len(report['scope_exclusions'])} 条逐轮观察因超出图职责未发布为业务错误（可能重复）。"]
        lines += ['']
    lines += ['## 检查失败与未完成范围', '']
    for scope in report.get('check_scope',[]):
        if scope['unfinished_stages']:
            lines += ['- '+text(scope['requirement_id']+' 未完成：'+'、'.join(scope['unfinished_stages']))]
    if not failures:
        lines += ['没有记录执行失败。业务问题仍以原文证据和人工复核为准。', '']
    for d in failures:
        scope = d.get('requirement_ids') or ([d['requirement_id']] if d.get('requirement_id') else [])
        lines += ['- '+text(f"阶段 {d.get('stage','')}；调用 {d.get('call','—')}；需求 {'、'.join(scope) or '见检查范围'}；原因 {d.get('failure_kind',d['code'])}。{d['message']}")]
        if d.get('http_status'): lines += [f"  HTTP 状态：{d['http_status']}"]
        if d.get('finish_reason'): lines += ['  返回结束原因：'+text(d['finish_reason'])]
    if report.get('dismissed_issues'):
        lines += ['', '## 已排除的问题（保留复核依据）', '']
        for item in report['dismissed_issues']:
            lines += ['- '+text('、'.join(item['origin_ids'])+'：'+item['reason'])]
    if report.get('resolved_validation_failures'):
        lines += ['', '## 已纠正的返回格式问题', '', '原失败记录保留；仅同范围纠正通过校验的诊断标为已解决，仍计入调用量。', '']
        for d in report['resolved_validation_failures']:
            lines += ['- '+text(f"调用 {d['call']} → 纠正调用 {d['resolved_by_call']}：{d['code']}")]
    return '\n'.join(lines)+'\n'


def write_issue_artifacts(report, directory, safe_cell):
    import csv
    root = Path(directory)
    summary = issue_summary(report.get('issues', []))
    payload = {'issue_schema_version':'1.0','run_id':report['run_id'],'run_status':report['run_status'],
               'summary':summary,'issues':report.get('issues',[]),'dismissed_issues':report.get('dismissed_issues',[]),
               'unlocalized_observations':report.get('unlocalized_observations',[])}
    (root/'issues.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['问题编号','图文件','图类型','位置类型','起始行','结束行','问题类型','复核状态','问题说明','相关需求',
            '需求要求','模型实际表达','反例路径','缺失内容','修改建议','证据']
    with (root/'issues.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
        for i in report.get('issues',[]):
            loc=i['location'] or {}
            values=[i['id'],loc.get('file','未定位'),loc.get('diagram_type',''),loc.get('kind','未定位'),
                    loc.get('start_line',''),loc.get('end_line',''),i['type'],i['status'],i['title'],
                    '、'.join(i['requirement_ids']),i['expected'],i['observed'],' → '.join(i['path']),
                    i['missing_content'],i['recommendation'],i['evidence']]
            writer.writerow({k:safe_cell(v) for k,v in zip(fields,values)})
    (root/'execution-failures.json').write_text(json.dumps({'failures':report.get('execution_failures',[]),
        'resolved_validation_failures':report.get('resolved_validation_failures',[]),
        'check_scope':report.get('check_scope',[])},ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'report.md').write_text(issue_markdown(report),encoding='utf-8')
