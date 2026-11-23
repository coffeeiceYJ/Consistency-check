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
    "check_strategy": "candidate_first", "top_k": 2, "max_calls": 20,
    "reverse_batch_size": 4, "reverse_batch_chars": 24000, "validation_retries": 1,
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

候选检索、逐需求检查、反向审计补关联、必要补查、关联组整体检查、问题复核和原文证据校验。每轮先判断检查点是否属于当前图职责，职责外内容不当作遗漏。混合需求只对适用部分给出结论。

- report.md、issues.json、issues.csv：按图定位的问题清单。
- coverage.md、comparison.md、assessments.csv：需求覆盖与语义一致性附表。
- links.csv、retrieval-audit.json：初始候选、反向补回与检查范围。
- scope-audit.json：检查点职责、未核查内容和被过滤观察。
- call-records.json、execution-failures.json、report.json：实际调用、未解决失败、原始输入快照与全部诊断。

覆盖与语义一致性分别记录。未发现关联不等于确定遗漏，辅助元素不自动算多余，另一张正确图不能消除当前图的实际冲突。`completed` 表示流程完成，不是业务正确性的证明。复杂多图检查仍可能出现 partial，保留有效问题并单列未完成范围。职责分类、语义判断和检索收益仍需人工复核与进一步评估。

## 源码组织

- src/reqcheck/：Python 核心、提示词、输入和输出适配器。
- bridge/LissaBridge.java：调用原 LiSSA 相似检索组件的桥接。
- scripts/：启动、JDK 选择和依赖构建。
- vendor/lissa：按固定提交拉取的上游依赖。
- [方法与文件对应](docs/ARCHITECTURE.md)、[接口](INTERFACES.md)、[输出设计](docs/OUTPUT-DESIGN.md)、[参考与许可证边界](THIRD_PARTY.md)。

本仓库只发布必要源码和运行文档，不含本地测试 demo、测试报告、论文、课件、PPT、环境或缓存。LiSSA 仅复用部分组件，MCeT 只参考方法，不能把上游论文指标当作本项目指标。
