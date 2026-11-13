# 贡献指南

感谢你愿意改进这个作品集。这里优先考虑真实、可复现、容易审查的贡献，而不是提交数量或大型框架迁移。

## 开始之前

- 新功能、跨项目改动或指标口径变化，请先开 Issue 说明问题、使用场景和验收标准；文档修正、小型缺陷修复可以直接提交 PR。
- 搜索现有 Issue 与 PR，避免重复工作。较大改动建议先完成最小复现或设计说明，再开始编码。
- 参与本仓库即表示同意遵守[行为准则](CODE_OF_CONDUCT.md)。

## 本地环境

要求 Python 3.13。三个离线演示仅依赖标准库；Web 服务与 API 测试需要 FastAPI、uvicorn 和 httpx。

```bash
python -m pip install -r requirements.txt
python verify_all.py
```

`verify_all.py` 会运行 117 项自动化测试和三个离线演示，并重新生成部分演示产物。提交前请检查 diff，只保留与本次改动相关的文件。

也可以只运行受影响的项目：

```bash
cd insightrag   && python demo.py --eval-output artifacts/evaluation.json
cd promptlens   && python demo.py
cd datachat_bi  && python demo.py
```

## 提交与 Pull Request

- 每个 PR 只解决一个清晰的问题，说明动机、实现方式、关键取舍和验证结果；关联 Issue 时使用 `Closes #编号`。
- 提交信息使用简洁的祈使描述，可带作用域，例如 `fix(insightrag): reject invalid citation offsets` 或 `docs: clarify evaluation limits`。
- 新增或修复行为必须附带相应测试；测试总数变化时，同步更新中英文 README、展示页、验证脚本和相关模板。
- 保持 GitHub Actions 的 Ubuntu 与 Windows 检查通过。不要通过删除测试、放宽断言或跳过检查换取通过。
- 不提交 `*.db`、`__pycache__`、`*.pyc`、本地查询审计日志、个人配置或其他生成文件。

## 工程与真实性边界

- 核心演示必须默认离线、无需 API Key；真实模型和外部服务只能作为可选配置，不得新增默认联网行为。
- 不把规则、哈希方法或 mock provider 包装成神经模型、通用智能或生产效果。示例数据指标只验证工程链路，修改指标时必须同时说明口径与限制。
- 把模型输出视为不可信输入：回答引用必须能回放到来源，候选 SQL 必须经过守护与只读执行，实验参数不能覆盖已审计请求。
- 不降低现有安全边界来换取功能或演示成功率。涉及 SQL 守护、引用校验、重试、审计或脱敏的改动，需要覆盖绕过、失败与敏感信息不落盘场景。
- 不提交真实 API Key、令牌、个人数据或企业内部内容。审计问法只保留必要的脱敏信息，SQL 与绑定参数原文不得进入日志。

## 文档与发布

用户可见行为、配置项或限制变化时，请同步更新对应子项目 README；跨项目变化还需更新中英文根 README。文档中的命令、链接和产物路径应当实际可执行或可访问。

本项目采用 [MIT License](LICENSE)。提交贡献即表示你有权提交相关内容，并同意按该许可证提供。
