# 输出设计

## 当前默认：任务式3.0

默认只写report.md和report.json，分别给人阅读、给程序消费和追溯。不修改模型返回的语义结论来美化报告；原文quote由程序按有效ID和行号提取。行号真实不代表推理必然正确。

report.md首先按图列出问题：主要错误位置、相关需求、要求、模型实际、反例路径、修改建议和原文；再单列建模质量建议、两维需求结论、职责外未核查项、技术失败和未完成范围。质量建议不计入业务冲突。

report.json包括checkpoints、checkpoint_results、candidate_links、classified_links、links、tasks、passes、issues、quality_notes、check_scope、call_records和provenance。classified_links保留yes/no/uncertain与理由；score只属于初筛，不是概率。被排除的图不冒称已经做了一致性检查。

直接路径不产生候选或分类决定，links中的unclassified只记录输入范围，不代表全关联。evidence_links才记录实际语义检查引用的模型证据；assessments.model_ids是证据涉及图，check_scope.model_ids是实际提供图。model_structure保留活动图辅助路径及未支持语法；advisory_errors记录未发布建议，不计业务错误。

`--export-csv`加issues.csv；`--debug`加旧式诊断附表。CSV默认不开，避免同一结果散成很多文件。原始问题即使在定向复核格式失败时也保留，状态为待复核；没有有效保留目标不能合并删除。

completed表示技术流程已完成，不是全部业务需求已被证明满足。partial表示有实际未完成或技术限制，已有问题照常保留。未发现关联、职责外、疑似遗漏、模型已正确这四种含义必须区分。旧报告保持原样。

## 以下为显式旧策略的2.2输出

默认 candidate_first 的 output_design_version 为2.2。主结果按问题定位图与行号，覆盖和语义一致性另作附表。

| 产物 | 内容 |
|---|---|
| report.md | 按图的问题、位置、差异、证据、建议和未完成范围 |
| issues.json / issues.csv | 每个独立业务问题及对应需求、图、期望、实际和反例路径 |
| coverage.md / assessments.csv | 覆盖与语义结论、适用检查点和职责外未核查部分 |
| comparison.md | 需求与模型证据对照 |
| links.csv / retrieval-audit.json | 初始候选、反向补回、实际范围 |
| scope-audit.json | 检查点职责与被过滤的观察 |
| call-records.json | 实际输入范围、耗时、用量、状态和 repair_of_call |
| execution-failures.json | 未解决失败、已纠正诊断和未完成阶段 |
| report.json | 输入快照、全部轮次、发现与诊断 |

覆盖标签：完全覆盖、部分覆盖、未发现覆盖、不适用。语义标签：一致、存在冲突、无法判断。两维独立，明确表达相反行为时可完全覆盖且存在冲突。

职责外表示未核查，不表示需求满足。混合需求只对适用检查点给出判断。未找到关联不等于确定遗漏，辅助元素不自动算多余，不要求每张图独立表达全部需求。

表达错误必须定位现有源码并引用需求和模型原文。疑似遗漏用附近真实结构作为建议补充位置，不虚构不存在元素的行号。未确定图时允许未定位，保留搜索范围。

程序逐字核对引文、行号、文件和本轮实际提供的图。多个有效证据片段可以共同覆盖定位区间，但其中每行都要有证据。来源真实不等于推理必然正确。

模型复核完成不等于人工确认。空问题数组不能自动清除旧问题，排除需要旧问题ID、理由和原文依据。失败不转成业务遗漏。局部结构问题最多纠正一次，保留原尝试和有效发现，未解决者仍为 partial。

completed 表示流程完成，不是模型正确性证明。partial/cancelled 保留已有有效发现。旧 full_context 策略不执行新程序职责过滤。
# 2026-11-24 复核完整性修正

复核保留或合并问题时，应在有效issues条目中列出origin_ids；合并后的条目未通过校验，旧问题不得移除。dismissed_issues区分action=false_positive和action=merge，后者还必须指向已接纳、保留全部来源的replacement_id；缺少明确操作的旧格式保守拒绝删除。跨图或不同问题类型不能借来源引用相互替换。引用相同非空来源集合、同图同类型的复核版本采用后一个有效版本，review_variant_ids保留版本追溯；没有明确同源关系的相似问题不自动合并，避免误删独立错误。文本语义与需求依据是否正确仍需模型及人工复核。
