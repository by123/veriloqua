"""Zero-config LLM backend that drives the locally-installed Claude Code CLI as a
subprocess — no API key, no vendor SDK.

If you have Claude Code (`claude`) installed and logged in, the engine runs auto
mode by shelling out to it in headless "print" mode and reading the answer from
stdout. Speed matters here: an agent CLI boots a full agent per call, so the preset
disables MCP servers, project/user settings, session persistence, and slash
commands, and caps the run to one turn — a translation needs none of that. This
takes a `claude -p` call from ~24s down to ~8-12s. The translator system prompt is
passed on the real `--system-prompt` channel (not concatenated into the user text),
which also avoids the model mistaking it for a prompt-injection.
"""

from __future__ import annotations

import shutil
import subprocess

from veriloqua.backends.base import LLMResponse
from veriloqua.errors import BackendNotConfigured

# Flags that strip everything a translation doesn't need, so the agent boots fast.
_CLAUDE_FAST = [
    "-p", "--output-format", "text",
    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',  # no MCP servers
    "--setting-sources", "",                                     # no project/user settings
    "--max-turns", "1",                                          # single turn, no tool loop
    "--no-session-persistence",
    "--disable-slash-commands",
]

# preset -> how to invoke it. Claude Code is the only supported agent CLI.
PRESETS: dict[str, dict] = {
    "claude_cli": {
        "bin": "claude",
        "args": _CLAUDE_FAST,
        "model_flag": "--model",
        "system_flag": "--system-prompt",
        "hint": "install Claude Code and run `claude` once to log in",
    },
}

DEFAULT_TIMEOUT = 120.0


def _model_alias(model: str | None) -> str | None:
    """Map a model id/alias to a Claude-CLI alias. Aliases resolve fast and are
    version-stable; an unknown id returns None so the CLI uses its own default."""
    if not model:
        return None
    m = model.lower()
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    return None


class CliLLMBackend:
    name = "cli"  # overridden per instance with the concrete preset (claude_cli)

    def __init__(
        self,
        preset: str = "claude_cli",
        *,
        model: str | None = None,
        binary: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        extra_args: list[str] | None = None,
    ) -> None:
        spec = PRESETS.get(preset)
        if spec is None:
            raise BackendNotConfigured(f"unknown CLI preset '{preset}'")
        self.preset = preset
        self.name = preset
        self.model = model or ""          # LLMBackend protocol wants a .model string
        self._spec = spec
        self._model = model               # explicit override (config.cli_model)
        self.timeout = timeout
        self.extra_args = extra_args or []
        self._bin = binary or shutil.which(spec["bin"])
        if not self._bin:
            raise BackendNotConfigured(
                f"'{spec['bin']}' CLI not found on PATH — {spec['hint']}, "
                "or use mode='fast'."
            )

    @classmethod
    def detect(cls, *, model: str | None = None) -> CliLLMBackend | None:
        """Return a backend for the Claude Code CLI if it is installed, else None."""
        for preset, spec in PRESETS.items():
            if shutil.which(spec["bin"]):
                return cls(preset, model=model)
        return None

    def complete(self, system: str, user: str, *, model: str | None = None,
                 effort: str = "high", max_tokens: int = 4096) -> LLMResponse:
        cmd = [self._bin, *self._spec["args"], *self.extra_args]

        # Model selection: an explicit cli_model wins (passed raw); otherwise map the
        # per-call model id to a Claude-CLI alias (haiku/sonnet/opus).
        alias = self._model or _model_alias(model)
        if alias:
            cmd += [self._spec["model_flag"], alias]

        cmd += [self._spec["system_flag"], system]     # system on its own channel
        run_kwargs: dict = {
            "capture_output": True, "text": True, "timeout": self.timeout,
            "input": user,                             # prompt via stdin, never argv
        }

        try:
            proc = subprocess.run(cmd, **run_kwargs)  # noqa: S603
        except FileNotFoundError as exc:
            raise BackendNotConfigured(f"'{self._bin}' not runnable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendNotConfigured(f"{self.name} timed out after {self.timeout:.0f}s") from exc

        if proc.returncode != 0:
            err = (proc.stderr or "").strip()[:300]
            raise BackendNotConfigured(
                f"{self.name} exited {proc.returncode}: {err or 'no stderr'} "
                f"— try `{self._spec['bin']} -p hi` to check your login."
            )

        text = (proc.stdout or "").strip()
        return LLMResponse(
            text=text,
            input_tokens=(len(system) + len(user)) // 4,   # CLI doesn't report tokens
            output_tokens=len(text) // 4,
            model=alias or self.preset,
        )
