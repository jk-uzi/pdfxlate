"""Static descriptions of the external agent CLIs we can drive.

One :class:`AgentDef` per supported agent.  Everything here is data: the exact
argv for each agent lives in :mod:`pdfxlate.agents.runtime`, because building a
command is behaviour, while "which binary do we look for, what is it called, and
where does the user install it from" is not.

The five agents are deliberately not modelled uniformly.  They differ in how
the prompt is delivered (stdin or argv), in whether a model can be chosen at
all, and in what a login probe can tell us.  Those differences are encoded as
fields here rather than hidden in ``if agent == ...`` branches scattered through
the detector and catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDef:
    """Everything static about one external agent CLI."""

    #: Stable id used in config.yaml (``Provider.agent``) and in the sidecar API.
    id: str
    #: Human-facing name. The renderer translates this itself by id; this is the
    #: fallback for logs and for a future non-Electron caller.
    name: str
    #: Executable name looked up on PATH (without a platform extension).
    bin: str
    #: Additional executable names to try, in order (e.g. WorkBuddy's ``cbc``).
    fallback_bins: tuple[str, ...] = ()
    #: Environment variable that overrides the executable path.
    env_var: str = ""
    #: Where to send someone who does not have it.
    install_url: str = ""
    docs_url: str = ""
    #: ``"stdin"`` writes the prompt to the child's stdin; ``"argv"`` appends it
    #: as the last argument and is therefore length-limited.
    prompt_mode: str = "stdin"
    #: Maximum prompt characters when ``prompt_mode == "argv"``.
    max_prompt_chars: int | None = None
    #: ZCode is a Node script, not a native executable: ``bin`` is the
    #: interpreter and ``script_relative`` is where the script sits inside the
    #: install directory.
    needs_node: bool = False
    script_relative: str | None = None
    #: Candidate paths, relative to an install directory, for agents that are
    #: not expected on PATH (ZCode, DSH, WorkBuddy's desktop app).
    standard_relative: tuple[str, ...] = ()
    #: Substrings matched against a Windows uninstall entry's ``DisplayName``.
    app_names: tuple[str, ...] = ()
    #: Model ids that are always offered even when the CLI cannot enumerate
    #: them, as ``(id, label)`` pairs.  Claude has no listing command at all.
    builtin_models: tuple[tuple[str, str], ...] = ()
    #: How to interpret the login probe: ``claude_json``, ``codex_text``,
    #: ``codebuddy_text``, ``zcode_doctor`` or ``none``.
    login_probe: str = "none"
    #: Whether v1 can actually use this agent.  Only WorkBuddy's desktop-only
    #: installation flips this to false at detection time.
    supported: bool = True

    @property
    def batch_char_limit(self) -> int:
        """How many prompt characters one batch may carry.

        argv agents have to stay below the platform command-line limit, so they
        use the smaller budget from the feature brief; stdin agents can afford
        the normal one.
        """
        return 8000 if self.prompt_mode == "argv" else 12000

    @property
    def max_batch_items(self) -> int:
        return 8


AGENT_DEFS: tuple[AgentDef, ...] = (
    AgentDef(
        id="claude",
        name="Claude Code",
        bin="claude",
        env_var="PDFXLATE_CLAUDE_BIN",
        install_url="https://docs.anthropic.com/en/docs/claude-code/setup",
        docs_url="https://docs.anthropic.com/en/docs/claude-code/overview",
        builtin_models=(
            ("sonnet", "Sonnet"),
            ("opus", "Opus"),
            ("haiku", "Haiku"),
        ),
        login_probe="claude_json",
    ),
    AgentDef(
        id="codex",
        name="Codex CLI",
        bin="codex",
        env_var="PDFXLATE_CODEX_BIN",
        install_url="https://github.com/openai/codex",
        docs_url="https://github.com/openai/codex",
        login_probe="codex_text",
    ),
    AgentDef(
        id="workbuddy",
        name="WorkBuddy / CodeBuddy",
        bin="codebuddy",
        fallback_bins=("cbc",),
        env_var="PDFXLATE_CODEBUDDY_BIN",
        install_url="https://www.codebuddy.cn/docs/workbuddy/Overview",
        docs_url="https://www.codebuddy.cn/docs/workbuddy/Overview",
        login_probe="codebuddy_text",
        app_names=("WorkBuddy", "CodeBuddy"),
    ),
    AgentDef(
        id="zcode",
        name="ZCode",
        bin="node",
        env_var="PDFXLATE_ZCODE_BIN",
        install_url="https://zcode.z.ai",
        docs_url="https://zcode.z.ai",
        prompt_mode="argv",
        max_prompt_chars=8000,
        needs_node=True,
        script_relative="resources/glm/zcode.cjs",
        standard_relative=("resources/glm/zcode.cjs",),
        app_names=("ZCode",),
        builtin_models=(("default", "Current model"),),
        login_probe="zcode_doctor",
    ),
    AgentDef(
        id="dsh",
        name="DeepSeek Harness",
        bin="dsh",
        env_var="PDFXLATE_DSH_BIN",
        install_url="https://github.com/deepseek-ai/deepseek-harness",
        docs_url="https://github.com/deepseek-ai/deepseek-harness",
        standard_relative=("resources/runtime/cli/bin/dsh.cmd",),
        app_names=("DeepSeek Harness",),
        builtin_models=(("default", "Current model"),),
        login_probe="none",
    ),
)

AGENT_DEFS_BY_ID: dict[str, AgentDef] = {defn.id: defn for defn in AGENT_DEFS}


def get_agent_def(agent: str) -> AgentDef:
    """Return the definition for ``agent`` or raise a message naming the ids."""
    try:
        return AGENT_DEFS_BY_ID[agent]
    except KeyError:
        known = ", ".join(sorted(AGENT_DEFS_BY_ID)) or "(none)"
        raise ValueError(
            f"unknown agent {agent!r}; supported agents: {known}"
        ) from None


__all__ = ["AGENT_DEFS", "AGENT_DEFS_BY_ID", "AgentDef", "get_agent_def"]
