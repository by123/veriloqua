"""Two-stage retrieval.

Stage 1 (EXACT) is a deterministic normalized-span scan over active exact-key
spans — the ONLY tier that auto-applies and the ONLY tier that feeds the
deterministic reject-guard. Stage 2 (lexical fuzzy) only SURFACES candidates for the
prompt; it never blind-substitutes and never affects the guard's verdict, so the
verdict is identical whether or not ``rapidfuzz`` is installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from veriloqua._norm import contains_span, normalize
from veriloqua.memory.records import SCOPE_PRECEDENCE, Kind, MemoryEntry

INJECTION_CAP = 20

# lexical fuzzy backend: rapidfuzz if present, else stdlib difflib. Never feeds
# the reject-guard, so the choice cannot affect a Tier-1 verdict.
try:  # pragma: no cover - trivial import guard
    from rapidfuzz.fuzz import token_sort_ratio as _fuzz

    def _ratio(a: str, b: str) -> float:
        return _fuzz(a, b) / 100.0

    FUZZY_BACKEND = "rapidfuzz"
except Exception:  # pragma: no cover
    import difflib

    def _ratio(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a, b).ratio()

    FUZZY_BACKEND = "difflib"


def tags_contradicted(entry_tags: dict[str, str], ctx: dict[str, str]) -> bool:
    """Directional match: suppress an entry only if one of ITS declared tags is
    CONTRADICTED by the current segment. An under-tagged (sparse) but correct entry
    is never dropped for sparsity — that is the anti-overfit gate."""
    # Gate on domain/locale only. Register is compared as a soft signal elsewhere;
    # the correction path stores a Joos style here while retrieval carries a tone tag,
    # so contradicting on it would falsely suppress valid corrections.
    for key in ("domain", "locale"):
        ev = normalize(entry_tags.get(key, ""))
        cv = normalize(ctx.get(key, ""))
        if ev and cv and ev != cv:
            return True
    return False


@dataclass(slots=True)
class Surfaced:
    entry: MemoryEntry
    tier: str          # exact | lexical
    similarity: float


@dataclass(slots=True)
class Retrieval:
    #: term locks whose source span occurs in the input (auto-applied, all modes).
    exact_locks: list[MemoryEntry] = field(default_factory=list)
    #: corrections whose span occurs AND whose context is not contradicted
    #: (auto-applied medium/high; feed the deterministic Layer-B guard).
    exact_corrections: list[MemoryEntry] = field(default_factory=list)
    #: lexical-fuzzy candidates for prompt injection only (never auto-apply).
    surfaced: list[Surfaced] = field(default_factory=list)
    fuzzy_backend: str = FUZZY_BACKEND

    def landmine(self) -> bool:
        """A matched past correction forces the deep tier of the auto cascade."""
        return bool(self.exact_corrections)


def _scope_key(e: MemoryEntry) -> int:
    return SCOPE_PRECEDENCE.get(e.scope, 0)


def _entry_key(e: MemoryEntry) -> tuple[str, str, str]:
    return (e.source_norm, e.src_lang, e.tgt_lang)


def _dedupe_highest_scope(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """The same normalized key can exist at several scopes (user AND project AND
    global). Inject only the highest-precedence row (user > project > global) —
    never all of them at once."""
    seen: set[tuple[str, str, str]] = set()
    out: list[MemoryEntry] = []
    for e in entries:  # callers pass these sorted by scope precedence, descending
        k = _entry_key(e)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def exact_scan(entries: list[MemoryEntry], source_text: str,
               ctx: dict[str, str]) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
    """Deterministic: return (term_locks, corrections) whose normalized source span
    is a contiguous token-subsequence of ``source_text`` and (for corrections) whose
    context is not contradicted. Independent of any fuzzy backend."""
    locks: list[MemoryEntry] = []
    corrections: list[MemoryEntry] = []
    for e in entries:
        if not contains_span(source_text, e.source_text):
            continue
        if e.kind in (Kind.TERM_LOCK,):
            if not tags_contradicted(e.context_tags, ctx):
                locks.append(e)
        else:
            if not tags_contradicted(e.context_tags, ctx):
                corrections.append(e)
    # highest scope precedence first, then most specific (longest) span; then keep
    # only one row per key — the winning scope — instead of injecting duplicates
    locks.sort(key=lambda e: (_scope_key(e), len(e.source_norm)), reverse=True)
    corrections.sort(key=lambda e: (_scope_key(e), len(e.source_norm)), reverse=True)
    return _dedupe_highest_scope(locks), _dedupe_highest_scope(corrections)


def retrieve(entries: list[MemoryEntry], source_text: str, ctx: dict[str, str], *,
             use_fuzzy: bool = True, cutoff: float = 0.72) -> Retrieval:
    """Full retrieval for medium/high. Exact tier auto-applies; fuzzy tier surfaces."""
    locks, corrections = exact_scan(entries, source_text, ctx)
    # keys already covered by the exact tier (at any scope) never surface again
    exact_keys = {_entry_key(e) for e in locks} | {_entry_key(e) for e in corrections}

    surfaced: list[Surfaced] = []
    if use_fuzzy:
        nsrc = normalize(source_text)
        for e in entries:
            if _entry_key(e) in exact_keys:
                continue
            if tags_contradicted(e.context_tags, ctx):
                continue
            sim = _ratio(nsrc, e.source_norm)
            gate = sim * e.weight()
            if gate >= cutoff * 0.6:  # surface generously; injection is capped downstream
                surfaced.append(Surfaced(entry=e, tier="lexical", similarity=sim))
        surfaced.sort(key=lambda s: (s.similarity * s.entry.weight(), _scope_key(s.entry)),
                      reverse=True)
        # one row per key here too: the best-scoring scope wins
        seen: set[tuple[str, str, str]] = set()
        deduped: list[Surfaced] = []
        for s in surfaced:
            k = _entry_key(s.entry)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(s)
        surfaced = deduped[:INJECTION_CAP]

    return Retrieval(exact_locks=locks, exact_corrections=corrections, surfaced=surfaced)


def fast_locks(entries: list[MemoryEntry], source_text: str) -> list[MemoryEntry]:
    """Fast mode: INVARIANT whole-token term locks only. No corrections (they may
    inflect and fast has no model to inflect them), no context gating beyond the
    invariant flag — kills morphological breakage on de/ru/pl/ar/fi."""
    out: list[MemoryEntry] = []
    for e in entries:
        if e.kind is Kind.TERM_LOCK and e.invariant and contains_span(source_text, e.source_text):
            out.append(e)
    out.sort(key=lambda e: len(e.source_norm), reverse=True)
    return out
