"""Three-stage retrieval.

Stage 1 (EXACT) is a deterministic normalized-span scan over active exact-key
spans — the ONLY tier that auto-applies and the ONLY tier that feeds the hard
non-repeat guarantee. Stages 2 (lexical) and 3 (semantic) only SURFACE candidates
for the prompt; they never blind-substitute and never change the guarantee, so the
verdict is identical whether or not ``rapidfuzz`` / embeddings are installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from veriloqua._norm import contains_span, normalize
from veriloqua.memory.records import SCOPE_PRECEDENCE, Kind, MemoryEntry

INJECTION_CAP = 20

# lexical fuzzy backend: rapidfuzz if present, else stdlib difflib. Never feeds
# the hard guarantee, so the choice cannot affect a Tier-1 verdict.
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
    tier: str          # exact | lexical | semantic
    similarity: float


@dataclass(slots=True)
class Retrieval:
    #: term locks whose source span occurs in the input (auto-applied, all modes).
    exact_locks: list[MemoryEntry] = field(default_factory=list)
    #: corrections whose span occurs AND whose context is not contradicted
    #: (auto-applied medium/high; feed the deterministic Layer-B guard).
    exact_corrections: list[MemoryEntry] = field(default_factory=list)
    #: fuzzy/semantic candidates for prompt injection only (never auto-apply).
    surfaced: list[Surfaced] = field(default_factory=list)
    fuzzy_backend: str = FUZZY_BACKEND

    def landmine(self) -> bool:
        """A matched past correction forces escalation to high."""
        return bool(self.exact_corrections)


def _scope_key(e: MemoryEntry) -> int:
    return SCOPE_PRECEDENCE.get(e.scope, 0)


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
    # highest scope precedence first, then most specific (longest) span
    locks.sort(key=lambda e: (_scope_key(e), len(e.source_norm)), reverse=True)
    corrections.sort(key=lambda e: (_scope_key(e), len(e.source_norm)), reverse=True)
    return locks, corrections


def retrieve(entries: list[MemoryEntry], source_text: str, ctx: dict[str, str], *,
             use_fuzzy: bool = True, cutoff: float = 0.72) -> Retrieval:
    """Full retrieval for medium/high. Exact tier auto-applies; fuzzy tier surfaces."""
    locks, corrections = exact_scan(entries, source_text, ctx)
    exact_ids = {id(e) for e in locks} | {id(e) for e in corrections}

    surfaced: list[Surfaced] = []
    if use_fuzzy:
        nsrc = normalize(source_text)
        for e in entries:
            if id(e) in exact_ids:
                continue
            if tags_contradicted(e.context_tags, ctx):
                continue
            sim = _ratio(nsrc, e.source_norm)
            gate = sim * e.weight()
            if gate >= cutoff * 0.6:  # surface generously; injection is capped downstream
                surfaced.append(Surfaced(entry=e, tier="lexical", similarity=sim))
        surfaced.sort(key=lambda s: (s.similarity * s.entry.weight(), _scope_key(s.entry)),
                      reverse=True)
        surfaced = surfaced[:INJECTION_CAP]

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
