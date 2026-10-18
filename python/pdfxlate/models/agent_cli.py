"""Translation adapter backed by an external agent CLI.

The agent's own login state is the credential: nothing here reads a key, a
token or another program's configuration.  A job spawns the CLI's headless mode
in an empty temporary directory, feeds it a batch of paragraphs, and parses the
answer back into one string per paragraph.

WorkBuddy has a second transport: when only its desktop app is installed, the
same adapter drives the app's signed-in HTTP gateway instead of a CLI process
(see :mod:`pdfxlate.agents.workbuddy_desktop`).  The batch queue, cache key and
error classes stay identical, so nothing downstream has to know which
transport ran.

Everything process-related lives in :mod:`pdfxlate.agents`; this file is the
thin :class:`~pdfxlate.models.base.ModelAdapter` bridge: batching, transport
selection, the extra cache-key fields, and shutting the worker down.
"""

from __future__ import annotations

from typing import Any, Sequence

from pdfxlate.agents import workbuddy_desktop
from pdfxlate.agents.batch import BatchQueue
from pdfxlate.agents.catalog import not_installed_message
from pdfxlate.agents.defs import AgentDef, get_agent_def
from pdfxlate.agents.detect import configured_paths, resolve_agent
from pdfxlate.models.base import ModelAdapter, register


@register
class AgentCliAdapter(ModelAdapter):
    """Drive ``claude -p`` / ``codex exec`` / … or WorkBuddy's desktop app."""

    name = "agent_cli"

    def __init__(
        self,
        profile: Any,
        lang_in: str,
        lang_out: str,
        prompt: str | None = None,
        ignore_cache: bool = False,
    ) -> None:
        # ``_cache_params`` runs inside super().__init__(), so the agent id has
        # to exist before that call.
        self.agent = str(getattr(profile, "agent", None) or "").strip()
        if not self.agent:
            raise ValueError(
                "provider of kind 'agent_cli' has no 'agent'; set it to one of: "
                "claude, codex, workbuddy, zcode, dsh"
            )
        self._defn: AgentDef = get_agent_def(self.agent)
        self._custom_prompt = prompt or None
        self._model = getattr(profile, "model", None)

        resolved = resolve_agent(self._defn, configured_paths())
        if not resolved.installed:
            # The cache may predate an installation the user just performed.
            resolved = resolve_agent(self._defn, configured_paths(), refresh=True)
        self._backend = "cli"
        if not resolved.installed or not resolved.command:
            if (
                self.agent == "workbuddy"
                and workbuddy_desktop.workbuddy_app_path() is not None
            ):
                # The desktop app is a backend in its own right.  The
                # standalone CLI still wins when it exists (checked above), so
                # an installed `codebuddy`/`cbc` keeps its old behaviour.
                self._backend = "desktop"
            else:
                from pdfxlate.agents.runtime import AgentNotInstalledError

                raise AgentNotInstalledError(not_installed_message(resolved))
        self._resolved = resolved

        super().__init__(profile, lang_in, lang_out, prompt, ignore_cache)

        # A custom template cannot be wrapped in the batch JSON contract, so it
        # runs one paragraph per invocation.
        max_items = 1 if self._custom_prompt else self._defn.max_batch_items
        self._queue = BatchQueue(
            self._run_batch,
            max_items=max_items,
            max_chars=self._defn.batch_char_limit,
            name=f"agent-{self.agent}",
        )

    # --- adapter contract ----------------------------------------------
    def do_translate(self, text: str) -> str:
        return self._queue.submit(text)

    def _run_batch(self, texts: Sequence[str]) -> list[str]:
        # Imported lazily: ``pdfxlate.agents.runtime`` imports this module's
        # base class, and a top-level import here would close the cycle when the
        # agents package is imported first.
        from pdfxlate.agents.runtime import (
            DEFAULT_TIMEOUT,
            AgentAuthError,
            AgentOutputError,
            AgentProcessError,
            run_batch,
        )

        if self._backend == "desktop":
            try:
                return workbuddy_desktop.translate_batch(
                    self._model,
                    texts,
                    self.lang_out,
                    custom_prompt=self._custom_prompt,
                    timeout=DEFAULT_TIMEOUT,
                    cancel_event=self.cancellation_event,
                    lang_in=self.lang_in,
                )
            except workbuddy_desktop.WorkBuddyError as exc:
                # Map the desktop failure onto the pipeline's own classes so a
                # signed-out account stays permanent and a network problem is
                # retried like any other retryable agent failure.
                if exc.kind in {
                    "auth",
                    "not_logged_in",
                    "app_missing",
                    "key_unavailable",
                }:
                    raise AgentAuthError(str(exc)) from exc
                if exc.kind == "output":
                    raise AgentOutputError(str(exc)) from exc
                raise AgentProcessError(str(exc)) from exc

        return run_batch(
            self._defn,
            self._resolved.command or (),
            self._model,
            texts,
            self.lang_out,
            custom_prompt=self._custom_prompt,
            timeout=DEFAULT_TIMEOUT,
            cancel_event=self.cancellation_event,
            lang_in=self.lang_in,
        )

    def _cache_params(self) -> dict[str, Any]:
        # Only the agent adapter adds these: an HTTP provider's cache key must
        # not change just because this backend learned about agents.
        params = super()._cache_params()
        params["agent"] = getattr(self, "agent", None) or getattr(
            self.profile, "agent", None
        )
        # Bumped whenever the batch prompt/parsing contract changes, so old
        # cached translations from a different contract are not reused.
        params["agent_batch"] = "v1"
        return params

    def close(self) -> None:
        self._queue.close()


__all__ = ["AgentCliAdapter"]
