# Veriloqua

**A self-improving translation engine that understands what people really mean.**

Veriloqua translates for *meaning, tone, register, and cultural effect* — not word-swaps —
and **learns from every correction so it never repeats the same mistake in the same context**.

**Completely zero-config.** `pip install veriloqua` and go — **no API keys, no setup**:

- **Fast** uses Google's public translation endpoint directly (keyless).
- **Medium / High** run through a **locally logged-in agent CLI** — `claude -p` (Claude Code)
  or `codex exec` (Codex) — as a background subprocess. No `ANTHROPIC_API_KEY`, no SDK.
- **`auto`** (the default) picks the best available: the agent CLI if it's installed, otherwise
  the keyless fast path. Either way it just works.

```bash
pip install veriloqua
```

```python
import veriloqua

# Zero config: uses your logged-in `claude`/`codex` CLI if present, else keyless Google
print(veriloqua.translate("Break a leg!", to="zh"))

tr = veriloqua.Translator()
r = tr.translate("Break a leg!", to="zh", mode="high", domain="casual-chat")
print(r.text, r.confidence)

# Teach it once — it never repeats that mistake in this context again
tr.correct(r.request_id, "祝你好运")
```

> API keys are still supported (`pip install veriloqua[anthropic]` + `ANTHROPIC_API_KEY`,
> or `[openai]`) and take over automatically if you'd rather use the SDK — but they are never required.

---

## Three modes, one API

| Mode | What it does | Needs | Speed (API key) | Speed (agent CLI) |
|------|--------------|-------|-----------------|-------------------|
| **`fast`** | Google Translate directly | nothing (keyless) | <1s | <1s |
| **`medium`** | One grounded LLM pass + glossary/correction memory | agent CLI **or** API key | ~2–3s | ~10s (varies 9–24s) |
| **`high`** | 2 candidates → judge picks the best → deterministic reject-guard | agent CLI or API key | ~8–10s | ~20–28s |

Pick per call: `translate(text, to="ja", mode="fast"|"medium"|"high"|"auto")`. `auto` (the default)
answers trivial short inputs ("你好", "thank you") instantly via the keyless fast path — zero LLM
calls — and routes everything else through the tiered cascade (Haiku triage → Sonnet translate →
Opus deep judgment on hard cases). With no LLM available it degrades to the keyless fast path.

### Speed & the agent-CLI floor

Over a local **agent CLI** (`claude -p`), every call cold-boots a full agent (~7s) and the model's
thinking time varies by input — so medium lands ~10s for typical text but can reach ~20s on a hard
idiom, and it is **not a hard ≤10s guarantee**. High is reliably under 30s. The engine already
disables MCP/settings/session loading, keeps output terse, and tells the model to translate
directly — this cut latency ~2–3× — but the boot + thinking floor is inherent to running a full
agent locally.

**For a hard latency guarantee** (medium ~2–3s, high ~8–10s), use an **API key** — the SDK path has
no boot cost and is used automatically when `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` is set. **`fast`
mode is always <1s.** `VERILOQUA_PROGRESS=1` shows per-call progress so a run never looks hung.

## The headline feature: it never repeats a corrected mistake

Correcting a translation is a first-class, durable operation — and the guarantee is
**deterministic**, not a hope pinned on model behavior:

```python
r = tr.translate("the cloud", to="zh", domain="tech")   # say it returned "云朵" (wrong)
tr.correct(r.request_id, "云端")                          # the accepted rendering
# From now on, in a tech context, "the cloud" will never come back as "云朵".
```

- **A correction stores your accepted rendering *and* the rejected one** (the mistake).
- On the next translation, a **deterministic normalized-span scan** finds any recurrence of
  the corrected span — anywhere in the input, not just identical whole documents.
- A **post-generation reject-guard** removes the rejected rendering if the model produces it
  again. An exact repeat of a corrected mistake *in its context* is impossible, regardless of
  model or temperature.
- **It works with zero LLM keys.** `vq correct` and the never-repeat guarantee need no API key;
  an optional model call only *widens* fuzzy recall.

### Anti-overfit by design

A fix learned for casual chat will **not** fire in a legal contract. Corrections default to the
**narrowest (user) scope**, auto-apply only on an exact-scope key match, and are gated by a
*directional* domain match. Crossing to a global/shared "always" lock requires an explicit
human `vq lock` — counts alone never promote a rule. Every correction auto-generates an
**over-fit probe** that CI asserts does *not* fire out of context.

## CLI

```bash
vq "Any language text here"                     # no --to → translates to Chinese (zh)
vq "Hello, world" --to es                       # pick a target language
vq "The spirit is willing" -t ru -m high -d literature
echo "long text" | vq --to fr --mode medium --json

vq correct <request_id> "better translation"   # the trusted learning path (no key needed)
vq glossary add "New York" "纽约" --from en --to zh --invariant
vq memory stats | export backup.json | forget <id> | conflicts | purge-log
vq lock <entry_id> --scope global              # explicit human promotion
vq eval                                        # deterministic never-repeat / over-fit gate
vq backends                                    # what's installed / available
```

Also runnable as `python -m veriloqua`.

## Memory model

Two tiers with opposite mechanisms:

- **Term locks** (a deterministic glossary): applied in *all* modes. Invariant locks
  (proper nouns, product names, codes) are protected by masking so they survive machine
  translation intact and never get mangled by inflection.
- **Corrections** (contextual memory): retrieved and injected in medium/high, gated by context,
  and enforced by the deterministic reject-guard.

Everything is stored in a **local SQLite file you own** (`~/.local/share/veriloqua/memory.db`) —
it never phones home. Retrieval precedence is `user > project > global`. Corrections
*supersede* rather than mutate (full audit + rollback via `superseded_by`), and a partial unique
index guarantees at most one active row per key.

### Privacy

Local-only storage, minimal-span records, hashing, and `forget()` / `export` are the real
guarantees. `correct-by-id` is powered by a **bounded, opt-outable request-log ring buffer**
(last 1000 requests or 30 days by default) — not permanent retention of every source. Clear it
any time with `vq memory purge-log`, or disable logging entirely (you keep correct-by-text).
A regex scrubber redacts obvious secrets; we don't claim it removes PII from free prose.

## Configuration

Resolution order: constructor arg → `VERILOQUA_*` env → provider-native env
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) → TOML (`~/.config/veriloqua/config.toml`) → defaults.

Nothing is required. Everything below is optional.

| Key | Purpose |
|-----|---------|
| `VERILOQUA_LLM_PROVIDER` | force a backend: `claude_cli` \| `codex_cli` \| `anthropic` \| `openai` (default: auto-detect, agent CLI first) |
| `VERILOQUA_CLI_MODEL` | model to pass to the agent CLI (`claude -p --model …`); default lets the CLI use its own model |
| `VERILOQUA_MEDIUM_MODEL` / `VERILOQUA_HIGH_MODEL` | translator models per mode (SDK backends) |
| `VERILOQUA_JUDGE_MODEL` / `VERILOQUA_BACKTRANSLATION_MODEL` | the **independent** verification models for high mode |
| `VERILOQUA_NO_THIRD_PARTY` | hard-disable the keyless Google path (fast mode is allowed by default) |
| `VERILOQUA_QUIET` | silence the one-time third-party notice |
| `VERILOQUA_MAX_COST` / `VERILOQUA_MAX_CALLS` | per-job budget ceilings |

### High-mode independence

Real verification needs a *different* model judging the translator's work. Set a distinct
`judge_model` and the trace reports `judge_independent: true`. With a single model configured,
high mode still runs (same model, different prompt) but honestly reports
`judge_independent: false` and drops the independence credit from confidence — it is **never faked**.

### Budgets

Every mode enforces per-segment **and** per-job ceilings (calls / tokens / wall-clock / est. cost).
High mode is capped at ≤12 LLM calls/segment. On exhaustion the engine **fails closed** to the
best result so far (or the keyless fast path) with a clear reason — a large file can never
silently become a five-figure bill.

## Backends

The default LLM path is your **locally logged-in agent CLI** (`claude -p` / `codex exec`) —
nothing to `pip install`, no key. The core install depends only on `httpx`. Everything below
is an optional alternative or upgrade:

```bash
pip install veriloqua[anthropic]   # API-key LLM backend (alternative to the agent CLI)
pip install veriloqua[openai]      # API-key LLM + API embeddings
pip install veriloqua[deepl]       # production MT swap
pip install veriloqua[google]      # Google Cloud Translation (keyed, rate-stable)
pip install veriloqua[embeddings]  # local semantic-recall booster (surfaces candidates only)
pip install veriloqua[rapidfuzz]   # faster fuzzy surfacing (difflib fallback otherwise)
pip install veriloqua[cli]         # rich CLI
pip install veriloqua[all]
```

The core install depends only on `httpx`. The self-learning guarantee and the fast path use
nothing heavier than the Python standard library.

## How it was designed

Veriloqua's architecture came out of a five-way design debate (a linguist, an LLM-verification
architect, a memory/self-learning expert, a packaging engineer, and an adversarial red-teamer),
synthesized and then stress-tested. The decisive principle: **the guarantee lives in
deterministic code, not model behavior** — optional ML only makes recall better, never carries
the promise.

## License

Apache-2.0.
