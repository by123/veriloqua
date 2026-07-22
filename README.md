# Veriloqua

**A self-improving translation engine that understands what people really mean.**

Veriloqua translates for *meaning, tone, register, and cultural effect* — not word-swaps —
and **learns from every correction**: a deterministic guard blocks corrected renderings
from recurring on exact-span matches in the same context.

**Zero-config by default.** `pip install veriloqua` and go — **no API keys, no setup**:

- **`fast`** uses Google's public translation endpoint directly (keyless).
- **`auto`** (the default) runs a tiered LLM cascade through a **locally logged-in
  Claude Code CLI** (`claude -p`) as a background subprocess. No `ANTHROPIC_API_KEY`,
  no SDK. With no LLM available it degrades to the keyless fast path.

```bash
pip install veriloqua
```

```python
import veriloqua

# Zero config: uses your logged-in `claude` CLI if present, else keyless Google
print(veriloqua.translate("Break a leg!", to="zh"))

tr = veriloqua.Translator()
r = tr.translate("Break a leg!", to="zh", mode="auto", domain="casual-chat")
print(r.text, r.confidence)

# Teach it once — the deterministic guard blocks that rendering in this context
tr.correct(r.request_id, "祝你好运")
```

> API keys are still supported (`pip install veriloqua[anthropic]` + `ANTHROPIC_API_KEY`,
> or `[openai]`) and take over automatically if you'd rather use the SDK — but they are never required.

---

## Two modes, one API

| Mode | What it does | Needs | Typical speed |
|------|--------------|-------|---------------|
| **`fast`** | Google Translate directly + invariant term locks | nothing (keyless) | <1s |
| **`auto`** (default) | Tiered LLM cascade: trivial inputs via keyless MT (<1s, zero LLM calls) → Haiku triage → Sonnet translate + self-review → Opus deep judgment on hard cases, with correction memory and a deterministic reject-guard | agent CLI **or** API key (else degrades to fast) | <1s trivial · ~10-30s per LLM tier over an agent CLI · ~2-10s over an API key |

Pick per call: `translate(text, to="ja", mode="fast"|"auto")`.

### Speed expectations (typical, not guaranteed)

Over a local **agent CLI** (`claude -p`), every call cold-boots a full agent (~7s) and the
model's thinking time varies by input, so per-tier latencies vary — a hard idiom that
escalates through all three tiers can take ~40s. Latencies here are typical observations,
not ceilings. An **API key** removes the boot cost (the SDK path is used automatically when
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` is set). `VERILOQUA_PROGRESS=1` shows per-call
progress so a run never looks hung.

## Correction memory: teach it once, deterministically enforced

Correcting a translation is a first-class, durable operation. Enforcement lives in
deterministic code, not in model behavior — and where enforcement cannot apply, the result
says so instead of passing silently:

```python
r = tr.translate("the cloud", to="zh", domain="tech")   # say it returned "云朵" (wrong)
tr.correct(r.request_id, "云端")                          # the accepted rendering
# In a tech context, an exact-span recurrence of "the cloud" → "云朵" is now blocked.
```

- **A correction stores your accepted rendering *and* the rejected one** (the mistake).
- On the next translation, a **deterministic normalized-span scan** finds any recurrence of
  the corrected span — anywhere in the input, not just identical whole documents.
- A **post-generation reject-guard** rewrites the rejected rendering if the model produces
  it again, using the same normalized matching for detection and replacement. If it detects
  a violation it cannot rewrite, the result is **degraded, never silently OK**.
- **Scope of the claim:** enforcement is exact-span, same-context. Paraphrases of a mistake
  are the model's job (the correction is injected into the prompt), not the guard's.
- **It works with zero LLM keys.** `vq correct` and the deterministic core need no API key;
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
vq "The spirit is willing" -t ru -d literature          # auto mode is the default
echo "long text" | vq --to fr --mode fast --json

vq correct <request_id> "better translation"   # the trusted learning path (no key needed)
vq glossary add "New York" "纽约" --from en --to zh --invariant
vq memory stats | export backup.json | forget <id> | conflicts | purge-log
vq lock <entry_id> --scope global              # explicit human promotion
vq eval                                        # deterministic correction-replay / over-fit gate
vq eval --suite gold                           # translate the gold set, report sentence chrF (advisory)
vq backends                                    # what's installed / available
```

Also runnable as `python -m veriloqua`.

## Memory model

Two tiers with opposite mechanisms:

- **Term locks** (a deterministic glossary): applied in *all* modes. Invariant locks
  (proper nouns, product names, codes) are protected by masking so they survive machine
  translation intact and never get mangled by inflection.
- **Corrections** (contextual memory): retrieved and injected in auto mode, gated by context,
  and enforced by the deterministic reject-guard on exact-span matches.

Everything is stored in a **local SQLite file you own** (`~/.local/share/veriloqua/memory.db`) —
it never phones home. Retrieval precedence is `user > project > global`. Corrections
*supersede* rather than mutate (full audit + rollback via `superseded_by`), and a partial unique
index guarantees at most one active row per key.

### Privacy

Local-only storage, minimal-span records, scrubbing, hashing, and `forget()` / `export` are
the real mechanisms. `correct-by-id` is powered by a **bounded, opt-outable request-log ring buffer**
(last 1000 requests or 30 days by default) — not permanent retention of every source. Clear it
any time with `vq memory purge-log`, or disable logging entirely (you keep correct-by-text).
A regex scrubber redacts obvious secrets; we don't claim it removes PII from free prose.

## Configuration

Resolution order: constructor arg → `VERILOQUA_*` env → provider-native env
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) → TOML (`~/.config/veriloqua/config.toml`) → defaults.

Nothing is required. Everything below is optional.

| Key | Purpose |
|-----|---------|
| `VERILOQUA_LLM_PROVIDER` | force a backend: `claude_cli` \| `anthropic` \| `openai` (default: auto-detect, Claude CLI first) |
| `VERILOQUA_CLI_MODEL` | model to pass to the agent CLI (`claude -p --model …`); default lets the CLI use its own model |
| `VERILOQUA_TRIAGE_MODEL` / `VERILOQUA_TRANSLATE_MODEL` / `VERILOQUA_DEEP_MODEL` | the three cascade tier models |
| `VERILOQUA_SDK_MODEL` | default model for API-key SDK backends |
| `VERILOQUA_NO_THIRD_PARTY` | hard-disable the keyless Google path (fast mode is allowed by default) |
| `VERILOQUA_QUIET` | silence the one-time third-party notice |
| `VERILOQUA_MAX_COST` / `VERILOQUA_MAX_CALLS` | per-job budget ceilings |

### Budgets

Every mode enforces per-segment **and** per-job ceilings (calls / tokens / wall-clock /
est. cost). On exhaustion the engine stops and returns the best result so far (or the
keyless fast path), marked degraded with a clear reason, so a large file cannot silently
run up an unbounded bill.

## Backends

The default LLM path is your **locally logged-in Claude Code CLI** (`claude -p`) —
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

The core install depends only on `httpx`. The self-learning core and the fast path use
nothing heavier than the Python standard library.

## How it was designed

Veriloqua's architecture came out of a five-way design debate (a linguist, an LLM-verification
architect, a memory/self-learning expert, a packaging engineer, and an adversarial red-teamer),
synthesized and then stress-tested. The decisive principle: **enforcement lives in
deterministic code, not model behavior** — optional ML only makes recall better, never carries
the enforcement.

## License

Apache-2.0.
