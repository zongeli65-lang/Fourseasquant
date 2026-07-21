# Mac 开发环境 Agent 交接文档

> 用途：让后续 Codex/AI Agent 在执行任务前快速、准确地获取本机开发环境信息。
>
> 最后核验日期：2026-07-21（Asia/Shanghai）。版本可能随 Homebrew 更新而变化，关键任务开始前应重新运行版本检查。
>
> 安全说明：本文不包含密码、API Key、GitHub Token、Tushare Token 或其他秘密。Agent 不得读取、输出或提交这些秘密。

## 1. 用户偏好与操作原则

- 用户希望需要直接操作的交付文件优先放在桌面：`/Users/lz666/Desktop`。
- 若当前沙箱不能写桌面，应先保存到任务的 `outputs/`，再申请权限复制到桌面。
- 中间文件、测试代码和临时资产放在任务的 `work/`，不要堆放到桌面。
- Python 项目必须使用项目虚拟环境，禁止向 macOS 系统 Python 安装第三方库。
- 不要在文档、代码、终端输出或 Git 中写入密码和 API Token。
- 当前主要方向：AI Agent 开发、Python/机器学习、量化金融、数据分析、Web、GitHub、C/C++。

## 2. 设备与系统

| 项目 | 当前值 |
|---|---|
| 设备 | MacBook Pro |
| Model Identifier | Mac17,2 |
| 芯片 | Apple M5，Apple Silicon / ARM64 |
| macOS | 26.5.1，Build 25F80 |
| 默认 Shell | `/bin/zsh` |
| zsh | 5.9，ARM64 |
| 用户目录 | `/Users/lz666` |
| 当前 Codex 工作区 | `/Users/lz666/Documents/Codex/2026-07-18/v` |

## 3. Shell 与 Homebrew

| 工具 | 版本 | 路径 |
|---|---:|---|
| Homebrew | 6.0.11 | `/opt/homebrew/bin/brew` |
| Homebrew Prefix | — | `/opt/homebrew` |

`/Users/lz666/.zprofile` 已包含：

```zsh
eval "$(/opt/homebrew/bin/brew shellenv zsh)"
```

因此在新开的交互式 zsh 中可直接使用 `brew`、`python3.14`、`node`、`npm` 和 `gh`。自动化 Agent 若继承不到该 PATH，应使用绝对路径或先执行：

```zsh
eval "$(/opt/homebrew/bin/brew shellenv zsh)"
```

常用检查：

```zsh
brew doctor
brew list
brew list --cask
```

## 4. 编译与版本控制基础

| 工具 | 版本 | 路径/状态 |
|---|---:|---|
| Xcode Command Line Tools | 已安装 | `/Library/Developer/CommandLineTools` |
| Apple Clang | 21.0.0 | `/usr/bin/clang`、`/usr/bin/clang++` |
| 完整 Xcode App | 未安装 | 当前 AI、Python、Web、通用 C/C++ 工作不受影响 |
| Git | 2.50.1 | `/usr/bin/git` |

Git 全局提交身份当前仍未配置：

- `user.name`：未设置
- `user.email`：未设置
- `init.defaultBranch`：未设置

Agent 不得猜测这些值；需要提交代码时应向用户确认姓名与邮箱。建议用户设置默认分支为 `main`。

## 5. GitHub 与 SSH

| 项目 | 状态 |
|---|---|
| GitHub CLI | 2.96.0，`/opt/homebrew/bin/gh` |
| 登录账户 | `zongeli65-lang` |
| Git 协议 | SSH |
| GitHub CLI 登录 | 已完成，凭据存于 macOS Keychain |
| SSH 连接 | 已验证成功 |

由于当前网络的 22 端口连接受限，`/Users/lz666/.ssh/config` 已配置 GitHub SSH-over-HTTPS：

```sshconfig
Host github.com
    Hostname ssh.github.com
    Port 443
    User git
```

验证命令：

```zsh
ssh -T git@github.com
gh auth status
```

SSH 克隆格式：

```zsh
git clone git@github.com:zongeli65-lang/REPOSITORY.git
```

## 6. Python 与 uv

| 项目 | 版本 | 路径 |
|---|---:|---|
| Homebrew Python | 3.14.6 | `/opt/homebrew/bin/python3.14` |
| uv | 0.11.29 | `/opt/homebrew/bin/uv` |
| macOS 系统 Python | 3.9.6 | `/usr/bin/python3`，不要用于项目依赖 |
| 量化虚拟环境 | Python 3.14.6 | `/Users/lz666/Developer/quant/.venv` |
| site-packages | — | `/Users/lz666/Developer/quant/.venv/lib/python3.14/site-packages` |

进入量化环境：

```zsh
cd /Users/lz666/Developer/quant
source .venv/bin/activate
python --version
```

不激活时直接调用：

```zsh
/Users/lz666/Developer/quant/.venv/bin/python script.py
```

使用 uv 安装新依赖：

```zsh
/opt/homebrew/bin/uv pip install --python /Users/lz666/Developer/quant/.venv/bin/python PACKAGE
```

## 7. 已安装的 Python 科学计算、AI 与量化库

以下库均安装在量化虚拟环境，不在系统 Python：

| 库 | 版本 | 典型导入/调用 | 用途 |
|---|---:|---|---|
| NumPy | 2.5.1 | `import numpy as np` | 数组、向量、矩阵计算 |
| pandas | 3.0.3 | `import pandas as pd` | 表格与金融时间序列 |
| SciPy | 1.18.0 | `from scipy import stats` | 优化、统计、科学计算 |
| matplotlib | 3.11.1 | `import matplotlib.pyplot as plt` | 数据可视化 |
| scikit-learn | 1.9.0 | `import sklearn` | 机器学习与模型评估 |
| PyTorch | 2.13.0 | `import torch` | 深度学习、Apple MPS 加速 |
| JupyterLab | 4.6.1 | `jupyter lab` | Notebook 数据分析 |
| AKShare | 1.18.64 | `import akshare as ak` | 中国金融市场数据 |
| Tushare | 1.4.29 | `import tushare as ts` | 股票、基金、宏观数据；需用户 Token |
| yfinance | 1.5.1 | `import yfinance as yf` | 海外市场数据 |
| backtrader | 1.9.78.123 | `import backtrader as bt` | 策略回测 |
| quantstats | 0.0.81 | `import quantstats as qs` | 策略绩效分析 |

JupyterLab 启动方式：

```zsh
source /Users/lz666/Developer/quant/.venv/bin/activate
jupyter lab
```

## 8. Node.js 与 Web 环境

| 工具 | 版本 | 路径 |
|---|---:|---|
| Node.js | 26.5.0 | `/opt/homebrew/bin/node` |
| npm | 11.17.0 | `/opt/homebrew/bin/npm` |

调用：

```zsh
node --version
npm --version
npm install
npm run dev
```

## 9. R、RStudio 与 LaTeX

| 工具 | 版本/状态 | 路径 |
|---|---|---|
| R | 4.6.1 | `/opt/homebrew/bin/R` |
| RStudio | 已安装 | `/Applications/RStudio.app` |
| BasicTeX / pdfTeX | TeX Live 2026，pdfTeX 1.40.29 | `/Library/TeX/texbin/pdflatex` |

调用：

```zsh
R
Rscript analysis.R
/Library/TeX/texbin/pdflatex report.tex
```

## 10. Visual Studio Code

| 项目 | 状态 |
|---|---|
| VS Code | 1.129.1，ARM64 原生版本 |
| App | `/Applications/Visual Studio Code.app` |
| CLI | `/opt/homebrew/bin/code` |
| 量化项目配置 | `/Users/lz666/Developer/quant/.vscode/settings.json` |

已安装扩展：

| 扩展 ID | 版本 | 用途 |
|---|---:|---|
| `ms-python.python` | 2026.4.0 | Python 语言支持 |
| `ms-python.debugpy` | 2026.6.0 | Python 断点调试 |
| `ms-python.vscode-pylance` | 2026.2.1 | 补全与类型分析 |
| `ms-toolsai.jupyter` | 2025.9.1 | Notebook 支持 |
| `ms-vscode.cpptools` | 1.32.2 | C/C++ 编辑与调试 |
| `charliermarsh.ruff` | 2026.62.0 | Python 格式化和静态检查 |

量化项目已固定解释器：

```text
/Users/lz666/Developer/quant/.venv/bin/python
```

打开项目：

```zsh
code /Users/lz666/Developer/quant
```

Python、Node.js 和 Clang C++ 均已通过实际运行测试。

## 11. AI 开发工具

| 工具 | 版本 | 路径/说明 |
|---|---:|---|
| Claude Code | 2.1.214 | `/Users/lz666/.local/bin/claude` |
| Codex CLI | 0.145.0-alpha.18 | `/Applications/ChatGPT.app/Contents/Resources/codex` |
| ChatGPT App | 已安装 | `/Applications/ChatGPT.app` |
| Cursor | 未安装 | VS Code 已作为主代码编辑器 |

调用：

```zsh
claude
/Applications/ChatGPT.app/Contents/Resources/codex
```

DeepSeek API 的实际接入状态未确认。Agent 不得搜索或输出用户密钥；若项目需要，应让用户通过安全环境变量或客户端密钥管理功能配置。

## 12. Docker

| 项目 | 当前状态 |
|---|---|
| Docker Desktop | 已安装，4.82.0（233772） |
| App | `/Applications/Docker.app` |
| Homebrew Cask | `/opt/homebrew/Caskroom/docker-desktop/4.82.0,233772` |
| Docker CLI | App 内置 29.6.1，路径 `/Applications/Docker.app/Contents/Resources/bin/docker` |
| `docker` PATH | 当前未检测到全局命令 |
| Docker daemon | 当前未运行 |

Agent 在使用 Docker 前应让用户从“应用程序”启动 Docker Desktop并完成首次授权。启动后检查：

```zsh
docker version
docker info
docker run hello-world
```

若启动后 `docker` 仍不在 PATH，可暂时使用完整路径：

```zsh
/Applications/Docker.app/Contents/Resources/bin/docker version
```

不要在未经用户确认时修改系统级 `/usr/local/bin`。

## 13. 终端效率工具

| 工具 | 版本 | 路径/调用 |
|---|---:|---|
| iTerm2 | 已安装 | `/Applications/iTerm.app` |
| tmux | 3.7b | `/opt/homebrew/bin/tmux` |
| starship | 1.26.0 | `/opt/homebrew/bin/starship` |
| zoxide | 0.10.0 | `/opt/homebrew/bin/zoxide` |
| eza | 0.23.5 | `/opt/homebrew/bin/eza` |
| fzf | 0.74.0 | `/opt/homebrew/bin/fzf` |

`~/.zshrc` 当前未检测到 starship/zoxide 初始化配置，因此它们已安装但不会自动启用。需要修改时先征得用户同意，再加入：

```zsh
eval "$(starship init zsh)"
eval "$(zoxide init zsh)"
```

## 14. 开发目录

```text
/Users/lz666/Developer/
├── python/  # Python 学习、脚本与数据分析
├── quant/   # 量化研究；包含 .venv 与 .vscode/settings.json
├── ai/      # AI、LLM、Agent 项目
├── web/     # Node/Web 项目
└── cpp/     # C/C++ 项目
```

## 15. 当前已完成与待完成事项

### 已完成

- Homebrew PATH 配置。
- Python 3.14、uv、科学计算、AI 和量化库安装。
- Node.js/npm、R/RStudio、BasicTeX 安装。
- VS Code 与 Python/Jupyter/C++/Ruff 扩展安装、运行验证。
- GitHub CLI 登录。
- GitHub SSH Key 验证，SSH 改走 443 端口。
- Docker Desktop App 安装。
- 开发目录创建。
- 61 个 Codex Skills 已安装到 `/Users/lz666/.codex/skills/` 并完成清单核验。

### 待用户完成或确认

- 设置 Git 全局 `user.name`、`user.email` 和默认分支。
- 启动 Docker Desktop、完成首次系统授权并确认 daemon 可用。
- 配置 Tushare Token、DeepSeek API Key 等个人凭据（仅在实际需要时）。
- 是否启用 starship、zoxide 的 zsh 自动初始化。

## 16. Agent 快速自检命令

```zsh
eval "$(/opt/homebrew/bin/brew shellenv zsh)"
brew --version
git --version
python3.14 --version
uv --version
node --version
npm --version
gh auth status
code --version
/Users/lz666/Developer/quant/.venv/bin/python -c "import numpy, pandas, torch, akshare; print('python env OK')"
/Library/TeX/texbin/pdflatex --version
```

Docker 必须在 App 已启动后单独检查：

```zsh
docker info
```

## 17. Codex Skills

### 17.1 安装状态与调用规则

截至 2026-07-21，共核验到 61 个用户级 Skill：

| 来源 | 数量 | 主要方向 |
|---|---:|---|
| `mattpocock/skills` | 22 | 软件工程、需求澄清、实现、测试、审查与交接 |
| `Imbad0202/academic-research-skills` | 1 | 学术研究与论文工作流整合 |
| `K-Dense-AI/scientific-agent-skills` | 22 | 科学计算、统计、机器学习、实验设计与科研写作 |
| `anthropics/financial-services` | 16 | 财务建模、股票研究、行业分析与投资工作流 |

统一安装根目录：

```text
/Users/lz666/.codex/skills/<skill-name>/SKILL.md
```

调用方式：在 Codex 对话中明确写“使用 `$技能名` + 目标 + 输入材料 + 输出要求 + 约束”。当任务与 Skill 描述高度匹配时，Codex 也可自动选择 Skill。示例：

```text
使用 $diagnosing-bugs，诊断这个 Python 程序为何内存持续增长，只报告原因，先不要修改代码。
使用 $academic-research-suite，围绕研究问题整理文献综述大纲，并标注需要补充的证据。
使用 $dcf-model，根据我提供的财报建立五年 DCF，输出 Excel 并列明假设和数据来源。
```

Skill 是 Agent 工作流程与专业说明，不代表配套 Python 包、外部数据库、付费 API 或模板已经全部安装。执行前仍需检查输入文件、依赖、凭据和数据授权。

### 17.2 软件工程与协作工作流（22）

| Skill | 作用 |
|---|---|
| `$ask-matt` | 根据当前问题推荐合适的 Matt Pocock 工作流或技能组合。 |
| `$setup-matt-pocock-skills` | 为仓库配置 Issue 跟踪方式、分诊标签和领域文档布局。 |
| `$grill-me` | 通过连续追问，把模糊需求、计划或技术方案澄清到可执行。 |
| `$grilling` | 可复用的深度访谈方法，追问决策分支、依赖关系和边界条件。 |
| `$grill-with-docs` | 在访谈同时维护领域术语、上下文和架构决策文档。 |
| `$to-spec` | 把已澄清的讨论整理为规格说明、PRD 或可验收方案。 |
| `$to-tickets` | 把规格拆成小型任务或 Issues，并标明依赖和验收条件。 |
| `$triage` | 分类、核验和补充 Issue/PR 信息，形成适合 Agent 执行的任务。 |
| `$wayfinder` | 快速熟悉陌生代码库，定位入口、关键模块、约束和变更路径。 |
| `$implement` | 按规格实施代码变更，完成验证并汇报结果。 |
| `$tdd` | 使用红—绿—重构流程，以测试驱动功能开发或缺陷修复。 |
| `$diagnosing-bugs` | 通过证据和最小实验定位复杂缺陷或性能回归的根因。 |
| `$prototype` | 构建一次性原型，快速验证接口、状态模型或技术可行性。 |
| `$codebase-design` | 设计更深的模块接口，改善抽象、封装和职责边界。 |
| `$domain-modeling` | 统一领域术语、实体、规则和上下文边界。 |
| `$improve-codebase-architecture` | 系统评估并逐步改善代码库架构和模块组织。 |
| `$code-review` | 从规范符合度和缺陷风险两方面审查代码变更。 |
| `$resolving-merge-conflicts` | 安全处理正在进行的 Git merge/rebase 冲突并验证结果。 |
| `$research` | 针对技术问题检索高可信一手资料，并在仓库生成研究记录。 |
| `$handoff` | 生成覆盖环境、路径、状态、待办和安全约束的 Agent 交接文档。 |
| `$teach` | 用循序渐进、可运行的示例讲解代码或技术概念。 |
| `$writing-great-skills` | 设计、编写和改进结构清晰、触发可靠的 Codex Skill。 |

### 17.3 学术研究套件（1）

| Skill | 作用 |
|---|---|
| `$academic-research-suite` | 统筹选题、检索、证据整理、学术写作、审稿、实验规划和 research-to-paper 流程。 |

### 17.4 科研、统计与量化计算（22）

| Skill | 作用 |
|---|---|
| `$aeon` | 时间序列分类、回归、聚类、预测、异常检测和相似度检索。 |
| `$dask` | 将 pandas/NumPy 工作流扩展到并行或超内存数据处理。 |
| `$experimental-design` | 在采集数据前设计随机化、分组、区组、因子和序贯实验。 |
| `$exploratory-data-analysis` | 对科学数据进行结构、质量、分布和异常的系统探索。 |
| `$matplotlib` | 创建可精细控制并可导出 PNG、PDF、SVG 的静态图表。 |
| `$networkx` | 创建、分析和可视化复杂网络、关系图和路径结构。 |
| `$pymc` | 构建贝叶斯、层次模型，执行 MCMC、后验预测和模型比较。 |
| `$pymoo` | 使用 NSGA-II、NSGA-III、MOEA/D 等算法解决多目标优化问题。 |
| `$pytorch-lightning` | 用 LightningModule 和 Trainer 组织、训练及扩展深度学习实验。 |
| `$research-grants` | 按资助机构要求撰写科研项目申请书和评审导向材料。 |
| `$scientific-brainstorming` | 开展开放式科研创意生成、跨学科连接和假设探索。 |
| `$scientific-critical-thinking` | 评价科学主张、实验设计、偏差、混杂因素和证据质量。 |
| `$scientific-visualization` | 设计期刊级多面板科研图、显著性标注和统一出版风格。 |
| `$scikit-learn` | 完成监督/无监督学习、预处理、管线、调参和模型评估。 |
| `$simpy` | 构建队列、物流、生产和资源竞争等离散事件模拟。 |
| `$stable-baselines3` | 使用 PPO、SAC、DQN、TD3、DDPG、A2C 开展强化学习实验。 |
| `$statistical-analysis` | 选择统计检验、检查假设、计算效应量并解释结果。 |
| `$statistical-power` | 进行样本量、统计功效、最小可检测效应和敏感性分析。 |
| `$statsmodels` | 使用 OLS、GLM、混合模型、ARIMA 等模型进行统计推断。 |
| `$sympy` | 执行精确符号代数、微积分、方程求解和代码生成。 |
| `$transformers` | 加载 Hugging Face 模型，执行推理、文本生成和 Trainer 微调。 |
| `$usfiscaldata` | 查询美国财政部 Fiscal Data API 的国债、收入和支出数据。 |

### 17.5 金融服务与股票研究（16）

| Skill | 作用 |
|---|---|
| `$3-statement-model` | 填充并联动利润表、资产负债表和现金流量表三张财务报表。 |
| `$audit-xls` | 审计 Excel 的公式准确性、错误引用、硬编码和常见建模问题。 |
| `$clean-data-xls` | 清理表格空格、大小写、数字文本、日期和重复记录。 |
| `$competitive-analysis` | 构建竞争格局、公司深度比较、市场定位和战略分析。 |
| `$comps-analysis` | 制作可比公司分析，计算运营指标、估值倍数和统计基准。 |
| `$dcf-model` | 基于财务数据、假设和折现率建立现金流折现估值模型。 |
| `$earnings-analysis` | 分析季度业绩、预期差、指引、关键指标和估值影响。 |
| `$earnings-preview` | 在财报发布前建立一致预期、情景分析和重点观察指标。 |
| `$initiating-coverage` | 通过分阶段流程制作机构级首次覆盖股票研究报告。 |
| `$lbo-model` | 构建杠杆收购模型，分析融资结构、债务偿还和投资回报。 |
| `$model-update` | 用新财报、管理层指引或宏观假设更新现有财务模型。 |
| `$morning-note` | 汇总隔夜事件、交易观点、催化剂和覆盖公司变化。 |
| `$sector-overview` | 制作行业规模、结构、趋势、竞争格局和主要公司的概览。 |
| `$thesis-tracker` | 持续维护多空投资逻辑、关键数据、催化剂和失效条件。 |
| `$catalyst-calendar` | 维护财报、会议、产品发布、监管决定等事件催化日历。 |
| `$idea-generation` | 结合量化筛选、主题研究和模式识别生成多头或空头候选。 |

### 17.6 快速核验与维护

统计当前用户级 Skill 数量：

```zsh
find /Users/lz666/.codex/skills -mindepth 2 -maxdepth 2 -name SKILL.md | wc -l
```

列出 Skill 名称：

```zsh
find /Users/lz666/.codex/skills -mindepth 2 -maxdepth 2 -name SKILL.md -print | sort
```

新增、删除或升级 Skill 后，应同步更新本节的核验日期、总数、来源数量及清单；不要把 `/Users/lz666/.codex/skills/.system/` 中的 Codex 内置技能计入上述 61 个用户级 Skill。
