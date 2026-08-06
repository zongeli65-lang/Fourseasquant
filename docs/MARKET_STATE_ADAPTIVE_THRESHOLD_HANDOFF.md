# 市场状态自适应阈值任务交接

> **2026-08-06 正式化更新（优先级最高）**
> 用户已明确选择“原始第八版”为正式市场状态规则。本文后续关于“只做隔离
> 原型、不得修改正式规则或数据库”的文字仅是历史实验边界，现已被本次明确
> 授权取代，不再是下一位智能体的执行限制。

## 0. 当前正式状态（2026-08-06）

- 正式规则版本：`market-environment-contextual-momentum-v8`。
- 正式快照结构：`market-environment-snapshot-v2`。
- 单一正式入口：`backend/fourseasquant/market_regime_v8.py`。
- 正式发布、读取和接口模型：`backend/fourseasquant/market_environment.py`。
- 正式页面：`frontend/src/MarketEnvironmentPage.tsx`。
- 原始第八版参数保持不变，反转实体门槛仍为 `0.50 ATR`；没有采用已否决的
  `0.60 ATR` 尝试。
- 五指数：上证、深证、沪深300、创业板、科创50；日期必须完全对齐。
- 正式内核与原始第八版三年 CSV 逐日核对：727日、状态差异0日。
- 关键日期：2024-10-08 震荡（顶部衰竭）、2024-10-09 下行（强下降验证）、
  2024-10-18 震荡（空头动能结束）。
- 生产数据库已追加291个新版本快照（2025-05-28至2026-08-05）；回测数据库
  已追加987个新版本快照（2022-07-07至2026-07-31）。旧版快照均保留。
- 验证：后端693项普通测试通过；16项系统测试和3个子测试通过；市场环境
  浏览器测试通过；前端生产构建通过；本次8个后端文件严格类型检查通过。
- 全工程严格类型检查仍有11个既有错误，位于三个并行回测测试文件，与本次
  市场状态改动无关，不能据此声称全工程类型检查通过。

后续修改必须从上述正式入口继续，禁止让生产代码重新依赖 `work/` 目录。
原型文件和输出仅用于追溯与可视化对照。

> 交接日期：2026-08-05（Asia/Shanghai）
> 项目：Fourseasquant
> 项目目录：`/Users/lz666/Documents/quant/fourseasquant-t01`
> 当前任务性质：隔离原型与三年回放，尚未进入正式市场状态规则
> 数据源：本机回测数据库中的 AKShare 指数日线
> GitHub：本任务没有要求提交或推送

## 1. 下一位 Agent 需要完成什么

完成“市场技术综合状态第三版（自适应阈值）”的三年回放、可视化、诊断报告和逐日审计数据。

用户认为第二版的突破和相对强度阈值过低，造成频繁切换。已经完成原因诊断和自适应算法主体，下一位 Agent 不需要重新设计，只需完成运行器、可视化、验证和交付。

不得直接修改正式网站、正式数据库或当前生产市场规则。先交付隔离实验，待用户查看图和指标后再决定是否正式接入。

## 2. 用户当前需求

用户要求市场状态不仅看高点、低点结构，还要加入技术评分中的：

- 结构趋势；
- 突破强度；
- 相对强度；
- 成交量确认。

但第二版阈值太低：

- 早期触发：至少1个指数突破、相对强度不低于0.30、快速技术分不低于6；
- 广泛触发：至少3个指数突破、相对强度不低于0.20、快速技术分不低于10；
- 快速证据保留5个交易日。

用户指出第二版因此再次出现频繁切换，希望提高阈值或改成自适应阈值。

当前选定方案是自适应阈值，而不是继续手工增加固定阈值。

## 3. 不允许改变的边界

1. 不修改已有个股技术评分公式和权重。
2. 突破、相对强度、成交量的计算继续沿用现有映射，不重新发明指标。
3. 不使用未来数据。第 `t` 日门槛只能使用第 `t-1` 日及以前的数据。
4. 多头和空头规则必须完全对称。
5. 缺少快速共振时回退到纯高低点结构状态。
6. 不写正式数据库，不覆盖已有市场状态快照。
7. 不同步 GitHub，除非用户后续明确要求。
8. 当前工作树存在大量其他任务改动，禁止 `git reset`、`git clean`、`git add .` 或覆盖无关文件。

## 4. 数据与回放范围

数据库：

```text
/Users/lz666/Documents/quant/fourseasquant-t01/data/backtests/technical_defensive.db
```

表：

```text
historical_benchmark_facts
```

五个指数及来源：

| 指数 | source |
|---|---|
| 上证指数 | `akshare_index_sh000001` |
| 深证成指 | `akshare_index_sz399001` |
| 沪深300 | `akshare_index_sh000300` |
| 创业板指 | `akshare_index_sz399006` |
| 科创50 | `akshare_index_sh000688` |

所有指数已核验覆盖：2022-06-09 至 2026-07-31，共1007个交易日。

正式展示与统计区间：

```text
2023-08-01 至 2026-07-31，共727个交易日
```

2022-06-09 至 2023-07-31 的数据仅用于指标预热和自适应阈值历史窗口。

## 5. 已完成的第一版：纯高低点结构

核心文件：

- `work/market_structure_regime_model.py`
- `work/prototype_market_structure_regime.py`

输出目录：

```text
outputs/market_structure_regime_20260805/
```

规则：

1. 收盘价计算 EMA3（三日指数移动平均）。
2. ATR10（十日平均真实波幅）的0.10倍作为有效斜率过滤带。
3. EMA3有效斜率反向时确认局部极值，状态从确认日生效，不回填到极值发生日。
4. 最近连续两次高点变化与最近连续两次低点变化均大于 `+0.10 ATR`：上行。
5. 两组变化均小于 `-0.10 ATR`：下行。
6. 极值不足、距离不足或高低点方向不一致：震荡。

三年结果：

| 指标 | 结果 |
|---|---:|
| 状态切换 | 30次 |
| 区间中位数 | 16日 |
| 上行 | 85日 |
| 震荡 | 528日 |
| 下行 | 114日 |

该版本稳定，但对快速反转识别过慢。

## 6. 已完成的第二版：固定阈值技术综合状态

核心文件：

- `work/market_technical_regime_model.py`
- `work/prototype_market_technical_regime_v2.py`

输出目录：

```text
outputs/market_technical_regime_v2_20260805/
```

技术映射：

### 6.1 突破强度

五个指数分别使用 EMA3 与最近确认高点/低点判断突破，计算方法沿用个股技术评分：

```text
单指数突破强度
= 0.75 × min(突破距离ATR, 3) / 3
+ 0.25 × min(连续突破天数, 5) / 5
```

市场突破分：

```text
20 × 五指数突破强度平均值
```

下行破位采用完全对称公式。

### 6.2 多指数相对强度

个股相对强度原本是个股相对市场基准；市场本身没有更高一级的同类基准，因此映射为五个指数在3日、5日、10日窗口内的同步方向强度：

```text
单窗口方向强度 = 收益率 / (abs(收益率) + 缩放参数)
```

缩放参数继续沿用技术评分：

| 窗口 | 缩放参数 |
|---|---:|
| 3日 | 5 |
| 5日 | 8 |
| 10日 | 12 |

对五指数、三个窗口取平均，得到范围约为 `[-1, +1]` 的市场相对强度。

### 6.3 成交量确认

每个指数：

```text
成交量确认 = clip(当日成交量 / 前10日平均成交量 / 2, 0, 1)
```

五指数取平均，权重为10；成交量只增强已有方向，不单独决定方向。

### 6.4 快速技术分

```text
上行快速技术分
= 20 × 上行突破强度
+ 15 × max(多指数相对强度, 0)
+ 10 × 成交量确认 × 上行突破指数数量 / 5
```

下行快速技术分完全对称。

第二版三年结果：

| 指标 | 结果 |
|---|---:|
| 状态切换 | 82次 |
| 区间中位数 | 7日 |
| 3日及以下短区间 | 11段 |
| 5日及以下短区间 | 32段 |
| 上行 | 245日 |
| 震荡 | 240日 |
| 下行 | 242日 |
| 上行触发日 | 104日 |
| 下行触发日 | 84日 |
| 快速覆盖 | 432日 |

2024年2月验证：

- 纯结构版在2024-02-08仍为下行；
- 沪深300率先突破；
- 多指数相对强度为0.339523；
- 上行快速分为6.766662；
- 第二版在2024-02-08切换为上行。

说明第二版解决了快速反转滞后，但触发门槛过低。

## 7. 已完成的频繁切换诊断

第二版82次切换的来源：

| 原因 | 次数 | 占全部切换 |
|---|---:|---:|
| 当日快速共振触发 | 39 | 47.6% |
| 五日效力到期后回退 | 32 | 39.0% |
| 纯结构状态变化 | 11 | 13.4% |

快速技术层共造成71次切换，占86.6%。用户的判断正确：主要问题是快速入口阈值低、触发过多，随后五日效力到期又回退。

## 8. 已实现但尚未完成回放的第三版自适应规则

已有文件：

```text
work/market_technical_regime_adaptive_model.py
```

该文件已经实现纯计算函数：

```python
apply_adaptive_thresholds(...)
```

默认参数：

| 参数 | 值 |
|---|---:|
| 历史窗口 | 120个交易日 |
| 最少历史 | 40个交易日 |
| 自适应分位数 | 第85百分位 |
| 快速分最低阈值 | 6.5 |
| 相对强度最低阈值 | 0.30 |
| 快速证据效力 | 5个交易日 |

多头门槛：

```text
当日上行快速分门槛
= max(6.5, 过去120日所有非零上行快速分的第85百分位)

当日上行相对强度门槛
= max(0.30, 过去120日所有正相对强度的第85百分位)
```

空头门槛完全对称：只读取过去120日非零下行分和负相对强度的绝对值。

触发条件：

```text
至少1个指数发生同方向突破
AND 当日方向快速分 >= 当日自适应快速分门槛
AND 当日方向相对强度 >= 当日自适应相对强度门槛
```

关键防未来数据规则：

```python
history = evidence_days[max(0, index - lookback_days):index]
```

切片不包含 `index` 当日，禁止改成包含当前日。

## 9. 探索性回放结果（下一位 Agent 必须重新正式运行验证）

当前会话使用第二版逐日证据进行过一次内存回放，自适应方案的探索性结果为：

| 指标 | 固定阈值第二版 | 自适应第三版（探索值） |
|---|---:|---:|
| 状态切换 | 82次 | 51次 |
| 区间中位数 | 7日 | 11日 |
| 3日及以下短区间 | 11段 | 3段 |
| 5日及以下短区间 | 32段 | 9段 |
| 上行触发日 | 104日 | 34日 |
| 下行触发日 | 84日 | 25日 |
| 2024年2月首次上行触发 | 2024-02-08 | 2024-02-08 |

这些数字不是最终交付，必须由正式运行脚本重新生成、写入 `result.json` 并做逐日不变量核验。

## 10. 下一位 Agent 的具体执行步骤

### 第一步：验证现有模块

```zsh
cd /Users/lz666/Documents/quant/fourseasquant-t01
PYTHONPATH=work /private/tmp/fourseasquant-market-chart-venv/bin/python -m py_compile \
  work/market_structure_regime_model.py \
  work/market_technical_regime_model.py \
  work/market_technical_regime_adaptive_model.py
```

如果临时绘图环境不存在，优先使用项目虚拟环境并核验其中的 Matplotlib；禁止向系统 Python 安装包。

### 第二步：新建单命令运行器

建议文件：

```text
work/prototype_market_technical_regime_v3_adaptive.py
```

该脚本应：

1. 只读加载五指数历史数据；
2. 调用 `classify_market_technical_regime` 生成结构、突破、相对强度和成交量逐日证据；
3. 调用 `apply_adaptive_thresholds` 生成第三版逐日状态；
4. 截取2023-08-01至2026-07-31作为正式统计区间；
5. 生成完整CSV、结果JSON、诊断报告和可视化；
6. 不写正式数据库。

### 第三步：输出目录

建议：

```text
outputs/market_technical_regime_v3_adaptive_20260805/
```

至少生成：

```text
market_technical_regime_v3_adaptive_daily.csv
threshold_comparison.csv
result.json
DIAGNOSTIC_REPORT.md
three_year_market_technical_regime_v3_adaptive.png
three_year_market_technical_regime_v3_adaptive.pdf
three_year_market_technical_regime_v3_adaptive.svg
feb_2024_market_technical_regime_v3_adaptive_zoom.png
feb_2024_market_technical_regime_v3_adaptive_zoom.pdf
feb_2024_market_technical_regime_v3_adaptive_zoom.svg
```

### 第四步：逐日CSV至少包含

- 日期、上证开高低收、成交量、EMA3；
- 第三版最终状态；
- 第二版固定阈值状态；
- 纯结构状态；
- 快速覆盖方向；
- 当日上行/下行是否触发；
- 上行/下行快速分；
- 上行/下行当日自适应快速分门槛；
- 当日相对强度；
- 上行/下行当日自适应相对强度门槛；
- 成交量确认；
- 上行/下行突破指数数量及指数名称。

### 第五步：可视化要求

三年全图和2024年2月放大图均应包含：

1. 上证指数日K线与EMA3；
2. 第三版状态背景；
3. 上行/下行快速分及各自动态门槛；
4. 多指数相对强度及多空动态门槛；
5. 成交量；
6. 第三版状态带；
7. 第二版状态带；
8. 两版差异带；
9. 快速共振触发标记；
10. 2024-02-08首次触发的直接标注。

图表必须输出 PNG、PDF、SVG，并使用本项目现有米黄色专业视觉。最终必须用视觉检查工具打开PNG，检查裁切、标签、图例和阈值线。

### 第六步：必须完成的不变量验证

1. CSV正好727个唯一交易日。
2. 每日自适应阈值只能由此前120个交易日生成。
3. 历史不足40日时使用最低阈值。
4. 多头触发必须同时满足突破数量、快速分门槛、相对强度门槛。
5. 空头规则与多头规则完全对称。
6. 快速覆盖为多头时最终状态必须为上行；为空头时必须为下行。
7. 无快速覆盖时最终状态必须等于纯结构状态。
8. 2024-02-08应保持：纯结构下行、第三版上行、沪深300突破。
9. 结果JSON列出的每个输出文件必须存在且非空。
10. 正式数据库和 `backend/fourseasquant/market_environment.py` 不得发生变化。

## 11. 验收目标

第三版应同时满足：

- 状态切换显著少于第二版82次，建议不超过60次；
- 区间中位数不少于10个交易日；
- 5日及以下短区间不超过10段；
- 2024年2月快速上涨仍能在纯结构确认以前识别；
- 没有未来数据泄漏；
- 所有阈值和触发来源可逐日审计。

如果正式结果不满足，不要偷偷改参数追求目标。应输出失败结果和敏感性对照，再让用户决定。

## 12. 后续正式接入边界

本交接只授权完成隔离实验。只有用户查看第三版图和统计后明确批准，才可以考虑：

1. 新建版本化正式规则，例如 `market-environment-v3-technical-adaptive`；
2. 在后端加入正式状态发布与数据库版本；
3. 增加单元测试、系统测试和浏览器测试；
4. 更新市场环境页面；
5. 让策略读取新状态；
6. 重新回测所有受市场状态影响的策略版本。

在获得批准前，绝不能让该原型影响每日自动策略。

## 13. 相关文件索引

### 正式代码参考

- `backend/fourseasquant/technical_scoring.py`：技术评分参数和公式来源。
- `backend/fourseasquant/market_environment.py`：当前正式市场环境规则，仅供核对，不要修改。

### 原型代码

- `work/market_structure_regime_model.py`
- `work/prototype_market_structure_regime.py`
- `work/market_technical_regime_model.py`
- `work/prototype_market_technical_regime_v2.py`
- `work/market_technical_regime_adaptive_model.py`

### 已完成结果

- `outputs/market_structure_regime_20260805/result.json`
- `outputs/market_structure_regime_20260805/market_structure_regime_daily.csv`
- `outputs/market_structure_regime_20260805/three_year_market_structure_regime.png`
- `outputs/market_technical_regime_v2_20260805/result.json`
- `outputs/market_technical_regime_v2_20260805/market_technical_regime_v2_daily.csv`
- `outputs/market_technical_regime_v2_20260805/three_year_market_technical_regime_v2.png`
- `outputs/market_technical_regime_v2_20260805/feb_2024_market_technical_regime_v2_zoom.png`

## 14. 给下一位 Agent 的一句话

不要重新设计市场规则；在现有三个纯计算模块上完成第三版自适应阈值的正式隔离回放、全量审计和两张可视化，确认切换从82次降到约51次且2024-02-08仍能提前识别，然后把结果交给用户决定是否进入正式系统。
