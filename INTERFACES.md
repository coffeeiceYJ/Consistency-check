# 核心接口

入口：pipeline.check(request, retriever, client=None, progress=None, cancelled=None, relation_classifier=None, cache_dir=None)。不绑定runtime、UI或凭证存储。

- Artifact：id、text、file、start_line、end_line、diagram_type、responsibility。
- CheckRequest：requirements、models、rules、options、run_id。
- Retriever.retrieve(request)：返回requirement_id、model_id、score等初筛候选，不是满足关系。
- RelationClassifier.classify(pairs, runner)：返回requirement_id、model_id、decision(yes/no/uncertain)、reason、method；注入测试后端须明确标为fixture。
- ModelClient.complete(system, payload)：返回JSON对象。任务式约束在task_prompts.py，旧策略在prompts.py。
- progress(event)：接收阶段事件；cancelled()返回取消状态。
- inputs.load_request(manifest)：读取txt/md/puml，路径相对清单；Word/PDF需要前置转换。
- reporting.write_report(report, directory)：写入新目录，不覆盖原结果。

task_based为默认策略。routing_mode默认auto：完整材料符合单任务预算时all，否则classified。显式classified/all用于控制执行和对照，报告requested_routing、routing_mode与routing_reason。旧candidate_first/full_context必须显式指定。

稳定checkpoints包含id、requirement_id、text、dimension、evidence。每个checkpoint_result引用check_id；issue通过check_ids关联需求，requirement_ids由程序从检查点推导，不能任意填写。业务expected从这些检查点生成，语义是否忠实仍须复核。

主检查结果results包含coverage、consistency、summary、model_ids、evidence、needs_context。issues定位具体图和行号；quality_notes独立存放建模建议。模型只返回artifact_id/start_line/end_line引用，由程序提取原文quote。

model_structure记录活动图顺序/if-else子集的完整路径及辅助解析限制，传入主检查作控制流上下文，不是自动业务验证器。evidence_links记录检查点实际引用的模型证据；links仅表示路由提供范围。直接路径的links使用unclassified，candidate_links/ classified_links为空，不把“提供全部材料”伪装为“全部关联”。advisory_errors单列被拒绝的可选建模建议。

顶层结果包括issues、quality_notes、assessments、checkpoints、checkpoint_results、candidate_links、classified_links、links、tasks、passes、check_scope、scope_decisions、scope_exclusions、execution_failures、call_records、inputs、options和provenance。默认文件为report.md、report.json，CSV和诊断附表按需开启。

主要选项：
- max_calls默认100，max_context_chars默认100000；分类预留主检查调用预算，耗尽不能声称完成。
- task_requirements默认6，task_source_chars默认30000；完整图超预算不截断。
- candidate_top_k/reverse_top_k默认20；只控制初筛，分类结果不是top_k硬选。
- preparation_batch_size默认12；validation_retries只能0或1（默认1）。
- expand_missing_context默认true，max_expansion_models默认8；仅触发一次，不全量重复扫描。
- review_policy为signals（默认）/all_findings/none；signals仅处理同图重叠或重复检查点。
- max_output_tokens、model_timeout_seconds、model_thinking由客户端处理。
- debug_artifacts、export_csv控制附加文件。

cache_dir传None关闭缓存，CLI默认使用项目.reqcheck-cache。密钥由ModelClient接收并在进程内使用，不进入报告、Java参数或缓存；Java本机网关使用本次随机令牌。

任务式流程不执行旧显式活动规则，收到rules时会列为未执行、返回partial；rules模式可独立验证有限活动图规则。runtime将材料转为Artifact/CheckRequest、注入客户端及回调，再消费同一报告即可。
