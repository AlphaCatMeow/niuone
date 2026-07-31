# app 模块结构

`app/` 根目录不再放业务实现或零散入口。受支持的命令行/服务入口集中在 `app/entrypoints/`，历史裸模块适配器集中在 `app/compat/`，其余子包承载领域实现。兼容适配器由 `_compat.py` 在历史模块命名空间中执行迁移后的实现，因此运行时 monkeypatch 语义保持不变。

## 领域边界

| 目录 | 职责 | 不应承担的职责 |
|---|---|---|
| `app/core/` | 运行路径策略、原子 JSON 缓存等跨领域基础设施 | 业务规则、服务编排 |
| `app/automation/` | 定时任务模型、Cron 匹配、子进程执行、失败重试和调度器状态 | 报告正文生成、策略评分、成交实现 |
| `app/dashboard/` | FastAPI 路由、安全辅助、公开展示投影、版本化快照、API 缓存、行情采样及页面侧实战计划编排 | 策略评分细节、账户存储实现、外部数据源解析 |
| `app/market_data/` | 行情/研究数据访问、证券代码规范化、日 K 与量能缓存 | 策略决策、账户与成交状态 |
| `app/messaging/` | 通知模型、渠道适配、HTTP 传输、分发和成交消息格式 | 交易状态持久化 |
| `app/monitoring/x/` | X 关注列表抓取、媒体/上下文解析、消息格式、进程循环与重试状态 | Dashboard 路由、交易决策 |
| `app/reports/a_share/` | A 股竞价、午盘、盘后、日历、龙虎榜和模型增强报告 | Cron 触发时机、Dashboard 路由 |
| `app/reports/us/` | 隔夜美股摘要与机构评级报告 | Cron 触发时机、Dashboard 路由 |
| `app/storage/` | 消息历史、模拟盘、报告的 SQLite/文件存储接口、去重与迁移 | 策略评分、HTTP 路由 |
| `app/screening/` | 多策略/全市场扫描、候选行业增强、题材强度与分钟缓存编排 | 账户执行、HTTP 路由 |
| `app/strategies/` | 策略注册、评分、归因、选股、风险预算、退出规则和提示词片段 | 行情 I/O、账户落盘 |
| `app/trading/` | 模拟账户、模型决策、执行风控、成交落盘、优化器和卖出信号 | Dashboard HTTP 路由、Cron 调度 |

`entrypoints/` 中的 Dashboard、交易器、调度器、监控器和报告入口均为薄启动器；`compat/` 中的各 `*_dashboard_api.py`、`notifications.py` 及历史模块名均为薄适配器。Dashboard 的生产 HTTP 组合层位于 `dashboard/fastapi_app.py`，接口实现按领域位于 `dashboard/routers/`；`dashboard/server.py` 保留后台状态、配置、数据源、行情采样和页面侧实战计划的组合函数。其他实际组合实现位于 `trading/practice_trader.py`、`automation/scheduler_service.py`、各领域的 `*_service.py` 等文件。

组合层的正式执行合同是直接运行 `app/entrypoints/*.py`。领域实现使用 `app.<domain>` 包路径；仍依赖历史裸模块名的组合代码由入口统一加载 `app/compat/`，外部代码不应再依赖已经移除的 `app/*.py` 路径。

## 依赖方向

```text
启动脚本 / 兼容入口
        ↓
服务与组合层（dashboard、automation、screening、trading、reports、monitoring）
        ↓
规则与基础层（strategies、market_data、messaging、storage、core）
        ↓
标准库与外部数据源
```

上图表达主要依赖方向，不表示各包完全互不调用：例如 Dashboard 会编排扫描和模拟交易，交易层会读取策略规则、行情与存储接口。领域包不能反向导入根启动入口；`strategies/` 不应主动请求行情或写账户，`market_data/` 不应判断买卖，`storage/` 不应实现策略评分。进程锁、长生命周期缓存、路径与环境配置放在相应服务组合层；可复用计算函数优先接收显式参数。这样既能独立测试，又能保留调用方对旧模块全局值进行替换的兼容行为。

## Dashboard Web 与增量读模型

Dashboard 使用 Vue 3 + Vite 和 FastAPI/Uvicorn，并保持单进程、单监听端口及原页面布局。高频展示读取与服务端计算解耦：

- `public_projection.py` 只接受显式源数据，并用字段白名单生成展示模型；
- `public_snapshots.py` 原子发布内容寻址对象、manifest 和 latest 指针；
- `projection_service.py` 在后台固定频率读取服务端状态，浏览器轮询不会触发交易、行情或历史重算；
- `fastapi_app.py` 是唯一 HTTP 监听者，只组合中间件、Vue 构建、共享缓存响应和领域路由；
- `routers/` 显式声明 system、messages、market、practice、admin 五组浏览器接口；
- `security.py`、`visit_stats.py`、`response_cache.py` 接收显式状态和路径，分别实现访问控制、统计持久化和带失效代次的并发 JSON 缓存；
- `server.py` 的既有后台函数仍由 FastAPI 组合层复用，但其中已不存在 `BaseHTTPRequestHandler`、`ThreadingHTTPServer` 或静态页面分发；
- `web/` 保存 Vue 组件、Vite 配置和依赖锁，生产产物由 FastAPI 从 `web/dist/` 提供；
- Vue 已接管主题、合规弹窗、版本状态、栏目 bootstrap 与路由、最后刷新时间、全部公开栏目、完整模拟账户及管理页；旧 `frontend/*.html`、`frontend/dashboard.js` 和 `frontend/admin.js` 已删除，`frontend/` 仅保存 Vue 复用的样式。模拟账户拆为账户概览、持仓/卖出卡片、收益曲线、交易日历、操作日志、规则、盘面总结和候选股组件，数据层共享公开投影订阅并按区块摘要刷新。FastAPI 显式声明全部浏览器 API，未知路径直接返回 404；管理员会话、操作请求头、请求体上限与分级限流语义保持不变。
- “题材强度”页面使用完整研究快照 `niuone_mainline_latest.json`、分钟行情快照 `niuone_mainline_minute_latest.json` 和 `niuone_mainline` 公开投影区块。共享定时或手动交易扫描结束后会另启 `--niuone-mainline-only` 全市场研究任务，它忽略当前交易策略且只更新完整题材缓存，不覆盖候选缓存或触发买卖。Dashboard 在盘前通过 `--prewarm-kline-cache` 把全量非 ST 股票最近 120 根腾讯前复权日 K 原子写入私有 SQLite；市场宽度采样器默认每 30 秒（可配置为 30～600 秒）取得一份覆盖有效的逐股报价并交给进程内题材快速计算器，后者只读取本地日 K、行业映射和最近完整研究扫描的慢速确认分量，不重复请求腾讯或新闻模型。快速计算覆盖不足、报价过期或失败时保留上一份有效快照；公开页总是选择生成时间最新的有效题材结果。页面分别展示不改变交易门槛的“今日前5”和用于跨日主线确认的“结构前5”：今日强度按上涨广度 45%、涨幅至少 3% 的占比 25%、涨幅至少 5% 的占比 15% 和正中位涨幅 15% 计算，只有实时涨跌幅覆盖至少 80% 且有效报价不少于 3 只时才参与排名；结构分继续使用 5 日、20 日、量能、均线、龙头与跨日延续。`/niuone-mainline` 只消费字段白名单读模型，因此策略切换不会影响题材更新，同时逐股行情、原始个股上下文、SQLite 缓存和消息数据不会下沉到浏览器。
- 浏览器先检查轻量 latest 指针，只在区块摘要变化时加载对应数据；完整模拟账户历史仅在用户打开缺少分时数据的日历日期时按需读取，成功后本页面会话不再重复下载，失败最多每 5 分钟重试一次。
- Vue 资金流动画请求使用 `compact=1` 字段投影，服务端仅返回节点标识、名称、净额、采样时间和控件配置；完整响应仍保留给显式请求它的 API 客户端。

`/admin` 可以与公开页面通过同一域名访问，但所有配置读取、修改和测试操作仍必须经过管理员会话、限流与操作请求头校验。

## 变更检查

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
./scripts/validate.sh
```

新增功能应优先放入对应领域包；只有 CLI、HTTP 路由、调度或跨域编排代码留在根入口。
