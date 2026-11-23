"""Human-readable comparison from saved report data; never invokes a model."""
from html import escape
from pathlib import Path


def table_cell(value):
    return escape(str(value), quote=False).replace('|', '\\|').replace('\r\n', '\n').replace('\n', '<br>')


def comparison_markdown(report):
    artifacts = {a['id']: a for a in report['inputs']}
    history = {}
    for result in report.get('passes', []):
        for row in result['assessments']:
            history.setdefault(row['requirement_id'], []).append((result['stage'], row))
    failures = [d for d in report.get('diagnostics', []) if d['code'] == 'model_call_failed']
    initial_conflicts = sorted({rid for rid, rows in history.items()
                                if any(r['consistency'] == '存在冲突' for stage, r in rows)})
    p = report['provenance']
    lines = ['# 需求—模型对照表', '',
             f"运行状态：{report['run_status']}；模式：{p['mode']}；调用尝试：{p['calls']}；调用或校验失败记录：{len(failures)}。", '',
             '本表从本次 report.json 导出，未重新调用模型，也未读取人工预期答案。', '',
             '**最终结论与中间发现分开显示。未复核发现可供人工检查，不代表完整检查已经通过。**', '',
             '未完成项中的“未发现覆盖”是原程序的占位结果，不表示已确认需求缺失。候选关系只表示检索相关性，不能作为正确性证据。', '',
             '已保存轮次里的冲突需求：'+('、'.join(initial_conflicts) if initial_conflicts else '没有记录；不能据此判定模型无冲突。'), '',
             '| 需求编号、原文及位置 | 最终覆盖／语义结论 | 已保存的中间检查（未替代最终结论） | 模型证据位置与检索候选 |',
             '|---|---|---|---|']
    details = []
    for final in report['assessments']:
        rid = final['requirement_id']
        req = artifacts.get(rid, {})
        source = req.get('source', final.get('requirement_source', {}))
        source_label = f"{source.get('file', '')}，第 {source.get('start_line', '?')}–{source.get('end_line', '?')} 行"
        observations = history.get(rid, [])
        evidence = []
        seen = set()
        for row in [final]+[r for stage, r in observations]:
            for e in row.get('evidence', []):
                if e['artifact_id'] == rid:
                    continue
                key = (e['artifact_id'], e['start_line'], e['end_line'], e['quote'])
                if key not in seen:
                    seen.add(key)
                    evidence.append(e)
        ev_labels = []
        for e in evidence:
            file = artifacts.get(e['artifact_id'], {}).get('source', {}).get('file', '')
            ev_labels.append(f"{e['artifact_id']}：{file}，第 {e['start_line']}–{e['end_line']} 行")
        candidates = sorted({l['model_id'] for l in report.get('links', []) if l['requirement_id'] == rid})
        candidate_label = '候选（仅相关）：'+('、'.join(candidates) or '无检索候选')
        initial = '\n\n'.join(f"{stage} 轮：{r['coverage']}／{r['consistency']}。{r['summary']}"
                               for stage, r in observations) or '没有保存成功的检查结论。'
        verdict = f"{final['coverage']}／{final['consistency']}。{final['summary']}"
        values = [f"{rid}\n{req.get('text', '原文未记录')}\n来源：{source_label}",
                  verdict, initial, '\n'.join(ev_labels or ['无有效模型证据'])+'\n'+candidate_label]
        lines.append('| '+' | '.join(table_cell(v) for v in values)+' |')
        details += [f'## {rid}：证据与复核', '', table_cell(req.get('text', '')), '',
                    '需求位置：'+table_cell(source_label), '',
                    '**最终结论：** '+table_cell(verdict), '',
                    '**中间检查（须人工复核）：** '+table_cell(initial), '']
        for e in evidence:
            file = artifacts.get(e['artifact_id'], {}).get('source', {}).get('file', '')
            details += [f"- 模型证据 {e['artifact_id']}：{table_cell(file)}，第 {e['start_line']}–{e['end_line']} 行"]
            details += ['  > '+escape(line, quote=False) for line in e['quote'].splitlines()]
        notes = list(final.get('review_notes', []))
        missing = list(final.get('missing_dimensions', []))
        for stage, row in observations:
            notes.extend(f'{stage}：{n}' for n in row.get('review_notes', []))
            missing.extend(f'{stage}：{n}' for n in row.get('missing_dimensions', []))
        if missing:
            details += ['', '未发现表达的维度：'+table_cell('；'.join(missing))]
        if notes:
            details += ['', '复核说明：'+table_cell('；'.join(notes))]
        details += ['']
    lines += ['', '## 执行限制', '']
    for d in report.get('diagnostics', []):
        lines += ['- '+table_cell(f"{d.get('stage', '')} {d['code']}：{d['message']}")]
    if failures:
        lines += ['', '失败原因以原始 diagnostics 为准；只有通用 ModelError 的历史记录，无法反推出是网络、额度、超时还是回答截断。']
    return '\n'.join(lines+['']+details)+'\n'


def write_comparison(report, directory):
    path = Path(directory) / 'comparison.md'
    with path.open('x', encoding='utf-8') as f:
        f.write(comparison_markdown(report))
    return path
