# 核心接口

入口：pipeline.check(request, retriever, client=None, progress=None, cancelled=None) 返回结果字典，不绑定 runtime、UI 或凭证存储。

- contracts.Artifact：id、text、file、start_line、end_line、diagram_type、responsibility。
- contracts.CheckRequest：requirements、models、rules、options、run_id。
- Retriever.retrieve(request)：返回 requirement_id、model_id、score、method、status 的候选列表。
- ModelClient.complete(system, payload)：返回 JSON 对象，具体约束见 prompts.py。
- progress(event) 接收阶段事件，cancelled() 返回是否取消。
- inputs.load_request(manifest) 读取 txt/md/puml 清单，路径相对清单。
- reporting.write_report(report, directory) 写入新目录，不覆盖现有报告。

反向审计通过 model_audits 为每张图独立返回 related_requirement_ids、assessments、issues、dismissed_issues。每张图独立核对证据，整体和复核阶段才联合阅读相关图。

默认 candidate_first 中，assessment 带 checks、coverage_scope、unchecked_dimensions。检查点包含 id、dimension、description、applicability、reason、verdict、missing_content。issue 用 check_ids 关联本轮检查点。无效维度或引用不能直接当作一致。

顶层结果含 run_status、links、assessments、issues、check_scope、scope_decisions、scope_exclusions、execution_failures、resolved_validation_failures、call_records、inputs、provenance。输出文件见 docs/OUTPUT-DESIGN.md。

选项包括 top_k、max_calls、max_context_chars、reverse_batch_size、reverse_batch_chars、validation_retries、max_output_tokens、model_timeout_seconds、model_thinking。validation_retries 只允许0或1，纠正计入调用预算。服务地址、模型和密钥变量名由客户端/CLI传入，密钥值不进入报告。

后续 runtime 将材料转换为 Artifact/CheckRequest，注入检索器、模型客户端、进度和取消回调，再消费同一结果，不需要改业务检查核心。
