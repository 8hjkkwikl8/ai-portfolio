# PromptLens

离线优先的提示词版本管理与可复现评测。用内容哈希固定提示词版本，用数据 SHA256 固定输入，用运行清单固定模型参数，再输出可比较的指标与单文件 HTML 报告。

项目零第三方依赖，随附的 mock provider 是确定性规则模拟，只用于验证评测流水线，不代表真实模型质量。

## 解决什么问题

提示词改了之后，如果只说“感觉更好了”，就无法复盘、无法定位回归，也无法证明改动值得保留。PromptLens 把一次实验拆成可审计的对象：

```text
提示词文件 + 描述 → 内容哈希版本（不可变）
数据集 → SHA256
运行配置 → 模型、参数、超时、重试、并发
        → 逐条输出 + 指标 + 运行清单 + 对比报告
```

同一数据集上的两次运行可以直接比较 `exact_match`、`contains`、`regex` 的均值与差值，并按标签查看失败分布。

## 快速开始

```bash
python demo.py
```

无需安装任何依赖。命令会：

1. 注册随附的两个提示词版本；
2. 对 `examples/feedback.jsonl` 运行 `prompt_v1.txt` 与 `prompt_v2.txt`；
3. 生成 `artifacts/baseline.json`、`artifacts/candidate.json`；
4. 生成 `artifacts/comparison.json` 与 `artifacts/report.html`。

打开 `artifacts/report.html` 即可查看对比结果。示例中 `exact_match` 从 7.1% 提升到 92.9%，这是 mock provider 对格式约束的确定性反应，仅说明评测链路能区分两个版本，不是模型质量结论。

## CLI

```bash
# 注册不可变版本；名称 + 模板 + 描述共同参与内容哈希
python -m promptlens.cli register \
  --name feedback-classifier \
  --prompt-file examples/prompt_v1.txt \
  --description "基础分类指令与统一输出格式" \
  --registry .promptlens/registry

# 运行一次实验
python -m promptlens.cli run \
  --config examples/run_v1.json \
  --output artifacts/baseline.json \
  --report artifacts/baseline.html

# 比较两次同数据集运行
python -m promptlens.cli compare \
  artifacts/baseline.json artifacts/candidate.json \
  --output artifacts/comparison.json \
  --report artifacts/report.html
```

`register` 输出 `version_id`（`p_` + 内容 SHA256）。相同名称、模板与描述重复注册会得到同一版本；任何一项变化都会产生新版本，旧版本不被覆盖。

## 运行配置

```json
{
  "name": "反馈分类-v1",
  "prompt_name": "feedback-classifier",
  "prompt_description": "基础分类指令与统一输出格式",
  "prompt_file": "prompt_v1.txt",
  "dataset": "feedback.jsonl",
  "provider": "mock",
  "model": "offline-feedback-rules-v1",
  "model_params": {"temperature": 0, "max_tokens": 128, "seed": 42},
  "timeout_seconds": 2,
  "max_retries": 2,
  "retry_backoff_seconds": 0.05,
  "concurrency": 1,
  "pricing": {
    "input_per_1k_tokens": 0.00015,
    "output_per_1k_tokens": 0.0006,
    "currency": "USD",
    "label": "示例单价（非供应商报价）"
  },
  "output": "../artifacts/baseline.json",
  "report": "../artifacts/baseline.html"
}
```

相对路径以配置文件所在目录为基准。数据集为 JSONL，每行至少包含 `id`、`input`、`expected`，可选 `tags` 与 `pattern`。

`pricing` 可省略。配置后，`input_per_1k_tokens` 与 `output_per_1k_tokens` 必须是非负数，`currency` 默认 `USD`，`label` 用于标注单价来源；单价属于实验身份的一部分，修改单价会产生不同的 `run_id`。

## 成本估算（可选）

PromptLens 不内置任何供应商报价，成本只按调用方提供的单价计算：

```text
估算 token 数 = ceil(Unicode 字符数 / 4)      # 启发式，不是 tokenizer
单条成本     = 输入 token / 1000 × 输入单价
             + 输出 token / 1000 × 输出单价
```

每条记录写入 `cost_estimate`（`input`、`output`、`total`、`currency`、`is_estimate`、`method`），汇总写入 `estimated_total_cost`、`cost_currency`、`cost_is_estimate`，运行清单写入 `cost` 块并附说明；`compare` 额外输出基线与候选的成本差值。未配置 `pricing` 时以上字段为 `null`，报告显示“未配置”，不显示 0。

这是估算值，不是供应商账单：中文等多字节字符的 token 比例与模型 tokenizer 并不固定，四舍五入、缓存命中、批处理折扣也未计入。它适合比较同数据集上两个提示词版本的相对开销，不适合作为财务结算依据。

## 指标与错误处理

| 指标 | 口径 |
| --- | --- |
| `exact_match` | 去除首尾空白后完全一致记 1 |
| `contains` | 期望文本是输出子串记 1 |
| `regex` | 正则能搜索到输出记 1，使用 `MULTILINE`；未提供 `pattern` 时不进入分母 |
| 按标签汇总 | 在每个标签子集内重新计算成功/失败数与三项指标 |

单条样本失败会被隔离并记录错误，不会中断整批运行；失败输出在适用指标上记 0，仍留在分母中，避免“失败样本消失导致指标虚高”。408、429 与 5xx 按配置做有限指数退避；其余 4xx 视为永久错误，不重试。`model`、`messages` 是审计字段，不能被 `model_params` 覆盖，运行前和 provider 层都会拒绝。

## 接入真实模型（可选）

将配置中的 `provider` 改为 `openai_compatible`，并设置：

```bash
export OPENAI_BASE_URL="https://your-endpoint/v1"
export OPENAI_API_KEY="..."
export OPENAI_MODEL="your-model"
```

`model` 也可以由 `OPENAI_MODEL` 提供。真实调用会产生网络请求与费用，且结果受模型版本、温度和供应商实现影响；用于结论时应在独立留出集上重复实验，而不是复用示例数据。

## 设计取舍

**内容哈希而不是自增版本号。** 版本 ID 直接绑定提示词内容与描述，无法“悄悄修改同一版本”；代价是注册表只增不改，需要自行清理不再使用的版本。

**透明指标而不是 LLM judge。** 精确匹配、包含和正则便宜、确定、可解释；代价是它们对语义等价不敏感。项目不假装这些指标能代替人工评审。

**失败保留在分母。** 失败样本按 0 计，而不是从统计中剔除；这会拉低指标，但能暴露稳定性问题。

**默认串行。** `concurrency` 默认可设为 1，配合 mock provider 得到可复现结果；提高并发可加速真实 API 调用，但会引入顺序不确定性，复现哈希只在串行 mock 运行中标记为可复现。

**报告单文件、全转义。** 不依赖 CDN 或前端框架，所有动态文本经过 HTML 转义，报告可离线分发，也避免把模型输出当成可信 HTML。

## 产物

- `artifacts/baseline.json` / `candidate.json`：完整逐条输出、指标、运行清单。
- `artifacts/comparison.json`：两次运行的差值、标签对比、复现信息。
- `artifacts/report.html`：离线可看的对比报告，包含 mock 免责声明。
- `artifacts/registry/`：示例运行使用的不可变提示词版本。

## 测试

```bash
python -m unittest discover -s tests -q
```

35 项测试覆盖：版本不可变与内容哈希、模板变量校验、数据哈希、三项指标与标签汇总、失败隔离、超时与重试、永久 4xx 不重试、保留字段拒绝、成本估算与对比差值、定价非法值拒绝、HTML 转义与报告免责声明。

## 限制

- 示例 provider 是规则模拟，不理解自然语言，也不调用模型。
- 指标只衡量字面输出，不评估语义正确性、事实性或安全性。
- 数据集需要人工准备与标注；没有主动学习、采样或标注界面。
- 未实现多模型并行对比与在线服务化。
- 成本为字符启发式估算，不是 tokenizer 精确计数，也不代表供应商账单。
- 并发运行会降低可复现性；真实结论应固定模型版本并重复实验。
