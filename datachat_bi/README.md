# DataChat BI

自然语言到安全 SQL 的只读电商分析演示。中文 Web UI、内联 SVG 图表，以及一套从词法预检到 SQLite authorizer 的多层安全边界。

离线规则规划器只处理明确问法，不冒充任意自然语言理解；LLM 模式也只负责生成候选 SQL，候选仍必须通过同一套守护与只读执行层。

## 解决什么问题

让模型写一条 SQL 很容易，难的是保证它**只能读、只能碰允许的表、不能绕过行数限制**，并且不会把用户没说的筛选条件悄悄丢掉。DataChat BI 把“生成”和“执行”彻底分开：

```text
自然语言 → 规则 / LLM 候选 SQL
        → 词法与结构预检（单 SELECT、白名单、参数绑定、LIMIT）
        → SQLite 只读连接 + query_only + authorizer
        → 进度处理器超时 + 行数上限
        → 表格 / 内联 SVG 图表
```

预检负责尽早给出稳定错误，authorizer 才是最终执行边界；即使预检被绕过，写操作和未授权表仍会被 SQLite 拒绝。

## 快速开始

```bash
python -m pip install -r requirements.txt

# 重建 48 行演示库，运行 7 类问题并生成图表报告
python demo.py

# 启动中文 Web UI 与 API
python -m app.api --port 8002
# 打开 http://127.0.0.1:8002
```

`demo.py` 会生成 `datachat_report.html`，其中包含结果表格和 6 幅内联 SVG 图表，可直接离线打开。

## 支持的离线问法

规则规划器覆盖以下 7 类问题及其列出的别名：

| 问题 | 结果 |
| --- | --- |
| 总销售额是多少？ | 总销售额 612,360.0 |
| 按类别销售额排行 | 各类别销售额降序 |
| 按月销售趋势 | 月度销售额折线图 |
| 按地区销售额 | 各地区销售额柱状图 |
| 销量最高的商品类别是什么？ | 智能家居 |
| 哪个渠道销量最高？ | 天猫 |
| 各渠道销售额对比 | 各渠道销售额柱状图 |

可以附加**一个**年份，例如“2024 年总销售额”，年份会作为绑定参数传入。出现未消费的额外条件（如“仅看华东”“只看线上渠道”）、多个年份或无法完整匹配的问法时，规划器会明确报错，而不是生成一条忽略条件的 SQL。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 服务状态、订单数、当前规划模式 |
| `GET` | `/api/schema` | 表白名单、列类型、行数上限与超时 |
| `POST` | `/api/query` | `{"question": "总销售额是多少？"}`，返回 SQL、结果、图表与模式 |
| `GET` | `/` | 中文 Web UI |

错误会区分规划失败、SQL 守护拒绝、执行失败与超时，并返回可展示的中文原因；不会泄露数据库路径、SQL 原文或底层异常。

## 本地查询审计

API 查询默认追加写入 `artifacts/query_audit.jsonl`。每条事件包含 UTC 时间、问法、规划模式、候选 SQL 与最终守护 SQL 的 SHA256、守护结果、执行状态、返回行数、耗时和异常类型；成功、规划拒绝、守护拒绝、执行错误与超时都会记录。

日志采用字段白名单，不记录 SQL 原文、绑定参数、数据库路径、环境变量或异常原文。问法会保留以便复盘，但常见的 `API_KEY=...`、Bearer 和 API Key 形式会被替换为 `<redacted>`；仍请勿在问法中输入密码、密钥或个人敏感信息。

```bash
# 自定义日志路径
export DATACHAT_AUDIT_LOG="/local/path/query_audit.jsonl"
python -m app.api --port 8002

# 也可使用启动参数，或显式关闭审计
python -m app.api --audit-log "/local/path/query_audit.jsonl" --port 8002
python -m app.api --no-audit-log --port 8002
```

健康接口只暴露 `audit_log: enabled/disabled`，不返回本机路径。默认日志已被 `.gitignore` 排除。写入失败会发出告警而不改变只读查询结果；该实现是进程内串行、可删除的本地 JSONL，不是防篡改、集中式或生产级审计系统。

## 安全边界

| 层 | 机制 |
| --- | --- |
| 词法与结构 | 只允许单条 `SELECT`/`WITH`；拒绝注释、多语句、控制字符、未闭合引号 |
| 关键字 | 拒绝 `INSERT`、`UPDATE`、`DELETE`、`DROP`、`ATTACH`、`PRAGMA` 等写操作与危险语句 |
| 对象白名单 | 仅允许 `orders` 表与已声明列；函数采用白名单，未列出的函数直接拒绝 |
| 参数 | 仅接受位置参数序列，值通过绑定传入；禁止字典参数与字符串拼接 |
| 行数 | 自动追加 `LIMIT`；已有 `LIMIT` 超过上限时收紧，拒绝逗号分页语法 |
| 连接 | SQLite `mode=ro` 只读 URI + `PRAGMA query_only` |
| 执行授权 | `set_authorizer` 按表、列、函数、动作逐项授权 |
| 超时 | `set_progress_handler` 按墙钟预算中断长查询 |
| 返回 | 行数上限 200，超时默认 2 秒，均可配置 |

## 设计取舍

**精确问法路由而不是宽泛正则。** 离线模式宁可拒绝，也不把“仅看华东”静默当成“按地区销售额”。代价是覆盖问法有限，但每条生成的 SQL 都能解释。

**两层防护而不是只做 SQL 字符串检查。** 预检负责可读错误与结构约束，authorizer 负责最终授权；不把正则当作唯一安全边界。

**SQLite 而不是数据仓库。** 单文件、零服务、易复现，适合演示安全边界；不代表生产环境的权限模型、查询成本或并发能力。

**内联 SVG 而不是图表库。** 报告与页面离线可开，无 CDN 依赖；代价是图表类型有限，只覆盖柱状图与折线图。

**LLM 只生成候选。** 开启 LLM 模式后，模型输出仍要经过参数绑定、白名单、LIMIT 与 authorizer；模型不能直接执行 SQL。

## 数据与图表

`examples/orders.csv` 包含 48 行确定性订单，字段为 `order_id`、`order_date`、`category`、`region`、`channel`、`quantity`、`revenue`。`examples/generate_data.py` 可重新生成数据，`demo.py` 每次从 CSV 重建数据库，保证结果可复现。

报告中的 6 幅 SVG 图表由查询结果直接生成，动态文本经过转义，避免把数据内容当作 HTML 注入。

## 接入 LLM 规划器（可选）

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="your-model"
export OPENAI_BASE_URL="https://your-endpoint/v1"   # 可选
python -m app.api --port 8002
```

检测到 `OPENAI_API_KEY` 与 `OPENAI_MODEL` 后启用 LLM 规划器；候选 SQL 仍统一经过 `SQLGuard` 与只读执行层。模型不可用或输出不合法时会返回明确错误，不会降级为执行未校验 SQL。

## 测试

```bash
python -m unittest discover -s tests -q
```

53 项测试覆盖：SQL 守护绕过、注释与多语句拒绝、表白名单、列校验、函数白名单、参数绑定、LIMIT 收紧、authorizer、查询超时、只读连接、规则问法完整匹配、额外条件与多年份拒绝、SVG 转义、API 契约，以及审计字段、SQL 哈希、密钥与参数脱敏、失败事件记录。

## 限制

- 仅支持单表 `orders` 的演示 schema，不含 JOIN 多表业务模型。
- 离线规划器只覆盖明确问法；复杂分析需要 LLM 模式和人工复核。
- 无账号、权限与多租户隔离，仅适合本机演示；审计日志是可删除的本地 JSONL，不具备防篡改、集中检索或生产级告警能力。
- SQLite 的并发与查询能力有限，不代表生产数据仓库。
- 图表只支持柱状图与折线图，不包含交互式钻取。
