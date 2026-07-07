# Lychee AlphaDesk 开发规格

版本：v0.1 草案

[English](DEVELOPMENT_SPEC.md) | [简体中文](DEVELOPMENT_SPEC.zh-CN.md)

## 1. 产品方向

Lychee AlphaDesk 是一个终端原生的 AI 投资研究工作台。

主产品不是 Web dashboard，而是一个快速、本地运行的 CLI/TUI 应用。Demo 模式不需要部署服务，不需要券商账户，也不需要付费 API key。

第一期实现必须证明这个工作流成立：

```text
投资政策 -> provider -> 市场发现 -> 数据质量 -> 每日报告 -> TUI 查看 -> 审计日志
```

用户 clone 仓库后，应该能在五分钟内看到项目价值。

## 2. 第一期目标

v0.1 应交付：

- 可安装 Python package。
- `lad` 命令。
- 内置 demo 模式。
- 投资政策文件校验。
- Provider 接口。
- Demo providers。
- 数据质量检查。
- 统一数据快照命令。
- Provider 健康检查命令。
- 用于本机 provider 配置的 CLI setup 命令。
- 覆盖美股、港股和 A 股的发现优先工作流。
- Markdown 每日报告。
- 本地审计日志。
- 最小 Textual TUI 外壳。

v0.1 不交付：

- Web 前端。
- FastAPI 服务。
- 实盘交易。
- 券商账户要求。
- 付费数据源要求。
- Demo 模式的真实 LLM 强依赖。
- 真实 TimesFM 强依赖。

## 3. 技术栈

| 层 | 决策 |
| --- | --- |
| 语言 | Python 3.11+ |
| 包管理 | uv |
| CLI | Typer |
| 终端 UI | Textual + Rich |
| 数据模型和配置 | Pydantic v2 + YAML |
| 表格和终端渲染 | Rich |
| 报告 | Markdown + Jinja2 |
| 存储 | JSON 存可读快照，SQLite 存审计记录和研究队列，后续 Parquet 存时间序列 |
| 测试 | pytest |
| 格式化和 lint | ruff |
| 类型检查 | mypy |
| 文档 | Markdown 优先，后续 MkDocs Material |

## 4. 包结构

```text
src/lychee_alphadesk/
  __init__.py
  cli/
    app.py
  tui/
    app.py
    screens/
  core/
    policy/
    providers/
    data_quality/
    reports/
    audit/
    portfolio/
  providers/
    demo/
    csv/
  templates/
examples/
  demo/
    alphadesk.yaml
    policy.yaml
    portfolio.csv
    market_data.csv
    news.jsonl
    filings.jsonl
docs/
tests/
```

## 5. CLI 命令

v0.1 必须支持：

```bash
lad demo
lad setup
lad setup set alpha_vantage YOUR_API_KEY
lad setup llm set https://api.example.com/v1 YOUR_API_KEY MODEL_NAME
lychee setup
lychee discover today
lad discover today --markets us,hk,cn
lad data health --demo
lad data snapshot --demo
lad data pull market --symbols AAPL,TSLA
lad data pull news
lad data pull news --symbols AAPL --provider auto
lad data pull news --symbols AAPL --provider auto --force
lad data pull filings --symbols AAPL,TSLA --limit 3
lad data set fund --symbol 2800.HK --name 盈富基金 --source-url https://example.com/2800 --tracking-index "Hang Seng Index" --expense-ratio "0.10%"
lad data freshness
lad data health
lad data snapshot
lad report --demo
lad policy check examples/demo/policy.yaml
lad research queue
lad audit list
lad
```

命令行为：

- `lad` 打开 TUI，主界面第一个动作必须是 `今日市场发现`，第二个动作必须是 `研究工作台`，然后是 `待判定证据队列`、研究复核历史、单条证据复核历史、研究备忘录历史，再进入手动股票代码钻取、数据健康检查、provider setup 指引、snapshot 和退出。
- `lad demo` 检查 demo 文件和本地输出目录。
- `lad setup` 打开统一交互式配置中心，数据 provider 和 LLM provider 都从这里配置。
- `lad setup set` 为自动化脚本和 agent 单项写入一个 provider key 或 token。
- `lad setup llm set` 为自动化脚本和 agent 单项写入一个 OpenAI-compatible LLM `base_url`、API key 和可选模型名。
- 交互式配置中心在 TTY 环境使用 Textual `OptionList` 控件和键盘导航。面向人的菜单必须使用 ↑/↓/←/→/Tab 移动选择，Enter 确认，Esc 返回或退出。菜单不得使用数字或字母作为选项选择操作，并且这条流程不得继续使用手写 raw-key parser。非 TTY 环境不得 fallback 到文本菜单，应使用非交互式 setup 命令。provider 菜单只显示展示名称和脱敏配置状态；进入 provider 后再显示注册链接和面向用户的配置说明。隐藏输入提交后会用 `✅` 或 `❌` 告诉用户是否收到内容。
- LLM 配置区会写入自定义 OpenAI-compatible `base_url`、API key 和模型名。它会先读取 `{base_url}/models`；如果接口不可用或没有返回可用模型 ID，就提示用户手动输入模型名。
- `lychee` 是推荐的 console command；`lad` 保留为短别名。
- `lad data health --demo` 打印 provider 级数据质量检查。
- `lad data snapshot --demo` 写入统一 JSON 快照，包含市场、新闻、公告和预测数据。
- `lychee discover today` 在不要求用户先输入股票代码的情况下，运行覆盖美股、港股和 A 股的发现优先流程。
- `lad discover today --markets us,hk,cn` 会先检查/拉取市场级新闻 cache，再以 `stream: true` 调用已配置的 OpenAI-compatible `/chat/completions` 接口，解析模型返回的 JSON，并写入本地 `llm-synthesized` discovery report cache，包含主题、关注候选、证据引用、warning 和下一步动作。如果没有可用新闻 provider、没有配置 LLM provider、API 请求失败，或模型没有返回有效 JSON，命令必须失败；不允许静默生成 fallback 报告。成功后必须同步写入 `.alphadesk/research.sqlite3`，作为研究队列和证据追踪的本地数据库。默认 LLM 读超时为 180 秒。
- `lad data pull market` 将 Alpha Vantage 日线行情写入本地 live cache。默认使用行情 cache 的保质期和交易时段判断；`--force` 可强制刷新。
- `lad data pull news` 将 Marketaux、Finnhub 或 NewsAPI 新闻事件写入本地 live cache。不传 `--symbols` 时拉取市场级新闻，传入 `--symbols` 时拉取个股新闻。`--query` 可传入主题关键词，用于按研究主题补强新闻证据；主题查询应使用 Marketaux 或 NewsAPI，Finnhub 不支持主题关键词查询。默认使用新闻 cache 保质期；`--force` 可强制刷新。新闻缓存必须保留已有行并追加去重后的新行，避免刷新后改变 `news_001` 等 evidence ID 的含义。Finnhub 当前仅用于个股新闻；市场级新闻应使用 Marketaux 或 NewsAPI。
- `lad data pull filings` 将 SEC EDGAR 近期 filings 写入本地 live cache。
- `lad data set fund` 将人工核验且带来源 URL 的基金/ETF 资料写入 `fund-metadata.json`，用于代理 ETF/指数核验里的跟踪指数、费用率、成分摘要、来源和资料日期。该命令必须要求来源 URL，不得把会漂移的基金费用或成分硬编码成生产事实。
- `lad data freshness` 只读取本地 `cache_entries`，展示缓存层级、状态、provider、cache key、市场、交易状态、过期时间和行数，不触发 provider 请求。
- `lad data health` 检查 live cache 是否存在以及行数状态。
- `lad data snapshot` 基于 live cache 写入统一 JSON 快照。
- TUI 主界面 Action 菜单必须先暴露发现优先流程、研究工作台、待判定证据队列、研究复核历史、证据复核历史和研究备忘录历史，再暴露手动 symbol 流程。`研究工作台` 动作必须运行工作台自检并展示可选择的研究任务列表；每个任务项必须展示入口、优先级、证据状态和排序理由，解释为什么该任务排在当前顺序。`待判定证据队列` 动作必须读取每个研究任务最新的下钻核验 artifact，过滤已经被 `research_evidence_reviews` 覆盖的证据，把待判定新闻展示为可选择行；选中后必须进入详情页，展示研究问题、复核命令和来源 artifact，并可在不离开队列流程的情况下记录为支持、风险/反向待查或无关/排除；从主界面队列完成单条证据复核后，TUI 必须继续提供同一任务的 `重新下钻核验` 动作，让用户立刻查看更新后的证据板；`研究复核历史` 动作必须读取 SQLite 复核记录并展示复核判断、备注、证据数量和 artifact 路径；`证据复核历史` 动作必须读取 SQLite 单条证据复核记录并展示证据片段、方向标签、备注和 artifact 路径；`研究备忘录历史` 动作必须读取 SQLite 备忘录记录并展示摘要、置信度、支持/反方/待补/下一步计数和 artifact 路径。用户用 ↑/↓ 选择任务并按 Enter 后，必须进入“研究任务面板”，展示入口、优先级、排序理由、本次研究要解决的问题、研究启动步骤、证据状态、信号读数、证据矩阵、已采集证据、行情、相关新闻、公告/财报线索、数据缺口、下一步动作和可执行动作菜单。详情页动作菜单至少应覆盖刷新本任务行情、刷新本任务新闻、弱证据任务的刷新主题新闻、刷新适用的美股公告/财报、下钻核验并显示证据板、生成研究备忘录，以及返回研究任务列表。刷新类动作完成后，即使工作台重新排序，也必须按 symbol、代理 symbol 或名称回到同一个研究任务，并把重新下钻核验放在下一步菜单首位，再提供生成研究备忘录和返回研究任务列表。下钻核验结果页必须提供可选择的复核记录动作，用于写入继续研究、需要补证据、暂停观察或存在阻塞等研究流程判断；第一项必须是“按工作台建议记录”，使用研究决策板给出的 suggested verdict，其它判断保留为可手动覆盖选项。对新闻待判定行，也必须提供可选择的单条证据方向复核动作，用于标记为支持、风险/反向待查或无关/排除，并在记录后重新运行下钻核验，让结果页展示更新后的证据板。复核记录完成后不得停留在静态确认页：`needs_more_evidence` 必须继续提供刷新主题新闻和重新下钻核验动作，`continue_research` 必须继续提供生成研究备忘录和重新下钻核验动作。手动输入股票代码只作为已经知道关注对象后的钻取路径保留。Textual 内置 command palette 不是业务命令入口，并且应在主界面保持禁用，以避免终端 glyph 宽度显示问题。
- `lad report --demo` 使用内置 demo provider 生成 Markdown 日报。
- `lad policy check` 校验投资政策文件，并打印违反项或警告。
- `lad research queue` 列出 SQLite 研究库中的关注候选，包含状态、市场、代码、主题、证据数量和下一步动作数量。默认输出必须是去重后的当前活跃队列：有证券代码的候选按“市场 + 代码”保留最新一条；没有证券代码的候选按“市场 + 标准化名称”保留，并可对非常明确的同义主题做保守聚合，例如“中国 AI 数据中心供应链 / 产业链 / 链条”。历史 discovery run 可继续留在 SQLite 中，但不得直接堆叠到默认工作台任务列表里。
- `lad research deepen` 从研究队列生成二阶段研究深挖包，写入本地 SQLite `research_packets` 表和 `.alphadesk/research/research-packets-*.json`，包含候选身份、证据 ID、可展开证据、已缓存数据、数据缺口、代理映射和下一步核验动作。深挖层必须先从候选池生成深挖包，再优先展示无数据缺口、可继续研究的任务，避免默认工作台被阻塞项占满。相关新闻选择必须先按研究主题相关性排序，再按时间排序，避免最新但离题的 symbol 新闻挡住主题证据。ETF、基金和指数类任务的主题新闻还必须命中股票、指数、ETF、交易所、成交、流动性等金融市场语境，不能因为同城或泛技术关键词就进入相关新闻。面向用户的输出必须是工作台任务卡，而不是课件式说明；至少包含：研究问题、入口、优先级、排序理由、证据状态、关键核验、下一步队列。证据状态必须包含支持、反向、待判定和离题证据数量；只有反向、待判定或离题证据时，任务必须降级为先复核证据方向。
- `lad research fill-gaps` 根据研究队列和深挖包暴露的数据缺口，自动补齐可拉取的数据；第一版支持缺失行情、美股股票 SEC 公告，以及无 symbol 主题的可审计代理映射行情。行情补齐默认使用 `auto`，美股走 Alpha Vantage，港股/A 股走 Eastmoney 日 K，主数据源失败时使用 Yahoo chart 兜底。缺少 symbol 的候选不得被静默改写为某个代码；系统只能生成带原因、置信度和证据 ID 的代理标的，并要求用户人工确认后再下钻。
- `lad research check --strict` 是 agent 和 CI 可用的工作台自检入口，必须自动执行补缺、重新深挖、生成 `AlphaDesk 研究工作台` 和机器可读 `workbench-check-*.json`。工作台输出不得只显示代码、代理标的、泛泛结论或课堂式解释；必须展示可执行任务、阻塞任务、排序理由、证据状态和下一步队列。每个任务卡和下一步队列行必须包含可直接复制执行的 `lychee research ...` 命令；阻塞任务必须包含补数据执行链命令。机器可读 `workbench-check-*.json` 也必须把同一个 `next_command` 写入每个候选，供 agent 和 TUI 复用。工作台必须把证据方向核验反映回优先级和下一步动作：只有反向、待判定或离题新闻证据的候选不得显示为直接下钻任务，其主命令必须指向 `lad research run --force`，先刷新主题新闻并重新核验，而不是重复运行只读式下钻核验。严格模式下，只要证据、研究入口、代理行情或数据缺口 gate 失败，就必须以非零退出码结束。
- `lad research detail` 是单条研究任务的非交互式详情入口，必须复用 TUI 研究详情的 core 渲染逻辑，输出“研究任务面板”、研究状态、排序理由、本次研究要解决的问题、研究启动步骤、信号读数、证据矩阵、行情、相关新闻、公告/财报线索、数据缺口和可执行刷新命令。研究任务面板不得伪装成投资结论页；它必须告诉用户第一步运行哪条下钻核验命令、证据板看哪些栏目、以及如何用 `lad research review` 记录流程判断。研究状态只能表达待补数据、先复核证据、代理核验、继续补证据或可下钻研究，不得表达买入/卖出/仓位/目标价。不得在 CLI 和 TUI 中维护两套不同口径的研究详情。
- `lad research run` 是单条研究任务的数据刷新执行链，必须选择任务、刷新相关行情/新闻/适用的美股公告、重新运行工作台自检、输出更新后的研究详情，并写入 `research-run-*.json` 审计记录。若任务当前证据质量为 missing、needs_review 或 mixed，执行链必须根据研究主题生成主题新闻 query，并额外刷新一轮主题新闻。审计记录必须包含结构化 `assessment`，记录阶段、一致性核验状态、证据读数和下一步判断。该命令不得给出买卖建议，只能推进证据收集和研究状态更新。
- `lad research verify` 是单条研究任务的下钻核验入口，必须核验行情、成交量、新闻、公告/财报和代理标的状态，并写入 `research-verification-*.json`。有直接 symbol 的任务使用本地价格缓存；没有直接 symbol 但有代理 ETF/指数映射的任务，必须使用代理映射中的 `latest_price` 完成行情和成交量核验，并把代理行情写入支持证据，不得在代理行情已覆盖时误报缺少本地行情。代理 ETF/基金如果已有 source-backed `fund-metadata.json` 资料，核验必须把跟踪指数、费用、成分摘要和来源放入支持证据；如果资料缺失或字段不完整，只能按具体缺失字段进入待补证据，不得把已补资料继续显示成泛化缺口。核验结果只能表达通过、待核验、阻塞或不适用；同时必须生成“支持证据 / 风险或反向待查 / 离题或已过滤 / 待补证据”四栏证据板，并生成“证据变化”摘要、“证据变化明细”和“研究决策板”。当存在新闻待判定时，输出必须包含“待判定证据处理”区块，列出已按当前任务过滤的 `lad research pending-evidence` 队列命令、具体 `lad research evidence-review` 命令模板，以及分类后的 `lad research verify` 重新核验命令。当同一研究任务存在上一份 `research-verification-*.json` 时，系统必须比较支持证据、风险/反向待查、离题/已过滤和待补证据的数量与逐条文本，明确展示本次证据是增强、压力增加、混合变化还是未变化，并列出新增、移除和已补掉的证据行。研究决策板把证据状态翻译成继续研究、需要补证据、暂停观察或存在阻塞等研究流程判断，并且必须给出可复制执行的下一步命令，例如 `lad research run --force`、`lad research review --verdict ...` 或 `lad research memo`。新闻和 discovery 证据不得只按数量进入支持证据，必须先做主题相关性和证据方向核验；未命中研究任务关键词的 headline/summary 应进入离题/已过滤栏目，命中主题但带有下降、放缓、疲弱等反向信号的新闻应标为反向证据，命中主题但方向不明的新闻应标为新闻待判定。一致性结论必须保守为待人工核验，直到后续有专门的一致性分析引擎。研究决策板只能给出研究流程状态和下一步研究动作，不得给出买入、卖出、持有、仓位、目标价或收益预期。
- `lad research pending-evidence` 是待判定单条证据复核队列入口，必须读取每个研究任务最新的 `research-verification-*.json`，只收集 `新闻待判定` 行，跳过已经被 `research_evidence_reviews` 覆盖的行，并展示任务、研究问题、证据文本、来源 artifact、系统建议方向、建议理由和已填好 verdict 的 `lad research evidence-review` 命令。队列不得让初学者面对 `<support|reverse|irrelevant>` 这类原始占位符自行猜测。它必须支持 `--symbol` 和 `--name` 过滤，使 `lad research verify` 打印出的命令可以直接打开对应任务的待处理队列。它只能作为研究流程待办队列，不得把待判定证据解释成买卖候选清单。
- `lad research evidence-review` 是单条证据方向复核入口，必须把某条新闻标题或证据文本片段记录为 `support`、`reverse` 或 `irrelevant`，写入 `research_evidence_reviews` SQLite 表和 `research-evidence-review-*.json`。后续 `lad research verify` 必须读取这些复核记录，并将匹配证据重新归类到支持、风险/反向待查或无关/排除路径，使“新闻待判定”可以被审计式消化。单条证据复核记录完成后，CLI 必须继续打印工作台下一步命令，包括重新运行 `lad research verify`、继续处理已过滤的 `lad research pending-evidence` 队列，以及查看 `lad research evidence-reviews`。该命令只能记录证据方向和备注，不得表达买入、卖出、持有、仓位、目标价或收益预期。
- `lad research evidence-reviews` 是单条证据复核历史查看入口，必须从 SQLite `research_evidence_reviews` 表读取记录，展示已复核的证据片段、方向标签、备注和 review artifact。该命令用于审计证据分类过程，不得将历史证据复核解释成买卖清单。
- `lad research memo` 是单条研究任务的 LLM 二阶段研究备忘录入口，必须先运行同一套下钻核验，再把证据板、核验项、证据变化摘要和研究决策板交给已配置 LLM，生成不会同秒覆盖的 `research-memo-*.json`。备忘录只能包含摘要、证据读数、支持点、反方审查、待补证据和下一步研究动作；不得包含买入、卖出、持有、仓位、目标价、收益预期或交易指令。备忘录生成后，CLI 必须继承研究决策板的 suggested verdict 打印工作台下一步：弱证据任务应回到 `lad research run --force` 和 `lad research review --verdict needs_more_evidence`，证据较强时才进入人工一致性复核；同时仍需提供重新运行 `lad research verify` 和查看 `lad research memos` 历史，不能停在静态报告页。LLM 未配置、请求失败、返回非 JSON、缺少必要字段或包含投资建议语言时必须失败。
- TUI 研究详情必须暴露同等的“生成研究备忘录”动作，显示 LLM 调用中的 loading 状态，并复用 `lad research memo` 的失败边界和非投资建议边界。备忘录生成后不得只显示静态结果或只允许返回任务列表，必须继续提供可选择动作：记录研究复核、重新下钻核验、查看研究备忘录历史、返回研究任务列表。
- `lad research review` 是单条研究任务的复核记录入口，必须先运行同一套下钻核验，然后写入 `research-review-*.json` 和 SQLite `research_reviews` 表。复核判断记录完成后，CLI 必须根据 verdict 打印对应的工作台下一步命令，例如 `continue_research` 后运行 `lad research memo` 和重新核验，或 `needs_more_evidence` 后运行 `lad research run --force` 和重新核验。复核判断只能表达研究流程状态：继续研究、需要补证据、暂停观察或存在阻塞；不得表达买入、卖出、持有、仓位、目标价或收益预期。
- `lad research reviews` 是研究复核历史查看入口，必须从 SQLite `research_reviews` 表读取记录，展示复核判断、备注、证据数量、review artifact 和对应的下钻核验 artifact。该命令用于复盘研究流程，不得将历史复核解释成买卖清单。
- `lad research memos` 是研究备忘录历史查看入口，必须从 SQLite `research_memos` 表读取记录，展示摘要、置信度、支持/反方/待补/下一步计数、memo artifact 和对应的下钻核验 artifact。该命令用于复盘研究过程，不得将备忘录历史解释成买卖清单。
- `lad audit list` 列出已生成的报告和决策记录。

## 数据新鲜度策略

本地 cache 必须有明确保质期。默认路径优先复用未过期数据，只有过期、缺失或用户显式传入 `--force` 时才刷新。

行情 cache 的保质期必须同时考虑数据类型和市场交易状态：

- 美股常规交易时段按 9:30-16:00 ET 处理。
- 港股常规交易时段按 9:30-12:00、13:00-16:00 HKT 处理。
- A 股常规交易时段按 9:30-11:30、13:00-15:00 CST 处理。
- 交易中默认 15 分钟保质期。
- 港股/A 股午休期间默认不刷新，等下午开盘后再判断。
- 收盘后允许做一次收盘确认刷新；确认后的行情 cache 冻结到下一个交易日开盘。
- 周末默认不刷新；第一版暂不内置完整节假日日历。
- `--force` 必须绕过保质期和交易时段判断。

新闻 cache 使用基础 TTL 策略：

- 默认保质期为 1 小时。
- 未过期时复用本地 `news-events.json`，避免 discovery 和手动钻取反复消耗 provider 配额。
- `--force` 必须绕过新闻保质期。

cache 状态写入 `.alphadesk/research.sqlite3` 的 `cache_entries` 表，记录 layer、cache_key、provider、artifact_path、created_at、expires_at、ttl_seconds、market、session_state、row_count 和 is_final_for_session。

## 交互规范

这是整个项目的永久交互规则：

- 面向人的交互式界面必须 keyboard-navigation-first。
- 菜单和选项选择必须使用 ↑/↓/←/→/Tab 移动，Enter 确认，Esc 返回或退出。
- 菜单不得使用数字、字母或输入命令别名作为选项选择方式。
- 只有真实值才允许文本输入，例如 API key、URL、模型名、证券代码或文件路径。
- v0.1 面向人的 CLI/TUI 文案必须中文优先；内部 provider 名、命令参数、模型 ID、证券代码等机器标识可以保留原文。
- LLM、网络请求、数据拉取和报告生成等可能耗时的动作，必须在阻塞调用前显示 loading/等待状态；失败时必须给出可读错误，不得让用户误以为终端卡死。
- 面向自动化脚本和 coding agent 的非交互命令允许存在，但必须通过显式命令参数传值，而不是隐藏的菜单选择。
- 非 TTY 环境不得提供数字或字母式文本菜单 fallback。

## 6. TUI 范围

v0.1 的 TUI 应该小而有用。

页面：

- Today Discovery：美股/港股/A 股主题、有证据支撑的关注候选、风险提示和建议拉取的数据。
- Today：每日结论、风险状态和不操作理由。
- Portfolio：模拟持仓、现金比例、目标偏离和政策违反项。
- News：demo 事件聚类和受影响资产。
- Forecasts：mock 预测区间和简单基准对比。
- Memos：生成的研究备忘录预览。
- Policy：已加载投资政策和校验结果。
- Providers：demo provider 健康状态。
- Audit：历史报告记录。

TUI 要求：

- 键盘优先导航。
- 本地快速启动。
- Demo 模式不需要网络。
- 清楚标记 demo 数据。
- 不提供实盘下单入口。
- 不要把输入股票代码作为新手进入产品的第一步。

## 6.1 今日市场发现引擎

产品入口必须是发现优先。

引擎先从广域证据出发，再逐步收敛到主题和候选标的：

```text
美股/港股/A 股市场概览 -> 广域新闻与事件 -> 证据包 -> LLM 综合分析 -> 关注候选 -> 钻取详细数据
```

必须覆盖的市场：

| 市场 | 第一轮数据 | 预期输出 |
| --- | --- | --- |
| 美股 | 主要指数、ETF、财经新闻、SEC filings、大盘股观察池 | 主题、股票、ETF、行业候选 |
| 港股 | 恒生系列指数、港股市场新闻、HKEX 公告、港币/利率背景 | 主题、股票、中国相关行业、IPO/打新提示 |
| A 股 | 宽基指数、行业板块、公告、财报/业绩预告、IPO/打新提示 | 主题、A 股候选、政策相关行业 |

Discovery report 必须包含：

- 市场覆盖情况和缺失 provider 的 warning。
- 来源列表，包括 provider 名称和时间戳。
- 主题，包括摘要、证据 ID、相关行业、风险提示和置信度。
- 关注候选，包括展示名称、可选股票代码、市场、资产类型、证据 ID、风险提示和建议拉取的数据。
- 明确说明候选只是研究对象，不是买入/卖出建议。

新闻和事件进入 LLM 前必须先整理成 evidence pack。证据项使用稳定的本地 ID，例如 `news_001`，并包含 headline、summary、source_url、timestamp、provider、symbols 和 tags。Evidence pack 应过滤明显荐股噪音，例如 direct buy picks、target price 或 analyst rating 文章。

LLM 可以负责摘要、聚类、提取、比较和建议下一步研究数据。LLM 必须引用 evidence pack 中的证据 ID，不得只写模糊证据描述。系统必须校验 LLM 返回的证据字段；如果证据不是本地 evidence pack 中存在的 ID，例如 `news_001`，命令必须失败且不得写入 discovery cache 或研究队列。LLM 不得输出直接买入/卖出结论、目标价、自动仓位配置或实盘交易指令。

如果没有配置 LLM provider、API 请求失败，或模型没有返回有效 JSON，命令必须失败并显示 setup/error 指引，而且不得写入 discovery cache。

## 6.2 Research Deepen Engine

Research Deepen 是 discovery 之后的二阶段研究准备层。它读取 SQLite 研究队列和本地 live cache，生成可审计研究包，而不是直接输出投资结论。

研究深挖包必须包含：

- 候选身份：candidate_id、display_name、symbol、market、asset_type、related_theme、why_watch、confidence 和 status。
- discovery 证据 ID 及可展开证据详情。
- 本地缓存数据：行情、相关新闻、公告。
- 数据缺口：缺少 symbol、缺少行情、缺少公告或证据 ID 无法在当前本地缓存中解析。
- 下一步核验动作。
- 明确的非投资建议免责声明。

Research Deepen 不得输出直接买入/卖出结论、目标价、自动仓位配置或实盘交易指令。

研究数据工作流必须形成闭环：

1. 开发或调整数据/研究能力。
2. 使用真实命令拉取或读取真实缓存数据。
3. 重新生成研究深挖包。
4. 检查 data_gaps 是否减少、证据是否可追溯、输出是否仍然不构成投资建议。
5. 如果数据仍不满足研究需要，继续开发补齐能力，而不是把缺口留给用户猜。

自动补缺口第一版仅处理确定性动作：已有 symbol 的行情、US stock 的 SEC filings。行情 provider 必须支持逐 symbol 容错：一个市场或 provider 失败不得丢弃其它 symbol 已成功拉取的数据。缺少 symbol 的候选进入映射队列，后续应由 symbol mapping provider 或 LLM-assisted mapping 在有证据约束下处理。

## 7. Provider 接口

核心系统依赖接口，不依赖具体厂商 SDK。

Provider 类型：

- `MarketDataProvider`
- `NewsProvider`
- `FilingProvider`
- `MacroProvider`
- `DiscoveryProvider`
- `ForecastProvider`
- `LLMProvider`
- `BrokerProvider`
- `StorageProvider`

v0.1 providers：

- `DemoMarketDataProvider`
- `DemoNewsProvider`
- `DemoFilingProvider`
- `DemoForecastProvider`
- `DemoLLMProvider`
- `ManualCsvBrokerProvider`

真实 provider 应在接口稳定后再逐步加入。

插件规则：

- 真实 provider 必须作为可选 extras，而不是默认依赖。
- 券商集成在加入任何订单草稿流程之前，必须先保持只读。
- Provider 失败时必须返回结构化警告，并包含来源名称和时间戳。
- Demo 模式绝不能把 demo 数据和 live provider 数据静默混用。

## 8. 投资政策文件

政策文件格式：YAML。

最小字段：

```yaml
base_currency: USD
live_trading: false

risk_limits:
  min_cash_weight: 0.30
  max_single_asset_weight: 0.25
  max_experimental_weight: 0.00

blocked_products:
  - margin
  - options
  - futures
  - leveraged_etf
  - inverse_etf
  - crypto

decision_requires:
  - data_quality_check
  - source_links
  - counterargument
  - human_approval
```

校验输出必须包括：

- Errors：会阻止报告生成的无效配置。
- Warnings：有风险但可接受的配置。
- Passes：明确通过的规则。

## 9. 每日报告

v0.1 报告格式为 Markdown。

必需章节：

- 每日结论。
- 组合快照。
- 投资政策检查。
- 数据质量状态。
- 新闻和事件。
- 预测摘要。
- 反方审查。
- 不操作或行动理由。
- 审计元数据。

报告必须明确标记 demo 数据。

## 10. 审计日志

每次生成报告都应写入审计记录。

最小字段：

- 报告 id。
- 生成时间。
- 投资政策文件路径。
- Provider 名称。
- 输入快照 id。
- 输出报告路径。
- Demo 或 live 模式。
- 警告和错误。

存储：

- SQLite 存元数据。
- 本地文件存 Markdown 报告。

## 11. 安全默认值

v0.1 默认：

- 首次运行使用 demo 模式。
- 禁用实盘交易。
- Broker provider 可选。
- LLM provider 对 demo/report 流程可选，但对 Today Discovery 必选。
- TimesFM provider 可选。
- Provider key 存在用户配置目录，而不是项目级 `.env` 文件。
- 所有真实 provider 失败都必须降级为明确警告。
- 不允许从真实数据静默 fallback 到 demo 数据。

## 12. 开源默认值

仓库应该容易试用，也容易审计。

默认要求：

- 第一次成功运行不需要 API key。
- 第一次成功运行不需要券商账户。
- 第一次成功运行不需要后台服务。
- CLI 输出应说明报告和审计记录写到了哪里。
- 生成的 demo 报告必须包含清晰的非投资建议免责声明。
- 配置示例应优先采用保守、长期投资的假设。

## 13. 验收标准

v0.1 完成时，新用户应该可以：

1. clone 仓库。
2. 用 uv 安装 package。
3. 运行 `lad demo`。
4. 运行 `lad report --demo`。
5. 打开生成的 Markdown 报告。
6. 运行 `lad` 并浏览 TUI。
7. 理解系统是研究优先，而不是交易机器人。
8. 查看投资政策校验和审计记录。

## 14. 实现顺序

1. Python package 和 CLI 骨架。
2. Demo 数据文件。
3. Pydantic 配置和投资政策模型。
4. Provider 接口。
5. Demo providers。
6. 数据质量检查。
7. Markdown 报告模板。
8. 审计存储。
9. Textual TUI 外壳。
10. 测试和 quickstart 文档。
