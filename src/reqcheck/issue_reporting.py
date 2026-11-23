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
    lines = ['# 模型问题清单', '',
             f"运行状态：{report['run_status']}；模式：{report['provenance']['mode']}。", '',
             f"业务冲突 {counts['conflicts']} 项；疑似遗漏 {counts['suspected_omissions']} 项；信息不足 {counts['information_gaps']} 项；待复核 {counts['pending_review']} 项。", '',
             f"执行失败或检查限制 {len(failures)} 项。模型复核完成不等于人工确认；原文校验只确认引用真实。", '',
             '主表按图定位问题；[需求覆盖附表](coverage.md)和[需求模型对照附表](comparison.md)单独查看。', '']
    if report['run_status'] != 'completed':
        lines += ['**本次检查未完成，保留已发现的问题供复核；问题数为 0 也不能表示模型正确。**', '']
    if report.get('provenance',{}).get('check_strategy')=='candidate_first':
        lines += ['检查策略：逐需求仅检查候选图；每张图反向走查补充关联；关联子模型整体检查；仅相关问题复核。', '',
                  f"初始候选 {len(report.get('initial_links',[]))} 对，反向补充候选 {len(report.get('recovered_links',[]))} 对。详见 retrieval-audit.json；实际输入范围见 call-records.json。", '']
    if not issues:
        lines += ['当前没有通过定位及证据校验的逐问题结果。请查看下方执行失败和未定位观察；不将调用失败写成模型遗漏。', '']
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
            if issue['missing_content']:
                lines += ['**未发现表达：** '+text(issue['missing_content'])+'（疑似遗漏，不是已确认缺失）。', '']
            if issue['path']:
                lines += ['**反例路径／条件：** '+text(' → '.join(issue['path'])), '']
            lines += ['**修改建议：** '+text(issue['recommendation']), '', '**原文证据：**', '']
            for e in issue['evidence']:
                lines += [f"- {text(e['artifact_id'])}：{text(e['source']['file'])}，第 {e['start_line']}–{e['end_line']} 行。"]
                lines += ['  > '+text(line) for line in e['quote'].splitlines()]
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
        lines += ['## 图职责与未核查内容', '', '覆盖和一致性只针对适用检查点；职责外内容不算遗漏。逐轮判定见 scope-audit.json。', '']
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
