# Review AI

Review AI 是一个 AI 代码审查工具，支持 DeepSeek、OpenAI、通义千问、Kimi、GLM、Claude、本地 Ollama，以及任何 OpenAI 兼容网关（vLLM、one-api、企业代理等）。

它作为标准的 `reviewbot.tools` entry point 插件安装在 worker 侧，**不需要修改 Review Bot 或扩展的任何源码**。

- 只消费 `reviewai.1.0` 队列，可与官方各语言 worker 镜像并存
- 通过 **LangChain**（`ChatOpenAI` / `ChatAnthropic` + LCEL 链）调用模型
- 结果以**行内评论**回写 Review Board，并附带严重性标注和修复建议
- 大变更自动分批，成本可控

## 支持的 Provider

| `provider` | 名称 | 协议 | 默认端点 | 默认模型 | Key 环境变量 |
| --- | --- | --- | --- | --- | --- |
| `deepseek` | DeepSeek | OpenAI 兼容 | `https://api.deepseek.com` | `deepseek-flash` | `DEEPSEEK_API_KEY` |
| `openai` | OpenAI | OpenAI 兼容 | `https://api.openai.com/v1` | `gpt-4o-mini` | `OPENAI_API_KEY` |
| `qwen` | 阿里云通义千问 | OpenAI 兼容 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | `DASHSCOPE_API_KEY` |
| `moonshot` | 月之暗面 Kimi | OpenAI 兼容 | `https://api.moonshot.cn/v1` | `moonshot-v1-32k` | `MOONSHOT_API_KEY` |
| `zhipu` | 智谱 GLM | OpenAI 兼容 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` | `ZHIPUAI_API_KEY` |
| `ollama` | 本地 Ollama | OpenAI 兼容 | `http://localhost:11434/v1` | `qwen2.5-coder:14b` | 不需要 |
| `anthropic` | Anthropic Claude | Anthropic Messages | `https://api.anthropic.com` | `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| `custom` | 任意 OpenAI 兼容网关 | OpenAI 兼容 | 需填写 | 需填写 | `REVIEW_AI_CUSTOM_API_KEY` |

默认模型名仅作起点，请按厂商文档填写当前可用的模型。切换 provider 有三种粒度：

1. **每个集成配置独立选择**（推荐）：在 Review Board 的集成配置里改 `Provider`/`Model`，同一个 worker 可同时服务多个 provider。
2. **整个 worker 统一覆盖**：设置 `REVIEW_AI_MODEL`、`REVIEW_AI_BASE_URL`。
3. **只用一把钥匙**：`REVIEW_AI_API_KEY` 对所有 provider 生效，省去为每家配变量。

## 传输层（LangChain）

请求由 `review_ai/client.py` 发出，链结构为：

```text
ChatPromptTemplate(system, human) → ChatOpenAI / ChatAnthropic → 响应归一化
```

- OpenAI 兼容协议（DeepSeek、OpenAI、Qwen、Kimi、GLM、Ollama、custom）→ `langchain_openai.ChatOpenAI`
- Anthropic Messages → `langchain_anthropic.ChatAnthropic`
- JSON mode 以 `response_format={"type": "json_object"}` 传入；只有在错误信息确实提到 `response_format` / `json_object`（或返回 HTTP 422）时才回退重试，避免把上下文超限、配额不足误判成“不支持 JSON mode”
- 响应归一化保留原有语义：`finish_reason=length` 且无正文时，报“输出预算被推理 token 耗尽”，而不是含糊的空响应

provider 解析（`REVIEW_AI_API_KEY` / `REVIEW_AI_BASE_URL` / `REVIEW_AI_MODEL`）、超时与重试次数仍然由插件统一处理，再传给底层模型。

## 工作流程

```
Review Board 发布 review request
        │
        ▼
ReviewBotIntegration 匹配条件 → StatusUpdate → send_task(queue="reviewai.1.0")
        │
        ▼
worker：取 diff → 拼提示词 → 调所选 provider → 解析 JSON → File.comment()
        │
        ▼
ReviewBotReviewResource 一次性发布 review，StatusUpdate → done-success（页面显示 passed.）/ done-failure（页面显示 error.）
```

工具上报给 Review Board 的元数据（`name`/`version`/`tool_options`）会被扩展用于动态渲染集成配置表单，因此 `provider`、`model` 等选项会直接出现在 Review Board 后台。

## 快速开始（Docker）

worker 需要一个可用的 broker（RabbitMQ 或 Redis）。没有现成环境时可以这样起一个：

```bash
docker run -d --name reviewbot-rabbitmq \
    -e RABBITMQ_DEFAULT_USER=reviewbot \
    -e RABBITMQ_DEFAULT_PASS=reviewbot123 \
    -e RABBITMQ_DEFAULT_VHOST=reviewbot \
    -p 5672:5672 -p 15672:15672 \
    rabbitmq:3.13-management-alpine
```

构建并运行 worker：

```bash
cd plugins/review-ai

docker build -t review-ai:1.0 .

docker run -d --name review-ai \
    -e BROKER_URL=amqp://reviewbot:reviewbot123@127.0.0.1:5672/reviewbot \
    -e REVIEW_AI_API_KEY=sk-xxxxxxxx \
    -e NUM_WORKERS=2 \
    -v "$PWD/config:/config" \
    review-ai:1.0

docker logs -f review-ai
```

`REVIEW_AI_API_KEY` 可用任意 provider 的 key；也可以改用 `DEEPSEEK_API_KEY` 等厂商专属变量。启动日志里应出现 `The following tools are available:` 且包含 `reviewai`。若出现在 `missing dependencies` 列表中，说明插件没有被 worker 正确加载。

固定 worker 版本以获得可复现构建。**版本必须与 Review Board 镜像内扩展的版本一致**（官方 `beanbag/reviewboard:7.0` 是 4.0）：

```bash
docker build --build-arg REVIEWBOT_VERSION=4.0 -t review-ai:4.0 .
```

### worker 环境变量

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `BROKER_URL` | 是 | AMQP / Redis 地址。未设置时容器直接退出并打印 `error: BROKER_URL is not set` |
| `REVIEW_AI_API_KEY` | 视 provider | 对所有 provider 生效的 key；也可改用 provider 专属变量 |
| `REVIEW_AI_BASE_URL` | 否 | 覆盖所有 provider 的端点 |
| `REVIEW_AI_MODEL` | 否 | 覆盖所有 provider 的模型 |
| `LOG_LEVEL` | 否 | 默认 `INFO`，映射到 `reviewbot -l` |
| `NUM_WORKERS` | 否 | 设置后映射到 `reviewbot -c`（Celery 并发数） |
| `REVIEWBOT_CONFIG_FILE` | 否 | 可选的 Python 配置文件路径 |

容器以 root 启动时会先修正 `/config`、`/repos` 的属主，再用 `gosu` 降权到 UID 1001 的 `reviewbot` 用户。

### 与 Review Board 的 docker-compose 部署集成

仓库的 `contrib/docker/examples/docker-compose.postgres.yaml` 已加入 `review-ai` 服务，直接构建本目录：

```bash
cd contrib/docker/examples

export REVIEW_AI_API_KEY=sk-...
export REVIEWBOT_VERSION=4.0     # 必须与 RB 镜像内扩展版本一致
docker compose -f docker-compose.postgres.yaml up -d --build review-ai
```

> **worker 与扩展的版本必须一致。** 4.1 的 worker 在回写 review 时会发送 `to_owner_only` 字段，4.0 的扩展会以 `400 Invalid Form Data` 拒绝，状态最终显示 `internal error.`。官方 RB 7.0 镜像内置扩展 4.0，因此 worker 也要固定 `REVIEWBOT_VERSION=4.0`。

启动后 worker 日志应包含 `The following tools are available: * reviewai`，然后在扩展配置页点击 **Refresh Installed Tools** 即可在集成配置里选择 **AI-Reviewer**。

### 网络受限环境

基础镜像与 PyPI 源都做成了构建参数，便于走内网镜像站：

```bash
docker build \
    --build-arg PYTHON_BASE_IMAGE=docker.1ms.run/library/python:3.12-slim \
    --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    -t review-ai:1.0 .
```

构建完成后可以自检（不启动 worker）：

```bash
docker run --rm --entrypoint python review-ai:1.0 -c \
  "from reviewbot.tools.base.registry import load_tool_classes, get_tool_class; \
   load_tool_classes(); print(get_tool_class('reviewai').name)"

docker run --rm review-ai:1.0 id -u   # 应为 1001，表示已降权
```

### 本地开发

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .

python -m unittest discover -s tests -t .
pytest tests -q               # 可选，需要额外安装 pytest
```

也可直接运行 `python -m unittest discover -s tests -t . -v` 查看逐条结果；`LiveProviderTests` 默认跳过。

验证插件可被发现（不启动 worker，也不连接 broker）：

```bash
python -c "from reviewbot.tools.base.registry import load_tool_classes, get_tool_class; load_tool_classes(); print(get_tool_class('reviewai'))"
```

## 在 Review Board 中启用

1. 确认 Review Bot 扩展已启用（官方 Docker 镜像已预装）：

   ```bash
   docker compose exec reviewboard rb-site manage /site \
       enable-extension reviewbotext.extension.ReviewBotExtension
   ```

2. 在扩展配置页填写 broker 地址与 bot 用户，并点击 **Refresh Installed Tools**，让 worker 上报工具清单。后台 `Tools` 列表中应出现 `AI-Reviewer 1.0`（插件内 `ReviewAITool.name`）。

3. 进入 **Administration → Integrations → Review Bot → Add integration**：
   - `Tool` 选择 **AI-Reviewer**
   - `Provider` 选择模型厂商；`Model` 留空则用该厂商默认模型
   - 用 `Conditions` 限定触发范围（例如仓库或分支），避免每个变更都触发 LLM 调用
   - 按需调整 `Maximum findings per file`、`Minimum severity`、`Maximum files`

4. 发布一个带 diff 的 review request，状态条会出现 `AI-Reviewer`，完成后变为 `passed.` 或 `failed.` 并附评论。

## 工具选项

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `provider` | `deepseek` | 模型厂商，见上表 |
| `api_key` | 空 | 仅作为兜底。**建议留空**，改用 worker 环境变量，避免密钥入库 |
| `model` | 空 | 留空使用 provider 默认模型 |
| `base_url` | 空 | 留空使用 provider 默认端点；填了可用于代理或私有网关 |
| `min_severity` | `low` | 低于该级别的发现被丢弃，`medium`/`high` 可显著降噪 |
| `language` | `chinese` | 输出语言，可选 `chinese` / `english`。同时影响模型生成的 summary/标题/正文，以及工具自己写的结论摘要与“建议修复”标签 |
| `max_findings_per_file` | `5` | 单文件最多评论数 |
| `max_files` | `20` | 单次审查最多文件数，超出部分跳过 |
| `max_file_lines` | `1500` | 单文件只发送前 N 行 |
| `max_prompt_chars` | `48000` | 单次请求的字符上限（约 4 字符 = 1 token），超出则分批 |
| `max_output_tokens` | `32768` | 单次响应 token 预算。推理模型（`deepseek-flash`、`deepseek-v4-pro`）的思维链也占用该预算 |
| `request_timeout` | `500` | 单次 API 请求超时秒数 |
| `extra_instructions` | 空 | 项目专属提示，例如“忽略测试文件中的断言风格” |

工具级超时为 600 秒（Review Board 状态条使用的超时）。

## 成本与安全

- 只把**文本文件内容**发给所选厂商；二进制、删除文件、空文件会被跳过。
- 提示词要求模型只对本次改动的行（`>` 标记）报告问题；Review Bot 在默认配置下也会自动丢弃落在未修改行的评论。
- 默认 `max_files=20`、`max_prompt_chars=48000`，单次审查的输入规模有上限。
- API key 通过环境变量注入，不要写进 `docker-compose.yaml` 或镜像。
- 代码内容会离开内网。若代码敏感，选择 `ollama` + 内网模型，或把 `base_url` 指向公司私有网关。
- 不同厂商的数据合规策略不同，选型时请自行确认。

## 排障

| 现象 | 排查方向 |
| --- | --- |
| 工具列表中没有 `AI-Reviewer` | 容器内 `pip show review-ai`；日志中搜索 `reviewai`；重新点击 Refresh Installed Tools |
| 状态一直是 `pending` | worker 未订阅 `reviewai.1.0`。检查启动日志的 `The following tools are available` 是否有 `reviewai` |
| 状态变为 `error` | 常见原因是 key 未设置、模型名不存在、配额不足、网络不通。日志中会显示 `... API returned HTTP ...` |
| 有 `pending` 但无任何日志 | worker 无法解析 Review Board 站点域名，回调失败。给 RB 容器在 worker 所在网络加 `aliases` |
| 评论为 0 但显示 `passed.` | 模型没发现问题，或发现被 `min_severity`、未修改行过滤、单文件上限丢弃。降低 `min_severity` 后重跑 |
| `AI-Reviewer internal error` + 日志 `to_owner_only: Field is not supported` | worker 与扩展版本不一致。把 `REVIEWBOT_VERSION` 固定成与 RB 镜像内扩展相同的版本后重建 |
| 日志报 `stopped at the max_tokens limit` | 推理模型把预算耗在思维链上。调大 `max_output_tokens`，或减小 `max_prompt_chars` 让单批更小 |
| 网关报 `response_format` 不支持 | 客户端会自动去掉 JSON 模式重试一次，无需处理；日志里会有一条 warning |
| 评论全部缺失、日志提示找不到 JSON | 模型返回被截断或不完整。调大 `max_output_tokens`、减小 `max_prompt_chars`，或换更稳定的模型 |

失败时工具会把详细报告写入 Review Bot 的「Tool console output」附件（API 调用异常时该附件不会上传，需查看 worker 日志）。

## 已知限制

- 不做跨文件调用链分析，只基于单文件上下文；复杂项目的深层问题可能漏报。
- 大文件只发送前 `max_file_lines` 行，超出部分不会被审查。
- 模型输出为非确定性结果，同一 diff 多次运行可能给出不同评论；`temperature` 固定为 0 以尽量减少波动。
- 推理模型（`deepseek-flash`、`deepseek-v4-pro`）的思维链与最终 JSON 共享 `max_output_tokens` 预算。预算耗尽时工具会明确报 `error`（而不是静默判为通过），此时调大该选项或减少单次文件数。
- 仅支持 Review Bot 4.0 及以上（依赖 `BaseTool.settings` / `in_task` 新接口）。
- 需要 Python 3.10 及以上（LangChain 1.x 的要求）；默认镜像基于 `python:3.12-slim`，无需调整。依赖 `langchain-core` / `langchain-openai` / `langchain-anthropic`。
- 协议层面只实现了 OpenAI 兼容与 Anthropic Messages 两种；其他私有协议需要通过 `custom` + 兼容网关接入。

## 从 reviewbot-deepseek 迁移

早期名称为 `reviewbot-deepseek`（tool ID `deepseek`，队列 `deepseek.1.0`），该包已改名为 `review-ai`：

```bash
pip uninstall reviewbot-deepseek
pip install -e .        # 或者 docker build -t review-ai:1.0 .
```

worker 重启后点击 **Refresh Installed Tools**，然后在集成配置里把工具重新选为 `AI-Reviewer`。旧的 `Tool` 记录（tool ID `deepseek`）不会自动清理，可在后台禁用或删除。
