import csv
import json
from pathlib import Path
from .comparison import write_comparison
from .issue_reporting import write_issue_artifacts

def cell(value):
    if isinstance(value,(dict,list)): value=json.dumps(value,ensure_ascii=False)
    value=str(value)
    # Treat untrusted source text as a value when a CSV is opened in Excel.
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value

def write_report(report,directory):
    path=Path(directory).resolve()
    path.mkdir(parents=True,exist_ok=True)
    names=('report.json','assessments.csv','links.csv','report.md','comparison.md','coverage.md','issues.json','issues.csv','execution-failures.json','call-records.json','retrieval-audit.json','scope-audit.json')
    if any((path/name).exists() for name in names): raise FileExistsError('Report files already exist; choose a new output directory')
    (path/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (path/'call-records.json').write_text(json.dumps(report.get('call_records',[]),ensure_ascii=False,indent=2),encoding='utf-8')
    if 'scope_decisions' in report:
        (path/'scope-audit.json').write_text(json.dumps({'scope_decisions':report['scope_decisions'],'scope_exclusions':report['scope_exclusions'],
            'note':'程序校验已声明检查维度的职责边界；需求拆分与分类仍依赖模型，需人工复核。'},ensure_ascii=False,indent=2),encoding='utf-8')
    if 'initial_links' in report:
        (path/'retrieval-audit.json').write_text(json.dumps({'initial_links':report['initial_links'],'recovered_links':report['recovered_links'],
            'check_scope':report['check_scope'],'note':'反向补回是有来源证据的候选，不是人工金标准；不得用未补回证明原检索完整。'},ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['requirement_id','coverage','consistency','summary','coverage_scope','unchecked_dimensions','checks','model_ids','requirement_source','evidence','missing_dimensions','review_notes']
    with (path/'assessments.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
        for row in report['assessments']: writer.writerow({k:cell(row.get(k,'')) for k in fields})
    fields=['requirement_id','model_id','score','method','status']
    with (path/'links.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
        for row in report['links']: writer.writerow({k:cell(row.get(k,'')) for k in fields})
    lines=['# 需求—模型检查报告','',f"运行：{report['run_id']}；状态：{report['run_status']}",
           f"模式：{report['provenance']['mode']}；调用次数：{report['provenance']['calls']}",'',
           '关联表示候选相关性。未发现覆盖需要人工复核；显式规则通过不等于原始需求整体满足。','',
           '需求原文、模型证据位置、最终结论和中间发现并排见 [需求—模型对照表](comparison.md)。','']
    for a in report['assessments']:
        lines.extend([f"## {a['requirement_id']}",'',f"覆盖：{a['coverage']}；语义一致性：{a['consistency']}",'',a['summary'],''])
        if a.get('preliminary_assessment'):
            prior=a['preliminary_assessment']
            lines.extend([f"初步覆盖：{prior['coverage']}；初步语义：{prior['consistency']}（未完成最终复核）。",''])
        for e in a['evidence']:
            source=e.get('source') or next((i['source'] for i in report['inputs'] if i['id']==e['artifact_id']),{})
            lines.append(f"- 证据 `{e['artifact_id']}`：{source.get('file','')}，第 {e['start_line']}–{e['end_line']} 行")
            lines.extend('  > '+line for line in e['quote'].splitlines())
        if a['missing_dimensions']: lines.extend(['','未发现表达：'+'；'.join(a['missing_dimensions'])])
        if a.get('unchecked_dimensions'): lines.extend(['','职责外，未核查：'+'；'.join(a['unchecked_dimensions'])+'。以上覆盖与一致性仅针对适用检查点。'])
        if a['review_notes']: lines.extend(['','人工复核/限制：'+'；'.join(a['review_notes'])])
        lines.append('')
    if report['rule_checks']:
        lines.extend(['## 显式规则结果',''])
        for r in report['rule_checks']:
            lines.append(f"- {r['requirement_id']} → {r['model_id']}：{r['verdict']}。{r['summary']}")
            if r['path']: lines.append('  路径：'+' → '.join(n['label'] for n in r['path']))
    if report['diagnostics']:
        lines.extend(['','## 执行说明',''])
        lines.extend(f"- {d['code']}：{d['message']}" for d in report['diagnostics'])
    (path/'coverage.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_comparison(report,path)
    write_issue_artifacts(report,path,cell)
    return path
