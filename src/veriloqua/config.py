"""Configuration resolution and paths.

Precedence (highest first): explicit constructor arg > ``VERILOQUA_*`` env >
provider-native env (``ANTHROPIC_API_KEY`` etc.) > TOML config file > built-in
defaults. API keys are redacted in ``repr`` so a logged Config never leaks a secret.
"""

from __future__ import annotations

import getpass
import hashlib
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10: stdlib tomllib arrived in 3.11; tomli is the official backport
    import tomli as tomllib

APP = "veriloqua"


def _default_user_id() -> str:
    """A real (local) user identity for user-scoped memory rows. OS account name —
    stable per machine account, never leaves the machine."""
    try:
        return getpass.getuser() or "default"
    except Exception:
        return "default"


def _default_project_id() -> str:
    """A real project identity for project-scoped memory rows: directory name plus a
    short path hash, so two checkouts named `app` don't share project memory."""
    cwd = Path.cwd()
    digest = hashlib.sha1(str(cwd).encode("utf-8")).hexdigest()[:8]
    return f"{cwd.name}-{digest}"


def _data_home() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP


def _config_home() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


_SECRET_HINT = ("key", "token", "secret", "password")


@dataclass(slots=True)
class Config:
    # --- paths ---
    data_dir: Path = field(default_factory=_data_home)
    config_dir: Path = field(default_factory=_config_home)

    # --- backends / models ---
    # "claude_cli" | "codex_cli" | "anthropic" | "openai" | None (auto-detect).
    # Auto-detect prefers a locally-installed agent CLI (claude/codex) so auto mode
    # works with ZERO config and NO API key; API-key SDK backends are the fallback.
    llm_provider: str | None = None
    # The tiered `auto` pipeline uses three models. Over the agent CLI these map to
    # `--model haiku`/`sonnet`/`opus`; SDK backends use the ids directly.
    triage_model: str = "claude-haiku-4-5"     # Haiku: detect / classify / route / simple
    translate_model: str = "claude-sonnet-5"   # Sonnet: translate + self-review (80-90%)
    deep_model: str = "claude-opus-4-8"        # Opus: hard cases, escalation only
    auto_deep: bool = True                     # allow escalation to the Opus deep pass
    auto_crosscheck: bool = True               # on hard cases, triangulate meaning via English
    auto_mt_shortcircuit: bool = True          # tier 0: trivial short inputs answer via keyless MT
    sdk_model: str = "claude-haiku-4-5"        # default model for API-key SDK backends
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    api_key: str | None = None                 # resolved from provider-native env if None
    # Optional model to pass to the agent CLI (`claude -p --model ...`). Default None
    # → let the CLI use whatever model it is already configured with (true zero-config).
    cli_model: str | None = None

    # --- third-party (keyless Google fast path) ---
    # Zero-config: the keyless Google endpoint is allowed by default (a one-time
    # transparent notice is printed). Set no_third_party to hard-disable it.
    allow_third_party: bool = True             # ship source text to the free endpoint
    no_third_party: bool = False               # hard guard: disables the free path

    # --- memory scope identities ---
    # user/project-scoped memory rows are stamped with these and retrieval only loads
    # rows belonging to the current identity (legacy rows with empty scope_id still load).
    user_id: str = field(default_factory=_default_user_id)
    project_id: str = field(default_factory=_default_project_id)

    # --- request log (powers correct-by-id) ---
    request_log_enabled: bool = True
    request_log_max: int = 1000                # keep last N requests
    request_log_days: int = 30                 # or last N days, whichever comes first

    # --- job budget ceilings (0 = unlimited) ---
    max_cost_usd: float = 0.0
    max_calls: int = 0

    default_mode: str = "auto"   # best available with zero config: local agent CLI → else fast

    # --- domain context packs ---
    # A request's `domain` (or an explicit `context=`) names a <domain>.md brief that is
    # injected as UNTRUSTED background into triage/translate/deep, so app/domain knowledge
    # (feature vocabulary, disambiguation rules, "reviews are terse/non-native") reaches the
    # model. Absent pack → zero injection, unchanged behavior. Point VERILOQUA_CONTEXT_DIR at
    # a project's in-repo dir to keep the briefs version-controlled next to the app.
    context_dir: Path | None = None
    context_max_chars: int = 4000   # cap the injected brief (keeps the cheap triage tier cheap)

    # ---- derived paths ----
    @property
    def db_path(self) -> Path:
        return self.data_dir / "memory.db"

    @property
    def resolved_context_dir(self) -> Path:
        return self.context_dir or (self.config_dir / "context")

    def domain_context(self, domain: str) -> str:
        """Load the ``<domain>.md`` context brief (capped), or ``""`` if none exists. The
        domain is reduced to a bare alnum/_/- filename, so it can never escape the dir."""
        name = "".join(c for c in (domain or "") if c.isalnum() or c in ("_", "-")).strip()
        if not name:
            return ""
        try:
            path = self.resolved_context_dir / f"{name}.md"
            if path.is_file():
                return path.read_text(encoding="utf-8")[: self.context_max_chars]
        except OSError:
            return ""
        return ""

    @property
    def replay_dir(self) -> Path:
        return self.data_dir / "replay"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.replay_dir.mkdir(parents=True, exist_ok=True)

    def resolved_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        provider = self.llm_provider or _detect_provider()
        if provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY")
        if provider == "openai":
            return os.environ.get("OPENAI_API_KEY")
        return None

    def __repr__(self) -> str:  # redact secrets
        parts = []
        for f in fields(self):
            val = getattr(self, f.name)
            if any(h in f.name for h in _SECRET_HINT) and val:
                val = "***redacted***"
            parts.append(f"{f.name}={val!r}")
        return f"Config({', '.join(parts)})"


def _detect_provider() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    # accept both a flat table and a [veriloqua] section
    return data.get("veriloqua", data) if isinstance(data, dict) else {}


def load_config(overrides: dict[str, Any] | None = None) -> Config:
    """Build a Config by layering TOML < provider env < VERILOQUA_* env < overrides."""
    cfg = Config()

    # layer 1: TOML file (config_dir/config.toml, or VERILOQUA_CONFIG)
    toml_path = Path(os.environ.get("VERILOQUA_CONFIG", cfg.config_dir / "config.toml"))
    for k, v in _load_toml(toml_path).items():
        if hasattr(cfg, k):
            setattr(cfg, k, _coerce(cfg, k, v))

    # layer 2: VERILOQUA_* env
    env_map = {
        "VERILOQUA_LLM_PROVIDER": "llm_provider",
        "VERILOQUA_TRIAGE_MODEL": "triage_model",
        "VERILOQUA_TRANSLATE_MODEL": "translate_model",
        "VERILOQUA_DEEP_MODEL": "deep_model",
        "VERILOQUA_SDK_MODEL": "sdk_model",
        "VERILOQUA_EMBED_MODEL": "embed_model",
        "VERILOQUA_CLI_MODEL": "cli_model",
        "VERILOQUA_API_KEY": "api_key",
        "VERILOQUA_DEFAULT_MODE": "default_mode",
        "VERILOQUA_USER_ID": "user_id",
        "VERILOQUA_PROJECT_ID": "project_id",
    }
    for env, attr in env_map.items():
        if env in os.environ:
            setattr(cfg, attr, os.environ[env])
    if "VERILOQUA_DATA_DIR" in os.environ:
        cfg.data_dir = Path(os.environ["VERILOQUA_DATA_DIR"])
    if "VERILOQUA_CONTEXT_DIR" in os.environ:
        cfg.context_dir = Path(os.environ["VERILOQUA_CONTEXT_DIR"])
    cfg.allow_third_party = _env_bool("VERILOQUA_ALLOW_THIRD_PARTY", cfg.allow_third_party)
    cfg.no_third_party = _env_bool("VERILOQUA_NO_THIRD_PARTY", cfg.no_third_party)
    cfg.request_log_enabled = _env_bool("VERILOQUA_REQUEST_LOG", cfg.request_log_enabled)
    cfg.auto_deep = _env_bool("VERILOQUA_AUTO_DEEP", cfg.auto_deep)
    cfg.auto_crosscheck = _env_bool("VERILOQUA_AUTO_CROSSCHECK", cfg.auto_crosscheck)
    cfg.auto_mt_shortcircuit = _env_bool("VERILOQUA_AUTO_MT_SHORTCIRCUIT", cfg.auto_mt_shortcircuit)
    if "VERILOQUA_MAX_COST" in os.environ:
        cfg.max_cost_usd = float(os.environ["VERILOQUA_MAX_COST"])
    if "VERILOQUA_MAX_CALLS" in os.environ:
        cfg.max_calls = int(os.environ["VERILOQUA_MAX_CALLS"])

    # layer 3: explicit overrides (highest)
    for k, v in (overrides or {}).items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, _coerce(cfg, k, v))

    return cfg


def _coerce(cfg: Config, key: str, value: Any) -> Any:
    if key in ("data_dir", "config_dir", "context_dir"):
        return Path(value)
    return value
