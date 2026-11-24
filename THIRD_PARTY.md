# 参考项目与依赖

## LiSSA

仓库：https://github.com/ardoco/lissa

固定版本：`b2fad29ec2cd83af2d2dc0de665869469143650a`，通过 `scripts/setup_lissa.py` 拉取到 `vendor/lissa`。LiSSA 自身使用 MIT 许可证，详见上游目录中的 LICENSE。构建产生的依赖和第三方许可证遵循各上游项目声明。

本项目的Java桥接调用LiSSA的Element、TargetElementStore、CosineSimilarity和SimpleClassifier。默认向量由本项目本地TF-IDF生成。SimpleClassifier使用其可配置模板，通过本机回环地址的OpenAI兼容网关接入本项目的计量模型客户端；实际关联提示词、JSON到yes/no的适配、不确定结果保留由本项目实现。远程密钥不交给Java，回环认证使用本次随机令牌。不是原LiSSA完整预处理、嵌入、RAG配置和论文数据实验的完整复现；原分类器代码接通不代表原论文效果得到复现。

## MCeT 与课程建模理论

MCeT：https://github.com/Huawei-TTE/MCeT ，论文：https://arxiv.org/abs/2508.00630 。借鉴整体、逐需求、从模型反向检查及复核的思路，未复制其源码，也不作为运行依赖。原项目主要面向顺序图。新版将多个观察角度放进同一主任务，只对实际信号追加补查或复核，没有照搬其固定多轮检查及投票，不能把本项目称为MCeT复现。

prompt_profiles.py 保留课程资料名称作为建模解释的来源标签，未上传原始课件、笔记或论文。课程风格建议不能自动变成业务需求。

本仓库尚未指定本项目源码的独立许可证，公开代码不意味着默认授权无限制复用。LiSSA 的许可证独立适用于其代码。
