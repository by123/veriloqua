# Veriloqua

[English](README.md) | 简体中文

**一个自我改进的翻译引擎，理解人们真正想表达的意思。**

Veriloqua 翻译的是*语义、语气、语域与文化效果*，而不是逐词替换——并且**从每一次更正中学习**：一个确定性守卫会在同一语境下的精确 span 命中上，阻止已被更正的错误译法再次出现。

**默认零配置。** `pip install veriloqua` 即可使用——**无需 API key，无需任何设置**：

- **`fast`** 直接调用 Google 公开翻译接口（免密钥）。
- **`auto`**（默认）通过**本机已登录的 Claude Code CLI**（`claude -p`）以后台子进程方式运行分层 LLM 级联——无 API key、无厂商 SDK。没有可用 LLM 时自动降级到免密钥的 fast 路径。

```bash
pip install veriloqua
```

```python
import veriloqua

# 零配置：本机装有已登录的 `claude` CLI 就用它，否则走免密钥 Google
print(veriloqua.translate("Break a leg!", to="zh"))

tr = veriloqua.Translator()
r = tr.translate("Break a leg!", to="zh", mode="auto", domain="casual-chat")
print(r.text, r.confidence)

# 教一次——确定性守卫会在该语境下阻止这个错误译法
tr.correct(r.request_id, "祝你好运")
```

---

## 两种模式，一套 API

| 模式 | 做什么 | 需要什么 | 典型速度 |
|------|--------|----------|----------|
| **`fast`** | 直连 Google 翻译 + 不变式术语锁 | 无（免密钥） | <1s |
| **`auto`**（默认） | 分层 LLM 级联：琐碎短句走免密钥 MT（<1s，零 LLM 调用）→ Haiku 分诊 → Sonnet 翻译 + 自查 → 难例升级 Opus 深度裁决，全程叠加更正记忆与确定性拒绝守卫 | 已登录的 Claude Code CLI（否则降级到 fast） | 琐碎短句 <1s · 每个 LLM 层约 10-30s |

按调用选择：`translate(text, to="ja", mode="fast"|"auto")`。

### 速度预期（典型值，非保证）

走本机 **Claude Code CLI**（`claude -p`）时，每次调用都要冷启动一个完整 agent（约 7s），且模型思考时长随输入变化，因此各层延迟有波动——一个升级完三层的疑难习语可能耗时约 40s。此处的延迟是典型观测值，不是上限承诺。`VERILOQUA_PROGRESS=1` 可显示每次调用的进度，运行过程不会看起来像卡死。

## 更正记忆：教一次，确定性执行

更正一条翻译是一等公民的持久操作。执行逻辑在确定性代码里，不依赖模型行为——当执行无法生效时，结果会如实说明，而不是静默通过：

```python
r = tr.translate("the cloud", to="zh", domain="tech")   # 假设返回了"云朵"（错误）
tr.correct(r.request_id, "云端")                          # 给出正确译法
# 此后在 tech 语境下，"the cloud" → "云朵" 的精确 span 复现会被阻止。
```

- **一条更正同时保存你认可的译法与被拒绝的译法**（那个错误）。
- 下次翻译时，**确定性的规范化 span 扫描**会发现被更正片段的任何复现——出现在输入的任何位置都算，而不要求整篇文档完全相同。
- **生成后拒绝守卫**在模型再次产出被拒译法时将其改写，检测与替换使用同一套规范化匹配。若检测到无法改写的违例，结果会**降级（degraded），绝不静默 OK**。
- **承诺的边界：** 执行范围是同语境下的精确 span。错误的改写变体属于模型的职责（更正会注入提示词），不属于守卫的职责。
- **零 LLM 密钥可用。** `vq correct` 与确定性核心不需要任何 API key。

### 反过拟合设计

在闲聊语境学到的修正**不会**在法律合同里生效。更正默认落在**最窄（user）作用域**，只在作用域精确匹配的 key 上自动应用，并受*方向性*领域匹配的门控。跨越到全局/共享的"始终生效"锁需要人工显式执行 `vq lock`——命中次数本身永远不会自动提升规则。每条更正会自动生成一个**过拟合探针**，由 CI 断言它*不会*在错误语境下触发。

## 命令行

```bash
vq "任意语言的文本"                              # 不带 --to → 默认译为中文（zh）
vq "Hello, world" --to es                       # 指定目标语言
vq "The spirit is willing" -t ru -d literature          # auto 模式是默认值
echo "长文本" | vq --to fr --mode fast --json

vq correct <request_id> "更好的译法"            # 受信任的学习路径（无需密钥）
vq glossary add "New York" "纽约" --from en --to zh --invariant
vq memory stats | export backup.json | forget <id> | conflicts | purge-log
vq lock <entry_id> --scope global              # 人工显式提升作用域
vq eval                                        # 确定性的更正回放 / 过拟合门禁
vq eval --suite gold                           # 真实翻译 gold 集并报告句级 chrF（仅供参考）
vq backends                                    # 各后端当前是否可用
```

也可通过 `python -m veriloqua` 运行。

## 记忆模型

两层机制，方向相反：

- **术语锁**（确定性词汇表）：在*所有*模式下应用。不变式锁（专有名词、产品名、代码）通过掩码保护，能完整穿过机器翻译，不会被词形变化破坏。
- **更正**（语境化记忆）：在 auto 模式下检索并注入，受语境门控，并由确定性拒绝守卫在精确 span 命中上执行。

所有数据存放在**你自己拥有的本地 SQLite 文件**（`~/.local/share/veriloqua/memory.db`）——永不回传。检索优先级为 `user > project > global`。更正采用*取代*而非修改（通过 `superseded_by` 支持完整审计与回滚），部分唯一索引保证每个 key 至多一条活跃记录。

### 隐私

本地存储、最小 span 记录、脱敏、哈希，以及 `forget()` / `export`，是真实存在的机制。`correct-by-id` 由**有界、可关闭的请求日志环形缓冲区**支撑（默认保留最近 1000 条或 30 天）——不是对所有原文的永久留存。随时可用 `vq memory purge-log` 清空，或完全关闭日志（correct-by-text 仍然可用）。正则脱敏器会打码明显的敏感信息；我们不声称它能从自由文本中清除全部 PII。

## 配置

解析顺序：构造函数参数 → `VERILOQUA_*` 环境变量 → TOML（`~/.config/veriloqua/config.toml`）→ 内置默认值。

没有任何必填项。以下全部可选。

| 键 | 用途 |
|-----|------|
| `VERILOQUA_CLI_MODEL` | 传给 Claude CLI 的模型（`claude -p --model …`）；默认由 CLI 使用它自己配置的模型 |
| `VERILOQUA_TRIAGE_MODEL` / `VERILOQUA_TRANSLATE_MODEL` / `VERILOQUA_DEEP_MODEL` | 级联三层各自的模型 |
| `VERILOQUA_NO_THIRD_PARTY` | 硬性禁用免密钥 Google 路径（fast 模式默认允许） |
| `VERILOQUA_QUIET` | 关闭一次性的第三方提示 |
| `VERILOQUA_MAX_COST` / `VERILOQUA_MAX_CALLS` | 单次任务的预算上限 |

### 预算

每次请求都执行分段级**与**任务级上限（调用次数 / token / 墙钟时间 / 估算成本）。超限时引擎停止并返回目前最好的结果（或免密钥 fast 路径），标记为 degraded 并附明确原因。注意：`translate()` 每次调用只处理单个文本片段——没有多文档批处理管线；任务级上限约束的是一次请求内产生的调用。

## 后端

默认 LLM 路径是**本机已登录的 Claude Code CLI**（`claude -p`）——无需 `pip install` 任何东西、无需密钥。核心安装只依赖 `httpx`。以下均为可选增强：

```bash
pip install veriloqua[rapidfuzz]   # 更快的模糊召回（否则用标准库 difflib）
pip install veriloqua[eval]        # sacrebleu chrF，供 `vq eval --suite gold` 使用
pip install veriloqua[cli]         # 更美观的 CLI
pip install veriloqua[all]
```

自学习核心与 fast 路径除 Python 标准库外不依赖任何重型组件。

## 设计过程

Veriloqua 的架构出自一场五方设计辩论（语言学家、LLM 验证架构师、记忆/自学习专家、打包工程师、对抗性红队），先综合、再压力测试。最终确立的原则是：**执行逻辑存在于确定性代码中，而非模型行为**——可选的 ML 组件只用来改善召回，从不承载执行本身。

## 许可证

Apache-2.0。
