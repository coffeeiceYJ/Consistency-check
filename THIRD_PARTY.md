# 参考项目与依赖

## LiSSA

仓库：https://github.com/ardoco/lissa

固定版本：`b2fad29ec2cd83af2d2dc0de665869469143650a`，通过 `scripts/setup_lissa.py` 拉取到 `vendor/lissa`。LiSSA 自身使用 MIT 许可证，详见上游目录中的 LICENSE。构建产生的依赖和第三方许可证遵循各上游项目声明。

本项目的 Java 桥接调用 LiSSA 的 Element、TargetElementStore 和 CosineSimilarity。默认向量由本项目本地 TF-IDF 生成，没有采用其完整 RAG/分类流程，检索结果不代表需求满足。

## MCeT 与课程建模理论

MCeT：https://github.com/Huawei-TTE/MCeT ，论文：https://arxiv.org/abs/2508.00630 。借鉴整体、逐需求、从模型反向检查及复核的思路，未将其源码复制到本仓库，也不作为运行依赖。原项目主要面向顺序图，本项目的多图提示词和调度另行实现。

prompt_profiles.py 保留课程资料名称作为建模解释的来源标签，未上传原始课件、笔记或论文。课程风格建议不能自动变成业务需求。

本仓库尚未指定本项目源码的独立许可证，公开代码不意味着默认授权无限制复用。LiSSA 的许可证独立适用于其代码。
