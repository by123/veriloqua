"""Default stdlib-``sqlite3`` memory store.

A plain, user-owned file that never phones home. Implements the full
``MemoryStore`` Protocol including the ring-buffer request log and the
partial-unique-index invariant (at most one active row per key).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from veriloqua.memory.records import (
    TTL_DAYS,
    Kind,
    MemoryEntry,
    Provenance,
    RequestLogRecord,
    Scope,
    Status,
)

MEMORY_SCHEMA_VERSION = 1
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_QUARANTINE_FLOOR = 0.3  # confidence below this auto-quarantines


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"],
        kind=Kind(row["kind"]),
        src_lang=row["src_lang"],
        tgt_lang=row["tgt_lang"],
        source_text=row["source_text"],
        source_norm=row["source_norm"],
        accepted_translation=row["accepted_translation"],
        literal_gloss=row["literal_gloss"],
        rejected_translations=json.loads(row["rejected_translations"] or "[]"),
        error_type=row["error_type"],
        context_tags=json.loads(row["context_tags"] or "{}"),
        applies_when=row["applies_when"],
        rationale=row["rationale"],
        examples=json.loads(row["examples"] or "[]"),
        provenance=Provenance(row["provenance"]),
        source_urls=json.loads(row["source_urls"] or "[]"),
        confidence=row["confidence"],
        scope=Scope(row["scope"]),
        scope_id=row["scope_id"],
        status=Status(row["status"]),
        invariant=bool(row["invariant"]),
        hit_count=row["hit_count"],
        apply_count=row["apply_count"],
        positive_signals=row["positive_signals"],
        negative_signals=row["negative_signals"],
        ttl_days=row["ttl_days"],
        created_at=row["created_at"],
        last_verified_at=row["last_verified_at"],
        last_applied_at=row["last_applied_at"],
        superseded_by=row["superseded_by"],
        deleted_at=row["deleted_at"],
    )


class SqliteMemoryStore:
    def __init__(self, path: str | Path = ":memory:", *, request_log_max: int = 1000,
                 request_log_days: int = 30) -> None:
        self.path = str(path)
        self.request_log_max = request_log_max
        self.request_log_days = request_log_days
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        """Owner-only (0600) on the DB and its WAL/SHM sidecars: the memory holds
        user text and must not be world-readable. Best-effort (no-op on :memory:,
        limited effect on Windows)."""
        if self.path == ":memory:":
            return
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.path + suffix)
            try:
                if p.exists():
                    os.chmod(p, 0o600)
            except OSError:
                pass

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        cur = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'")
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(MEMORY_SCHEMA_VERSION),),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ reads
    def active_entries(self, src_lang: str, tgt_lang: str, scopes: list[str], *,
                       user_id: str = "", project_id: str = "") -> list[MemoryEntry]:
        placeholders = ",".join("?" for _ in scopes) or "''"
        base = (
            "SELECT * FROM entries WHERE tgt_lang=? AND status='active' "
            f"AND deleted_at IS NULL AND scope IN ({placeholders})"
        )
        params: list = [tgt_lang, *scopes]
        # scope ownership: user rows belong to this user, project rows to this project;
        # legacy rows with an empty scope_id predate identities and still load.
        base += (
            " AND (scope='global'"
            " OR (scope='user' AND (scope_id=? OR scope_id=''))"
            " OR (scope='project' AND (scope_id=? OR scope_id='')))"
        )
        params += [user_id, project_id]
        # 'auto' is a wildcard on either side: an unspecified query source matches any
        # stored source, and stored 'auto' entries match a specific-source query.
        if src_lang not in ("auto", "", None):
            base += " AND (src_lang=? OR src_lang='auto')"
            params.append(src_lang)
        cur = self._conn.execute(base, params)
        return [_row_to_entry(r) for r in cur.fetchall()]

    def get(self, entry_id: int) -> MemoryEntry | None:
        cur = self._conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,))
        row = cur.fetchone()
        return _row_to_entry(row) if row else None

    def conflicts(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM conflicts WHERE resolved=0 ORDER BY created_at DESC"
        )
        return [dict(r) for r in cur.fetchall()]

    def stats(self) -> dict:
        c = self._conn
        by_status = {
            r["status"]: r["n"]
            for r in c.execute(
                "SELECT status, COUNT(*) n FROM entries WHERE deleted_at IS NULL GROUP BY status"
            )
        }
        by_kind = {
            r["kind"]: r["n"]
            for r in c.execute(
                "SELECT kind, COUNT(*) n FROM entries WHERE deleted_at IS NULL GROUP BY kind"
            )
        }
        total = c.execute("SELECT COUNT(*) n FROM entries WHERE deleted_at IS NULL").fetchone()["n"]
        reqs = c.execute("SELECT COUNT(*) n FROM request_log").fetchone()["n"]
        conf = c.execute("SELECT COUNT(*) n FROM conflicts WHERE resolved=0").fetchone()["n"]
        return {
            "total_entries": total,
            "by_status": by_status,
            "by_kind": by_kind,
            "request_log_size": reqs,
            "open_conflicts": conf,
            "db_path": self.path,
            "schema_version": MEMORY_SCHEMA_VERSION,
        }

    # ----------------------------------------------------------------- writes
    def add_entry(self, entry: MemoryEntry) -> int:
        entry.created_at = entry.created_at or _now()
        if entry.ttl_days is None:
            entry.ttl_days = TTL_DAYS.get(entry.kind)
        if entry.confidence < _QUARANTINE_FLOOR and entry.status == Status.ACTIVE:
            entry.status = Status.PROPOSED
        cur = self._conn.execute(
            """
            INSERT INTO entries (
                kind, src_lang, tgt_lang, source_text, source_norm, accepted_translation,
                literal_gloss, rejected_translations, error_type, context_tags, applies_when,
                rationale, examples, provenance, source_urls, confidence, scope, scope_id,
                status, invariant, hit_count, apply_count, positive_signals, negative_signals,
                ttl_days, created_at, last_verified_at, last_applied_at, superseded_by, deleted_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                entry.kind.value, entry.src_lang, entry.tgt_lang, entry.source_text,
                entry.source_norm, entry.accepted_translation, entry.literal_gloss,
                json.dumps(entry.rejected_translations, ensure_ascii=False), entry.error_type,
                json.dumps(entry.context_tags, ensure_ascii=False), entry.applies_when,
                entry.rationale, json.dumps(entry.examples, ensure_ascii=False),
                entry.provenance.value, json.dumps(entry.source_urls, ensure_ascii=False),
                entry.confidence, entry.scope.value, entry.scope_id, entry.status.value,
                int(entry.invariant), entry.hit_count, entry.apply_count,
                entry.positive_signals, entry.negative_signals, entry.ttl_days,
                entry.created_at, entry.last_verified_at, entry.last_applied_at,
                entry.superseded_by, entry.deleted_at,
            ),
        )
        self._conn.commit()
        entry.id = int(cur.lastrowid or 0)
        return entry.id

    def supersede(self, old_id: int, new_entry: MemoryEntry) -> int:
        """Corrections supersede, never mutate: mark old as SUPERSEDED, insert new,
        link via superseded_by. Full audit + rollback preserved."""
        # Insert first so the partial-unique index sees old still active? No — the old
        # active row and a new active row would collide. Retire the old row first.
        self._conn.execute(
            "UPDATE entries SET status='superseded' WHERE id=?", (old_id,)
        )
        new_id = self.add_entry(new_entry)
        self._conn.execute(
            "UPDATE entries SET superseded_by=? WHERE id=?", (new_id, old_id)
        )
        self._conn.commit()
        return new_id

    def soft_delete(self, entry_id: int) -> bool:
        cur = self._conn.execute(
            "UPDATE entries SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
            (_now(), entry_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def record_conflict(self, existing_id: int, proposed_accepted: str, note: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO conflicts(existing_id, proposed_accepted, note, created_at) "
            "VALUES (?,?,?,?)",
            (existing_id, proposed_accepted, note, _now()),
        )
        # existing entry drops to needs_review so it stops auto-applying until resolved
        self._conn.execute(
            "UPDATE entries SET status='needs_review' WHERE id=? AND status='active'",
            (existing_id,),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def promote_scope(self, entry_id: int, scope: str) -> bool:
        cur = self._conn.execute(
            "UPDATE entries SET scope=?, last_verified_at=? WHERE id=? AND deleted_at IS NULL",
            (scope, _now(), entry_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def activate(self, entry_id: int) -> bool:
        cur = self._conn.execute(
            "UPDATE entries SET status='active' WHERE id=? AND deleted_at IS NULL "
            "AND status IN ('proposed','needs_review')",
            (entry_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def find_active_key(self, source_norm: str, src_lang: str, tgt_lang: str,
                        scope: str, scope_id: str) -> MemoryEntry | None:
        cur = self._conn.execute(
            "SELECT * FROM entries WHERE source_norm=? AND src_lang=? AND tgt_lang=? "
            "AND scope=? AND scope_id=? AND status='active' AND deleted_at IS NULL",
            (source_norm, src_lang, tgt_lang, scope, scope_id),
        )
        row = cur.fetchone()
        return _row_to_entry(row) if row else None

    # -------------------------------------------------------- feedback / audit
    def record_application(self, entry_id: int, request_id: str, tier: str = "exact") -> None:
        self._conn.execute(
            "INSERT INTO applications(entry_id, request_id, applied_at, tier) VALUES (?,?,?,?)",
            (entry_id, request_id, _now(), tier),
        )
        self._conn.execute(
            "UPDATE entries SET apply_count=apply_count+1, last_applied_at=? WHERE id=?",
            (_now(), entry_id),
        )
        self._conn.commit()

    def bump_hit(self, entry_id: int) -> None:
        self._conn.execute("UPDATE entries SET hit_count=hit_count+1 WHERE id=?", (entry_id,))
        self._conn.commit()

    def apply_signals(self, entry_id: int, positive: int = 0, negative: int = 0) -> None:
        # asymmetric decay: reverts cost more than accepts (beta > alpha)
        alpha, beta = 0.05, 0.20
        entry = self.get(entry_id)
        if not entry:
            return
        conf = entry.confidence + alpha * positive - beta * negative
        conf = max(0.0, min(1.0, conf))
        status = entry.status.value
        if conf < _QUARANTINE_FLOOR and status == "active":
            status = "proposed"
        self._conn.execute(
            "UPDATE entries SET positive_signals=positive_signals+?, "
            "negative_signals=negative_signals+?, confidence=?, status=? WHERE id=?",
            (positive, negative, conf, status, entry_id),
        )
        self._conn.commit()

    def run_maintenance(self) -> dict:
        """Nightly sweep: TTL staleness flags + confidence-floor quarantine.

        Delayed weak-positive granting (applied-and-not-re-corrected) requires the
        request log; it is only performed when logging is enabled and is intentionally
        conservative — we never ship decay math that no signal feeds.
        """
        quarantined = self._conn.execute(
            "UPDATE entries SET status='proposed' "
            "WHERE status='active' AND confidence < ? AND deleted_at IS NULL",
            (_QUARANTINE_FLOOR,),
        ).rowcount
        self._conn.commit()
        return {"quarantined": quarantined}

    # ------------------------------------------------------ request-log buffer
    def log_request(self, record: RequestLogRecord) -> None:
        record.created_at = record.created_at or _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO request_log "
            "(request_id, src_lang, tgt_lang, source_text, output_text, mode, "
            " domain, register, source_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                record.request_id, record.src_lang, record.tgt_lang, record.source_text,
                record.output_text, record.mode, record.domain, record.register,
                record.source_hash, record.created_at,
            ),
        )
        self._conn.commit()
        self._prune_log()

    def load_request(self, request_id: str) -> RequestLogRecord | None:
        cur = self._conn.execute("SELECT * FROM request_log WHERE request_id=?", (request_id,))
        row = cur.fetchone()
        if not row:
            return None
        return RequestLogRecord(
            request_id=row["request_id"], src_lang=row["src_lang"], tgt_lang=row["tgt_lang"],
            source_text=row["source_text"], output_text=row["output_text"], mode=row["mode"],
            domain=row["domain"], register=row["register"], created_at=row["created_at"],
            source_hash=row["source_hash"],
        )

    def _prune_log(self) -> None:
        # retention = last N requests OR last M days, whichever is tighter
        if self.request_log_days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self.request_log_days)).isoformat()
            self._conn.execute("DELETE FROM request_log WHERE created_at < ?", (cutoff,))
        if self.request_log_max > 0:
            self._conn.execute(
                "DELETE FROM request_log WHERE request_id IN ("
                "  SELECT request_id FROM request_log ORDER BY created_at DESC LIMIT -1 OFFSET ?"
                ")",
                (self.request_log_max,),
            )
        self._conn.commit()

    def purge_log(self) -> int:
        n = self._conn.execute("SELECT COUNT(*) n FROM request_log").fetchone()["n"]
        self._conn.execute("DELETE FROM request_log")
        self._conn.commit()
        return n

    def all_entries(self, include_deleted: bool = False) -> list[MemoryEntry]:
        q = "SELECT * FROM entries"
        if not include_deleted:
            q += " WHERE deleted_at IS NULL"
        q += " ORDER BY id"
        return [_row_to_entry(r) for r in self._conn.execute(q).fetchall()]

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
