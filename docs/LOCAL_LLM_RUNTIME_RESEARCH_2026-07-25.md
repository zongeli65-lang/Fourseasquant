# Apple M5 24GB 本地大模型运行方案调研

> 版本：2026-07-25
> 用途：为产业链、供需与龙头候选 Agent 选择本地模型运行框架与初始资源边界。
> 调研范围：Qwen3-14B、Qwen3.5-9B、Qwen3.6-27B；MLX-LM、llama.cpp、Ollama。
> 证据边界：模型与框架事实仅使用模型发布方、Apple/MLX、llama.cpp、Ollama 的官方资料或官方源代码；内存数值推导单列为“工程估算”。
> 本轮限制：未安装软件、未下载模型、未读取秘密、未启动模型服务。

术语说明：Agent指智能体；LLM指大语言模型；CPU指中央处理器；GPU指图形处理器；HTTP指超文本传输协议；API指应用程序接口；JSON指结构化数据格式；Schema指结构约束；K/V指注意力机制中的键/值缓存；MLX指Apple面向苹果芯片的机器学习框架；GGUF指llama.cpp生态常用的通用大模型文件格式；Q4_K_M指一种四位混合量化格式。

## 一、结论先行

1. **不能从参数量或厂商通用榜单直接断言哪一款模型最适合本工程。**最终模型必须使用同一批产业链供需案例逐个测试；任一时刻只加载一个模型。
2. **运行框架的第一候选是 Ollama，模型第一阶段仍按已确认方案测试 Qwen3-14B。**原因不是断言它能力最好，而是 Ollama 已提供本地接口、工具调用、结构化输出和模型生命周期管理，工程接入成本最低；官方库中的 `qwen3:14b` 是9.3GB的 Q4_K_M（四位混合量化）文件。[Ollama Qwen3-14B 模型页](https://ollama.com/library/qwen3:14b)
3. **MLX-LM 适合作为 Apple Silicon（苹果芯片）性能对照和后续定制入口，但不建议直接把其内置 HTTP 服务器作为生产边界。**MLX-LM 官方明确写明服务器只实现基础安全检查，不建议生产使用；如果它最终胜出，应由本工程的 FastAPI（快速应用程序接口框架）服务封装、校验和限制工具权限。[MLX-LM 服务器说明](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md)
4. **llama.cpp 是控制力最强的 GGUF（通用大模型文件）备选。**它原生重视 Apple Silicon，支持多档权重量化、K/V（键值）缓存量化、OpenAI风格接口、工具调用和 JSON Schema（结构化数据约束）；但模型文件、启动参数和版本兼容需要工程自行管理。[llama.cpp 官方说明](https://github.com/ggml-org/llama.cpp)、[llama-server 官方说明](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
5. **Qwen3.6-27B 不应作为24GB机器的首装生产模型。**官方 Ollama Q4_K_M 文件已是17GB，MLX标签文件是20GB；这还不包括系统、运行缓冲、视觉组件运行态和上下文缓存。它只应在14B与9B均未达到验收线时，使用短上下文做后置压力测试。[Ollama Qwen3.6-27B Q4_K_M](https://ollama.com/library/qwen3.6:27b)、[Ollama Qwen3.6-27B MLX](https://ollama.com/library/qwen3.6:27b-mlx)
6. **生产接口必须将模型限制为“提出工具调用”，不能让模型直接执行终端命令。**联网检索、只读数据库查询、证据保存和候选提交由本工程的允许列表工具执行；模型输出的工具名和参数必须经过服务端校验。

## 二、本机与工程现状

### 2.1 本轮只读核验

| 项目 | 结果 | 证据性质 |
|---|---:|---|
| CPU架构 | ARM64 | 本机命令核验 |
| 芯片 | Apple M5 | 本机 `system_profiler` 过滤核验 |
| 统一内存 | 24GB | 本机 `system_profiler` 过滤核验 |
| 数据盘可用空间 | 约823GiB | 本机 `df` 核验 |
| `ollama` | 当前命令路径未发现 | 本机 `command -v` 核验 |
| `mlx_lm` | 当前命令路径未发现 | 本机 `command -v` 核验 |
| `llama-cli` / `llama-server` | 当前命令路径未发现 | 本机 `command -v` 核验 |

“命令路径未发现”只表示当前 Shell（命令行解释器）环境中不可调用，不能代替全盘安装审计。

### 2.2 与现有工程的接口适配

本工程当前使用 Python 3.14、FastAPI 和 Uvicorn（异步网络服务器），见仓库 `pyproject.toml`。因此最浅的集成边界是：

```text
事件发现/任务队列
        ↓
产业链 Agent 编排器（本工程）
        ↓ HTTP，仅本机回环地址
本地模型服务（Ollama 或 llama-server）
        ↓ 返回工具调用，不直接执行
允许列表工具网关
  ├─ 联网搜索
  ├─ 只读本地数据
  ├─ 证据快照
  └─ 候选结果提交
```

Ollama 官方支持原生 `/api/chat`，并兼容部分 OpenAI API；`/v1/chat/completions` 支持 `tools`、`response_format` 等字段。[Ollama OpenAI兼容说明](https://docs.ollama.com/api/openai-compatibility) llama-server 也提供 OpenAI风格的聊天接口、函数调用和受 JSON Schema 约束的输出。[llama-server 官方说明](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## 三、三个模型的官方事实

### 3.1 模型结构与公开上下文

| 模型 | 官方参数与结构 | 官方上下文上限 | 与本任务有关的官方能力 |
|---|---|---|---|
| Qwen3-14B | 14.8B参数、40层、40个查询头、8个K/V头 | 原生32,768；YaRN（长上下文扩展方法）可到131,072 | 支持思考/非思考模式、100多种语言、工具调用与Agent能力 |
| Qwen3.5-9B | 9B语言模型、32层、混合 Gated DeltaNet（门控增量网络）与注意力结构、带视觉编码器 | 原生262,144；可扩至1,010,000 | 原生视觉语言、201种语言、Agent与工具相关能力 |
| Qwen3.6-27B | 27B语言模型、64层、混合 Gated DeltaNet 与注意力结构、带视觉编码器 | 原生262,144；可扩至1,010,000 | 视觉语言、Agent编码、保留历史思考上下文 |

来源：[Qwen3-14B 官方模型卡](https://huggingface.co/Qwen/Qwen3-14B)、[Qwen3.5-9B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)、[Qwen3.6-27B 官方模型卡](https://huggingface.co/Qwen/Qwen3.6-27B)。

必须区分：

- “模型支持26万上下文”是**架构/训练上限**；
- “24GB机器能稳定分配多少上下文”是**部署资源问题**；
- “本工程需要多少上下文”是**任务设计问题**。

三者不能混为一谈。Qwen3-14B 官方还明确指出，平均上下文不超过32,768时不建议启用 YaRN，因为静态扩展可能损害短文本表现。[Qwen3-14B 长文本说明](https://huggingface.co/Qwen/Qwen3-14B#processing-long-texts)

Qwen3.5官方建议：发生内存不足时缩短上下文，但为保持复杂任务的思考能力，建议尽量保留至少128K。这是发布方对理想服务环境的建议，不代表24GB本机必须或一定能够稳定达到128K。[Qwen3.5-9B 服务说明](https://huggingface.co/Qwen/Qwen3.5-9B#serving-qwen35)

### 3.2 Ollama官方文件的实际大小

| 模型标签 | 官方库文件大小 | 格式/量化披露 | 24GB工程定位 |
|---|---:|---|---|
| `qwen3:14b` | 9.3GB | 14.8B，Q4_K_M | 第一阶段主候选 |
| `qwen3.5:9b` | 6.6GB | 9.65B，Q4_K_M | 第二阶段效率对照 |
| `qwen3.5:9b-mlx` | 8.9GB | MLX标签；页面未写明与Q4_K_M等价的量化精度 | Apple原生路线对照 |
| `qwen3.6:27b` | 17GB | 27.8B，Q4_K_M | 后置短上下文压力测试 |
| `qwen3.6:27b-mlx` | 20GB | MLX标签；页面未写明与Q4_K_M等价的量化精度 | 不建议在24GB上作为生产首选 |

来源：[Qwen3-14B](https://ollama.com/library/qwen3:14b)、[Qwen3.5-9B](https://ollama.com/library/qwen3.5:9b)、[Qwen3.5-9B MLX](https://ollama.com/library/qwen3.5:9b-mlx)、[Qwen3.6-27B](https://ollama.com/library/qwen3.6:27b)、[Qwen3.6-27B MLX](https://ollama.com/library/qwen3.6:27b-mlx)。

这些是**磁盘文件大小，不是运行时内存总量**。不同格式的文件大小也不能直接推导能力高低；尤其 MLX 标签页没有给出与 Q4_K_M 完全相同的量化声明，不能把两者当作严格的同精度实验。

## 四、内存与上下文工程估算

本节全部是根据官方结构参数和公开文件大小进行的**工程估算**，不是发布方给出的实测承诺。正式选择必须在本机记录峰值内存、交换内存、首字延迟和生成速度。

### 4.1 为什么四位权重大小不等于总内存

运行时至少同时占用：

```text
总占用 ≈ 模型权重
       + K/V上下文缓存
       + 运行图与临时缓冲
       + 分词器/视觉编码器运行态
       + 模型服务进程
       + macOS及其他工程服务
```

Apple MLX 使用统一内存，CPU与GPU数组共享内存而无需显式复制，这是其官方设计特征；但共享也意味着模型会和 macOS、数据库、浏览器及本工程争用同一套24GB物理内存。[Apple MLX 官方说明](https://github.com/ml-explore/mlx)

### 4.2 仅标准注意力K/V缓存的理论估算

假设K/V缓存使用 `f16`（16位浮点数），且不计 Gated DeltaNet 状态、视觉输入、框架缓冲和并发副本：

```text
每个标记的K/V字节数
= 注意力层数 × 2(K和V) × K/V头数 × 单头维度 × 2字节
```

| 模型 | 推导依据 | 每标记约占用 | 典型上下文理论占用 |
|---|---|---:|---:|
| Qwen3-14B | 40层 × 8个K/V头 × 128维 | 160KiB | 8K约1.25GiB；16K约2.5GiB；32K约5GiB |
| Qwen3.5-9B | 32层中约8个完整注意力层 × 4个K/V头 × 256维 | 32KiB | 32K约1GiB；64K约2GiB；128K约4GiB |
| Qwen3.6-27B | 64层中约16个完整注意力层 × 4个K/V头 × 256维 | 64KiB | 8K约0.5GiB；16K约1GiB；32K约2GiB；64K约4GiB |

这是便于容量规划的下界式估算。Qwen3.5/3.6还有线性注意力/循环状态，实际框架可能采用不同缓存布局；最终只能以进程实测为准。

### 4.3 24GB上的建议初始上下文

| 模型 | 初始测试 | 通过稳定性后再试 | 不应直接尝试 |
|---|---:|---:|---:|
| Qwen3-14B Q4 | 8K、单请求 | 16K；必要时32K | 直接启用131K YaRN |
| Qwen3.5-9B Q4 | 32K、单请求 | 64K，再评估128K | 未测试即把26万设为生产值 |
| Qwen3.6-27B Q4 | 4K或8K、单请求 | 仅在无交换内存恶化时试16K | 32K以上生产常驻 |

对14B，这一设置偏保守，是因为官方 Ollama 文件9.3GB加上32K的理论 `f16` K/V缓存约5GiB后，尚未计系统与运行缓冲。对27B，即使8K的标准注意力缓存估算只有约0.5GiB，17GB权重也已使24GB余量非常有限。

### 4.4 缓存量化与并发约束

- Ollama默认K/V缓存是 `f16`；官方支持 `q8_0`（约为 `f16` 一半内存）和 `q4_0`（约为四分之一内存），但也明确提示量化可能损害质量，尤其高GQA（分组查询注意力）模型需实测。[Ollama K/V缓存说明](https://docs.ollama.com/faq#how-can-i-set-the-quantization-type-for-the-kv-cache)
- llama.cpp 可分别指定K与V缓存类型，支持 `f16`、`q8_0`、`q4_0` 等，并默认使用 `f16`。[llama-server 参数说明](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- Ollama官方说明并行请求会按并行数放大上下文内存；24GB生产配置应固定**只加载一个模型、单请求推理，其余事件在工程队列排队**。[Ollama 并发说明](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests)

建议先用 `f16` K/V缓存建立质量基线，只有内存不够时才对照 `q8_0`；不应直接用 `q4_0`缓存换取看似更大的上下文，因为本项目更重视证据忠实与工具参数准确性。

## 五、三种运行框架比较

### 5.1 MLX-LM

#### 官方事实

- MLX由 Apple机器学习研究团队提供，针对 Apple Silicon，支持CPU、GPU和统一内存。[MLX 官方仓库](https://github.com/ml-explore/mlx)
- MLX-LM支持模型生成、Hugging Face（模型托管平台）接入、权重量化、LoRA（低秩适配微调）以及 Python接口。[MLX-LM 官方仓库](https://github.com/ml-explore/mlx-lm)
- Qwen官方确认 Qwen3.6 在 Apple Silicon 上同时受 `mlx-lm`（纯文本）与 `mlx-vlm`（视觉加文本）支持；Qwen3模型卡也列出 MLX-LM 本地支持。[Qwen3.6 官方仓库](https://github.com/QwenLM/Qwen3.6#local-use)、[Qwen3-14B 模型卡](https://huggingface.co/Qwen/Qwen3-14B)
- 当前 MLX-LM 源代码已有工具模板与工具调用解析路径，但正式服务器文档没有提供与 Ollama/llama-server 同等清晰的 JSON Schema 输出契约。[MLX-LM 当前服务器源代码](https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/server.py)
- MLX-LM官方明确标注内置服务器不建议生产使用，因为只有基础安全检查。[MLX-LM 服务器说明](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md)

#### 工程判断

优点：

- 最贴近 Apple Silicon，便于充分利用M5和统一内存；
- Python内嵌灵活，未来做LoRA或定制缓存策略时路径清晰；
- 可作为模型能力和性能的参考运行时。

缺点：

- 若直接嵌进现有 Python 3.14 服务，模型内存泄漏或崩溃会和业务API同进程；
- 内置服务器的安全与结构化输出能力不适合直接承担生产边界；
- 需自行实现进程隔离、健康检查、JSON校验、工具参数校验和重启。

适合定位：**性能对照、研究、后续定制；若生产使用，必须置于受控独立进程后。**

### 5.2 llama.cpp

#### 官方事实

- Apple Silicon是 llama.cpp 的“一等支持平台”，使用 ARM NEON、Accelerate 与 Metal（苹果图形计算框架）优化。[llama.cpp 官方仓库](https://github.com/ggml-org/llama.cpp)
- 支持1.5至8位权重量化、GGUF模型、CPU/GPU混合推理。[llama.cpp 官方仓库](https://github.com/ggml-org/llama.cpp)
- `llama-server`提供 OpenAI风格聊天接口、函数调用、受 JSON Schema 约束的输出、连续批处理、监控端点和多模态入口。[llama-server 官方说明](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- Qwen官方确认 llama.cpp 支持 Qwen3.6 的文本与视觉，并确认 Qwen3-14B 的 YaRN 可由 llama.cpp 使用。[Qwen3.6 官方仓库](https://github.com/QwenLM/Qwen3.6#llamacpp)、[Qwen3-14B 长文本说明](https://huggingface.co/Qwen/Qwen3-14B#processing-long-texts)

#### 工程判断

优点：

- 上下文、K/V缓存精度、GPU层数、输出语法约束可精细控制；
- C/C++独立进程，和现有 Python服务隔离；
- 对候选结果 JSON 的硬约束最透明。

缺点：

- 需要自行固定二进制版本、GGUF文件哈希、聊天模板和启动参数；
- Qwen新架构刚发布时，模板和工具解析可能比模型权重更新更快，必须做版本回归；
- 运维复杂度高于 Ollama。

适合定位：**需要精细内存调优、严格JSON语法或替换Ollama时的生产备选。**

### 5.3 Ollama

#### 官方事实

- macOS版支持 Apple M系列CPU与GPU。[Ollama macOS要求](https://docs.ollama.com/macos)
- 2026年起，Ollama在 Apple Silicon 上引入 MLX引擎，并明确利用统一内存和M5 GPU神经加速器；之后又同时增强了基于 llama.cpp 的GGUF兼容路线。[Ollama MLX公告](https://ollama.com/blog/mlx)、[Ollama GGUF公告](https://ollama.com/blog/improved-performance-and-model-support-with-gguf)、[Ollama MLX性能更新](https://ollama.com/blog/mlx-performance)
- Ollama支持单次、并行和多轮工具调用，工具只是模型提出的函数请求，实际执行仍由调用方代码完成。[Ollama 工具调用说明](https://docs.ollama.com/capabilities/tool-calling)
- Ollama支持在本地响应上强制 JSON Schema。[Ollama 结构化输出说明](https://docs.ollama.com/capabilities/structured-outputs)
- 默认绑定 `127.0.0.1:11434`，可保持仅本机访问；本地运行时官方称不会看到用户提示和数据。[Ollama FAQ](https://docs.ollama.com/faq)

#### 文档中的当前不一致

Ollama“上下文长度”页面写的是按显存分档自动选择4K/32K/256K，而FAQ仍写默认4K。[上下文长度页面](https://docs.ollama.com/context-length)、[FAQ上下文说明](https://docs.ollama.com/faq#how-can-i-specify-the-context-window-size)

工程上不能依赖默认值，必须：

1. 每个测试与生产版本显式记录 `num_ctx`；
2. 启动后用 `ollama ps` 核验实际上下文和处理器分配；
3. 把实际配置写入候选结果的模型版本元数据。

#### 工程判断

优点：

- 同时覆盖官方库、模型管理、HTTP接口、工具调用、结构化输出和生命周期；
- 现有 FastAPI 只需作为客户端调用本地回环地址；
- 可在同一编排接口下逐个测试14B、9B，必要时再测试27B；
- 模型默认仅保留内存一段时间，也可以通过 `keep_alive` 控制，便于事件驱动运行。[Ollama 模型驻留说明](https://docs.ollama.com/faq#how-do-i-keep-a-model-loaded-in-memory-or-make-it-unload-immediately)

缺点：

- 默认值随版本变化，必须固定版本并显式配置；
- GGUF与MLX标签不是同一量化实验，切换后要重新跑完整测试集；
- Ollama兼容的是“部分”OpenAI API，项目不应依赖未列入支持范围的字段。[Ollama OpenAI兼容说明](https://docs.ollama.com/api/openai-compatibility)

适合定位：**当前最合适的首个生产运行框架。**

## 六、工具调用、联网搜索与本地服务边界

模型本身不应拥有“联网后任意执行”的权限。推荐协议：

1. 工程把允许的工具以 JSON Schema 提供给模型；
2. 模型返回工具名与参数；
3. 工具网关检查：
   - 工具名是否在允许列表；
   - 参数类型、长度、域名、时间范围是否合规；
   - 本地查询是否只读；
   - 是否请求访问秘密、任意文件或终端；
4. 工程执行工具；
5. 对网页结果保存来源、发布时间、采集时间、哈希，以及模型认为最有用的证据；
6. 把结果作为 `tool` 消息返回模型；
7. 最终候选输出再经过本地 JSON Schema 与业务规则校验。

第一阶段允许列表建议只有：

| 工具 | 权限 |
|---|---|
| `search_public_web` | 搜索公开、无需登录的来源 |
| `fetch_public_page` | 获取明确网址，限制协议、大小与超时 |
| `query_local_market_data` | 参数化只读查询 |
| `query_company_evidence` | 参数化只读查询 |
| `save_evidence_snapshot` | 写入专用证据区，禁止任意路径 |
| `submit_leader_candidates` | 写入待校验候选，不直接改历史快照 |
| `propose_knowledge_update` | 只生成新版本建议并通知用户 |

禁止提供：

- 任意Shell/终端命令；
- 任意文件读写；
- 数据库原始SQL；
- 浏览器账户、密钥、付费内容绕过；
- 删除或覆盖历史结果；
- 自动交易或最终投资决策。

## 七、建议的分阶段验证方案

### 阶段A：不下载前的准备

1. 冻结本次需求为数据契约；
2. 建立约30个历史产业链事件测试集；
3. 固定模型输入、工具模拟结果、结构化输出Schema和评分方法；
4. 记录运行框架版本、模型文件哈希、量化、上下文、K/V缓存精度、采样参数；
5. 向用户提交安装和下载清单，获得确认后才执行。

### 阶段B：先测Qwen3-14B

建议首测组合：

```text
运行时：Ollama
模型：qwen3:14b
权重：Q4_K_M，官方库文件9.3GB
并行：1
同时加载模型数：1
上下文：先8K，再16K
K/V缓存：先f16建立质量基线
服务：仅绑定127.0.0.1
```

必须实测：

- 供需事实抽取；
- 产业链利润传导；
- 主营业务真实性；
- 1至3只候选排序；
- 空选能力；
- 工具调用参数正确率；
- JSON Schema通过率；
- 峰值内存、交换内存、首字延迟、完整任务时间；
- 模型失败后只重试一次，不切换备用模型。

### 阶段C：再测Qwen3.5-9B

先使用与14B同为Q4_K_M的 `qwen3.5:9b`，保持运行框架、工具和评分一致，避免一开始把“模型差异”和“运行格式差异”混在一起。若9B质量接近而速度、内存明显更好，再单独比较 `qwen3.5:9b-mlx`；MLX标签必须视为一个新的运行版本，重新回放全部案例。

### 阶段D：有条件才测Qwen3.6-27B

只有14B与9B均未达到已确认验收线，才下载 `qwen3.6:27b`。先用4K/8K单请求测试是否：

- 无持续交换内存；
- 不造成系统明显失去响应；
- 20分钟内完成高优先级事件；
- 相比14B有足够大的任务准确率提升。

任一条件不满足即淘汰，不通过降低到极端量化来勉强上线。

### 阶段E：运行框架复赛

生产模型确定后，才比较：

- Ollama当前格式；
- 同模型的 MLX-LM四位版本；
- 必要时 llama.cpp GGUF版本。

这一步比较运行效率和接口可靠性，不再改变模型任务评分集。

## 八、推荐决策

### 8.1 当前推荐

> **一个生产模型、一个常驻模型服务、一个受控Agent编排器。先以 Ollama＋Qwen3-14B Q4_K_M 建立基线；随后逐个对照Qwen3.5-9B。Qwen3.6-27B只作为有条件的后置压力测试。**

推荐 Ollama 是因为它目前最完整地覆盖项目需要的本地API、工具调用、结构化输出和模型管理；不是因为已经证明它在M5上一定比 MLX-LM 或 llama.cpp更快。

推荐先测14B是遵守已确认的分阶段方案；不是断言14B一定强于更新的9B。模型发布方的通用榜单不能代替本工程产业链案例。

### 8.2 当前不建议

- 不同时加载9B与14B；
- 不首装27B；
- 不把26万或13万上下文作为24GB机器默认值；
- 不直接暴露 MLX-LM 内置服务器到局域网或公网；
- 不让模型直接执行联网内容中的命令；
- 不根据一次聊天体验决定模型；
- 不在不同模型间使用不同证据包或不同输出标准；
- 不在未经用户确认前安装软件或下载数十GB模型。

## 九、安装前需提交用户确认的清单

正式操作前应另行提交，不在本轮执行：

1. Ollama具体版本、来源、安装位置；
2. 第一阶段模型标签、量化、官方文件大小与预计缓存空间；
3. 模型存储目录；
4. 是否启用登录自启动；
5. 回环监听地址和端口；
6. 显式上下文、并发、模型驻留时间；
7. 预计新增Python客户端依赖；
8. 30个案例的测试范围与预计耗时；
9. 卸载和回退方法；
10. 安装后验证命令与验收记录路径。

## 十、官方来源索引

### 模型发布方

- [Qwen3-14B 官方模型卡](https://huggingface.co/Qwen/Qwen3-14B)
- [Qwen3.5-9B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)
- [Qwen3.6-27B 官方模型卡](https://huggingface.co/Qwen/Qwen3.6-27B)
- [Qwen3.5/3.6 官方仓库](https://github.com/QwenLM/Qwen3.6)

### Apple与MLX

- [MLX 官方仓库](https://github.com/ml-explore/mlx)
- [MLX-LM 官方仓库](https://github.com/ml-explore/mlx-lm)
- [MLX-LM HTTP服务器说明](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md)
- [MLX-LM 当前服务器源代码](https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/server.py)

### llama.cpp

- [llama.cpp 官方仓库](https://github.com/ggml-org/llama.cpp)
- [llama-server 官方说明](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

### Ollama

- [macOS支持](https://docs.ollama.com/macos)
- [上下文长度](https://docs.ollama.com/context-length)
- [工具调用](https://docs.ollama.com/capabilities/tool-calling)
- [结构化输出](https://docs.ollama.com/capabilities/structured-outputs)
- [OpenAI兼容接口](https://docs.ollama.com/api/openai-compatibility)
- [FAQ与运行配置](https://docs.ollama.com/faq)
- [Apple Silicon MLX引擎公告](https://ollama.com/blog/mlx)
- [GGUF与llama.cpp兼容更新](https://ollama.com/blog/improved-performance-and-model-support-with-gguf)
- [MLX性能更新](https://ollama.com/blog/mlx-performance)
- [Qwen3-14B 官方库文件](https://ollama.com/library/qwen3:14b)
- [Qwen3.5-9B 官方库文件](https://ollama.com/library/qwen3.5:9b)
- [Qwen3.5-9B MLX官方库文件](https://ollama.com/library/qwen3.5:9b-mlx)
- [Qwen3.6-27B 官方库文件](https://ollama.com/library/qwen3.6:27b)
- [Qwen3.6-27B MLX官方库文件](https://ollama.com/library/qwen3.6:27b-mlx)
