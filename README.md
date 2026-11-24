# 需求与 PlantUML 一致性检查

输入需求文本和 PlantUML 模型，检查模型是否正确、完整地表达需求，输出具体图、源码行号、差异、证据和修改建议。核心独立于 runtime，当前以 Python API 和 CLI 运行。

支持用例图、类图、活动图、状态图、顺序图与系统顺序图的读取和提示词配置，不代表这些图的业务检出效果已经充分验证。当前主要由大模型阅读完整源码，保留来源位置并进行严格引文校验。

## 安装

需要 Python 3.12+、JDK 21+（包含 javac）、Maven 3.9+。Python 核心不依赖第三方运行库。LiSSA 作为固定版本上游依赖，不上传其构建缓存或整个复制目录。

```powershell
git clone https://github.com/coffeeiceYJ/Consistency-check.git
cd Consistency-check
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
# JAVA_HOME 指向本机 JDK 21，mvn 在 PATH 中
python scripts/setup_lissa.py
powershell -ExecutionPolicy Bypass -File scripts/build_lissa.ps1
```

已有 checkout 可执行 `python scripts/setup_lissa.py`。构建脚本默认跳过上游测试，只完成依赖打包，不代表重新验证上游论文实验。

其他系统可执行 `mvn -f vendor/lissa/pom.xml -DskipTests package`，然后使用安装后的 `reqcheck` 命令。桥接默认读取 `vendor/lissa/target/lissa-1.1.0-SNAPSHOT-jar-with-dependencies.jar`，也可通过 `LISSA_JAR` 或 `--lissa-jar` 指定现有 JAR。Java 路径可用 `JAVA_HOME` 或 `--java-home` 指定。

## 提交自己的输入

需求文件每条一个段落，保留共同条件。启用 preserve_ids 后每段必须以 `REQ-001：` 这样的编号开头，不放无编号标题。模型文件使用完整 `@startuml` / `@enduml` 图块。

在自己的材料目录创建 job.json，路径相对此清单：

```json
{
  "schema_version": "1.0",
  "requirements": [{"path": "requirements.md", "preserve_ids": true}],
  "models": [{"path": "model.puml", "id": "MODEL", "diagram_type": "activity", "responsibility": "填写本图承担的业务场景"}],
  "rules": [],
  "options": {
    "check_strategy": "task_based", "routing_mode": "auto", "max_calls": 100,
    "task_requirements": 6, "task_source_chars": 30000, "validation_retries": 1,
    "max_output_tokens": 12000, "model_timeout_seconds": 180, "model_thinking": "disabled"
  }
}
```

diagram_type 可选 `usecase`、`class`、`activity`、`state`、`sequence`、`system_sequence`、`unknown`。Word、PDF 需先提取成带来源位置的文本，当前文件适配器直接读取 txt/md/puml。

## 调用真实模型

将服务密钥配置在进程环境变量 `DEEPSEEK_API_KEY`，不要写入代码、清单或仓库。Windows 包装脚本在变量未配置时可隐藏输入密钥：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_live.ps1 -InputManifest C:/my-input/job.json -OutputDirectory outputs/my-run
```

也可以直接指定兼容 chat/completions 的服务和模型：

```powershell
reqcheck --input C:/my-input/job.json --output outputs/my-run --mode live --endpoint https://api.deepseek.com/chat/completions --model deepseek-flash --key-env DEEPSEEK_API_KEY
```

每次使用新的输出目录，程序不会覆盖现有报告。`model_thinking` 只对支持此开关的适配器生效。服务地址、模型名、用量和返回结构取决于所选服务，必要时通过选项调整。

`--mode rules` 仅检索和检查清单中的明确规则，不调用大模型，也不能代替自动自然语言语义检查。退出码：0=完成且无冲突，1=完成且存在冲突，2=输入/启动失败，3=部分完成或取消。

## 方法与输出

默认流程：一次整理稳定需求检查点 → 双向初筛候选 → 关联分类 → 组成有大小限制的相关需求与整图任务 → 主检查 → 按需补充上下文或纠正/复核 → 原文定位与输出。主检查在一次任务内联合核对需求、模型路径及多图表达，不再固定追加全量反向扫描和整体扫描。详见[架构与方法对应](docs/ARCHITECTURE.md)。

auto会在完整材料能放进一个任务（默认最多6条需求、30000个源字符）时跳过检索分类，直接联合检查。较大输入或显式classified才执行筛选链条。这是避免小材料无谓分类开销的预算规则，尚未证明是大项目的最优调度。两种执行路径及选择原因都进入报告。

- report.md：用户阅读的问题清单，包含图、行号、差异、建议；建模建议和未完成范围另列。
- report.json：同次检查的结构化结果及完整审计，包括输入快照、稳定检查点、初筛候选、分类决定、任务、调用和失败。
- `--export-csv` / PowerShell `-ExportCsv`：额外导出issues.csv。
- `--debug` / `-DebugArtifacts`：按需导出诊断附表。默认只生成前两个文件。

LiSSA复用范围现在包括原始CosineSimilarity和SimpleClassifier，分别由两个Java桥接调用。候选向量仍是本项目本地TF-IDF；分类提示词、计量传输、uncertain保留、多图检查和证据校验由本项目实现，没有复现原论文整个RAG实验。分类会增加模型调用，材料少时可能比直接联合检查更贵，不能承诺节省。默认双向初筛上限各20，仅控制待分类候选；不等于每条需求只能关联20张图，更不等于相关性真值。必须用实际数据评估候选漏检。

默认缓存已通过结构/来源校验的回答；源内容、行号、任务提示词或模型配置变更会使对应调用重新执行。语义错误也可能通过结构校验并被缓存，修正后应使用 `--no-cache` / `-NoCache` 重新验证。旧清单若显式写了candidate_first，仍走旧流程；迁移时改为task_based或传 `--strategy task_based`。规则模式继续独立保留；任务式流程收到显式旧规则会报未执行，不会默默忽略。

活动图主检查另携带程序生成的顺序/if-else完整路径，确保分支前共同动作不会被忽略；不支持的语法明确记录，保留原图，不宣称完整解析。直接路径不输出虚构的全关联；report.json中evidence_links表示实际引用的模型证据，check_scope表示提供范围。

使用自己的材料比较两条路线时，每次付费运行使用新的输出目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_live.ps1 -InputManifest C:/my-input/job.json -OutputDirectory outputs/classified-run -Routing classified -NoCache
powershell -ExecutionPolicy Bypass -File scripts/check_live.ps1 -InputManifest C:/my-input/job.json -OutputDirectory outputs/all-run -Routing all -NoCache
```

比较时保持输入与模型配置相同，把全部阶段计入成本。本地演示用例及开发用比较脚本不随本次源码发布。

覆盖与语义一致性分别记录。未发现关联不等于确定遗漏，辅助元素不自动算多余，另一张正确图不能消除当前图的实际冲突。`completed` 表示流程完成，不是业务正确性的证明。复杂多图检查仍可能出现 partial，保留有效问题并单列未完成范围。职责分类、语义判断和检索收益仍需人工复核与进一步评估。

## 源码组织

- src/reqcheck/：Python 核心、提示词、输入和输出适配器。
- bridge/LissaBridge.java、LissaClassifierBridge.java：调用原LiSSA候选检索及分类器的桥接。
- scripts/：启动、JDK 选择和依赖构建。
- vendor/lissa：按固定提交拉取的上游依赖。
- [方法与文件对应](docs/ARCHITECTURE.md)、[接口](INTERFACES.md)、[输出设计](docs/OUTPUT-DESIGN.md)、[参考与许可证边界](THIRD_PARTY.md)。


