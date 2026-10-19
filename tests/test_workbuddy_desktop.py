"""Tests for the WorkBuddy desktop backend.

Nothing here talks to a live gateway, starts the desktop app, or reads a real
credential: envelopes are sealed with a synthetic key, the HTTP layer is a fake
``requests``, and every probe is monkeypatched.  The security invariant is the
point of several tests — no token or secret may appear in an error or a repr.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdfxlate.agents as agents_pkg  # noqa: E402
from pdfxlate import sidecar  # noqa: E402
from pdfxlate.agents import catalog, detect, runtime, workbuddy_desktop as wd  # noqa: E402
from pdfxlate.agents.defs import get_agent_def  # noqa: E402
from pdfxlate.agents.detect import ResolvedAgent  # noqa: E402
from pdfxlate.models import agent_cli  # noqa: E402

SECRET = "synthetic-at-rest-secret"
KEY = hashlib.sha256(SECRET.encode("utf-8")).digest()
KEY_ID = wd.derive_key_id(KEY)
AT_REST = wd.AtRestKey(key=KEY, key_id=KEY_ID)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _seal(
    value: str,
    *,
    key: bytes = KEY,
    key_id: str = KEY_ID,
    suite: int = 1,
) -> dict:
    nonce = bytes(range(12))
    sealed = AESGCM(key).encrypt(nonce, value.encode("utf-8"), wd.field_aad(suite, key_id))
    envelope = {
        "suite": suite,
        "keyId": key_id,
        "nonce": _b64(nonce),
        "authTag": _b64(sealed[-16:]),
        "ciphertext": _b64(sealed[:-16]),
    }
    return {"$wbEncrypted": 1, "envelope": _b64(json.dumps(envelope).encode("utf-8"))}


def _credential(
    domain: str = "www.workbuddy.cn",
    access: str = "access-token",
    refresh: str = "refresh-token",
    uid: str = "user-1",
    enterprise: str | None = "enterprise-1",
    expires_at_ms: int = 0,
) -> wd.WorkBuddyCredential:
    return wd.WorkBuddyCredential(
        access_token=access,
        refresh_token=refresh,
        domain=domain,
        uid=uid,
        enterprise_id=enterprise,
        expires_at_ms=expires_at_ms,
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    wd.clear_caches()
    monkeypatch.delenv(wd.APP_EXECUTABLE_ENV, raising=False)
    monkeypatch.delenv(wd.AUTH_FILE_ENV, raising=False)
    monkeypatch.delenv(wd.PROXY_ENV, raising=False)
    # These tests must not write a real agents-snapshot.json into the user's
    # config directory; the snapshot module has its own dedicated tests.
    monkeypatch.setattr(agents_pkg.snapshot, "save", lambda *_args, **_kwargs: None)
    yield
    wd.clear_caches()
    detect._RESOLVE_CACHE.clear()
    detect._DETECT_CACHE.clear()
    catalog._MODELS_CACHE.clear()


class _FakeSession:
    """Stands in for ``requests.Session`` in the HTTP tests."""

    def __init__(self, *, get=None, post=None):
        self._get = get
        self._post = post
        self.closed = False
        self.trust_env = True
        self.proxies: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def close(self):
        self.closed = True

    def get(self, *args, **kwargs):
        if self._get is None:
            raise AssertionError("unexpected WorkBuddy GET")
        return self._get(*args, **kwargs)

    def post(self, *args, **kwargs):
        if self._post is None:
            raise AssertionError("unexpected WorkBuddy POST")
        return self._post(*args, **kwargs)


def _patch_http(monkeypatch, *, get=None, post=None) -> _FakeSession:
    session = _FakeSession(get=get, post=post)
    monkeypatch.setattr(wd, "_http_session", lambda: session)
    return session


def test_http_session_ignores_the_system_proxy():
    session = wd._http_session()
    try:
        assert session.trust_env is False
        assert session.proxies == {}
    finally:
        session.close()


def test_http_session_honours_an_explicit_proxy(monkeypatch):
    monkeypatch.setenv(wd.PROXY_ENV, "http://127.0.0.1:7999")
    session = wd._http_session()
    try:
        assert session.trust_env is False
        assert session.proxies == {
            "http": "http://127.0.0.1:7999",
            "https": "http://127.0.0.1:7999",
        }
    finally:
        session.close()


def test_proxy_error_names_the_explicit_override():
    error = wd._request_error(requests.exceptions.ProxyError("proxy is down"))
    assert wd.PROXY_ENV in str(error)
    assert "proxy" in str(error).lower()


# --- auth parsing / decryption --------------------------------------------


def test_parse_auth_opens_encrypted_fields_and_hides_tokens():
    document = {
        "auth": {
            "accessToken": _seal("access-123"),
            "refreshToken": _seal("refresh-456"),
            "domain": _seal("www.workbuddy.cn"),
            "expiresAt": 1893456000,
            "lastRefreshTime": 1890000000000,
        },
        "account": {
            "uid": _seal("uid-1"),
            "enterpriseId": _seal("ent-1"),
            "nickname": _seal("Nick"),
            "uin": _seal("12345"),
        },
    }
    credential = wd.parse_auth(json.dumps(document), AT_REST, file_path="auth.info")

    assert credential is not None
    assert credential.access_token == "access-123"
    assert credential.refresh_token == "refresh-456"
    assert credential.domain == "www.workbuddy.cn"
    assert credential.uid == "uid-1"
    assert credential.enterprise_id == "ent-1"
    assert credential.nickname == "Nick"
    assert credential.uin == "12345"
    assert credential.expires_at_ms == 1893456000 * 1000
    assert credential.region == "cn"
    # A token must never leak through a repr that ends up in a log line.
    assert "access-123" not in repr(credential)
    assert "refresh-456" not in repr(credential)


def test_parse_auth_rejects_a_mismatched_key_id():
    document = {"auth": {"accessToken": _seal("token", key_id="0123456789abcdef")}}
    with pytest.raises(wd.WorkBuddyError) as caught:
        wd.parse_auth(json.dumps(document), AT_REST)
    assert caught.value.kind == "key_unavailable"


def test_parse_auth_rejects_a_failed_tag_without_leaking_plaintext():
    document = {"auth": {"accessToken": _seal("token", key=hashlib.sha256(b"other").digest())}}
    with pytest.raises(wd.WorkBuddyError) as caught:
        wd.parse_auth(json.dumps(document), AT_REST)
    assert caught.value.kind == "key_unavailable"
    assert "token" not in str(caught.value)


def test_parse_auth_accepts_the_flat_plain_document():
    document = {
        "accessToken": "plain-access",
        "refreshToken": "plain-refresh",
        "domain": "www.workbuddy.ai",
        "uid": "u",
        "enterpriseId": "e",
    }
    credential = wd.parse_auth(json.dumps(document))
    assert credential is not None
    assert credential.access_token == "plain-access"
    assert credential.region == "global"


def test_parse_auth_returns_none_without_a_token():
    assert wd.parse_auth('{"auth": {}}', AT_REST) is None
    assert wd.parse_auth("not json", AT_REST) is None
    assert wd.parse_auth('{"auth": {"accessToken": ""}}', AT_REST) is None


def test_parse_auth_raises_when_encrypted_without_a_key():
    document = {"auth": {"accessToken": _seal("token")}}
    with pytest.raises(wd.WorkBuddyError) as caught:
        wd.parse_auth(json.dumps(document), None)
    assert caught.value.kind == "key_unavailable"


def test_expiry_normalizes_seconds_and_milliseconds():
    assert wd.expiry_to_ms(1893456000) == 1893456000 * 1000
    assert wd.expiry_to_ms(1893456000000) == 1893456000000
    assert wd.expiry_to_ms(0) == 0
    assert wd.expiry_to_ms("nope") == 0


def test_derive_at_rest_key_hashes_the_secret_string():
    key = wd.derive_at_rest_key(json.dumps({"atRestSecretKey": SECRET}))
    assert key.key == KEY
    assert key.key_id == KEY_ID
    with pytest.raises(wd.WorkBuddyError):
        wd.derive_at_rest_key("not json")
    with pytest.raises(wd.WorkBuddyError):
        wd.derive_at_rest_key(json.dumps({"atRestSecretKey": ""}))


# --- model catalog ---------------------------------------------------------


def _catalog_document(count: int = 25, cli_ids: list[str] | None = None) -> dict:
    models = [
        {
            "id": f"model-{index}",
            "name": f"Model {index}",
            "credits": "x0.00 credits" if index == 0 else "x0.29 credits",
            "maxInputTokens": 200000,
            "maxOutputTokens": 8192,
        }
        for index in range(count)
    ]
    document: dict = {"models": models}
    if cli_ids is not None:
        document["agents"] = [{"name": "cli", "models": cli_ids}]
    return document


def test_select_cli_models_follows_the_roster_order():
    selected = wd.select_cli_models(_catalog_document(cli_ids=["model-2", "model-0", "model-4"]))
    assert [model["id"] for model in selected] == ["model-2", "model-0", "model-4"]
    assert selected[1]["name"] == "Model 0"
    assert selected[1]["credits"] == "x0.00 credits"


def test_select_cli_models_without_a_roster_returns_every_model():
    selected = wd.select_cli_models(_catalog_document(count=4))
    assert [model["id"] for model in selected] == [f"model-{i}" for i in range(4)]


def test_select_cli_models_rejects_an_empty_catalog():
    with pytest.raises(wd.WorkBuddyError) as caught:
        wd.select_cli_models({"models": []})
    assert caught.value.kind == "transport"


def test_model_label_includes_the_credit_price():
    assert wd.model_label("Hy3", "x0.00 credits") == "Hy3 · x0.00 credits"
    assert wd.model_label("Hy3", "") == "Hy3"


# --- model fetch (fake HTTP) ----------------------------------------------


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        document: object | None = None,
        *,
        text: str = "",
        lines: list[str] | None = None,
        not_json: bool = False,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> None:
        self.status_code = status
        self._document = document
        self._text = text
        self._lines = lines or []
        self._not_json = not_json
        self.headers = headers or {}
        self.content = content if content is not None else b""
        self.closed = False

    def json(self):
        if self._not_json:
            raise ValueError("not json")
        return self._document

    @property
    def text(self) -> str:
        return self._text

    def iter_lines(self, decode_unicode: bool = False):
        # Mimic requests: its decode_unicode path guesses ISO-8859-1 when the
        # response has no charset, which is exactly the bug this suite covers.
        for line in self._lines:
            raw = line.encode("utf-8")
            yield raw.decode("latin-1") if decode_unicode else raw

    def close(self) -> None:
        self.closed = True


def _envelope(document: dict) -> dict:
    return {"code": 0, "data": document}


def test_fetch_models_cn_uses_v3_and_cli_headers(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, dict(headers or {})))
        return FakeResponse(document=_envelope(_catalog_document(cli_ids=["model-1"])))

    _patch_http(monkeypatch, get=fake_get)
    models = wd.fetch_models(_credential())

    assert [model["id"] for model in models] == ["model-1"]
    url, headers = calls[0]
    assert url == "https://copilot.tencent.com/v3/config"
    assert headers["Authorization"] == "Bearer access-token"
    assert headers["User-Agent"] == "CLI/2.63.2 CodeBuddy/2.63.2"
    assert headers["Origin"] == "https://www.codebuddy.cn"


def test_fetch_models_cn_falls_back_to_the_personal_catalog(monkeypatch):
    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/v3/config"):
            return FakeResponse(500, text="boom")
        return FakeResponse(document=_envelope(_catalog_document(cli_ids=["model-3"])))

    _patch_http(monkeypatch, get=fake_get)
    models = wd.fetch_models(_credential())

    assert [model["id"] for model in models] == ["model-3"]
    assert calls == [
        "https://copilot.tencent.com/v3/config",
        "https://copilot.tencent.com/v2/enterprises/personal/models",
    ]


def test_fetch_models_global_uses_the_desktop_channel(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, dict(headers or {})))
        return FakeResponse(document=_envelope(_catalog_document(cli_ids=["model-2"])))

    _patch_http(monkeypatch, get=fake_get)
    models = wd.fetch_models(_credential(domain="www.workbuddy.ai"))

    assert [model["id"] for model in models] == ["model-2"]
    url, headers = calls[0]
    assert url == "https://www.workbuddy.ai/v3/config"
    assert headers["User-Agent"] == "WorkBuddy/5.5.2"
    assert headers["X-Product"] == "SaaS"
    assert headers["X-Domain"] == "www.workbuddy.ai"
    assert "Origin" not in headers


# --- chat / SSE ------------------------------------------------------------


def _sse(*chunks: str) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        lines.append(f'data: {json.dumps({"choices": [{"delta": {"content": chunk}}]})}')
    lines.append("data: [DONE]")
    return lines


def test_chat_sends_the_required_headers_and_parses_sse(monkeypatch):
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        calls.append(
            {"url": url, "headers": dict(headers or {}), "body": json, "timeout": timeout}
        )
        return FakeResponse(lines=_sse("你好", "，世界"))

    _patch_http(monkeypatch, post=fake_post)
    answer = wd.chat(_credential(), "model-1", "Translate: Hello")

    assert answer == "你好，世界"
    call = calls[0]
    assert call["url"] == "https://copilot.tencent.com/v2/chat/completions"
    headers = call["headers"]
    assert headers["Authorization"] == "Bearer access-token"
    assert headers["X-User-Id"] == "user-1"
    assert headers["X-Enterprise-Id"] == "enterprise-1"
    assert headers["X-Domain"] == "www.workbuddy.cn"
    assert headers["X-Product"] == "SaaS"
    conversation_ids = {
        headers["X-Conversation-ID"],
        headers["X-Conversation-Request-ID"],
        headers["X-Conversation-Message-ID"],
    }
    assert len(conversation_ids) == 3
    for value in conversation_ids:
        uuid.UUID(value)  # well-formed, not necessarily persisted

    body = call["body"]
    assert body["model"] == "model-1"
    assert body["stream"] is True
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1] == {"role": "user", "content": "Translate: Hello"}


def test_chat_decodes_utf8_sse_bytes(monkeypatch):
    """The gateway sends UTF-8 bytes; requests must not Latin-1 them."""

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        return FakeResponse(lines=_sse("你好", "，世界"))

    _patch_http(monkeypatch, post=fake_post)
    answer = wd.chat(_credential(), "model-1", "Translate: Hello")

    assert answer == "你好，世界"


def test_json_answer_uses_utf8_content_over_latin1_text():
    payload = {"choices": [{"message": {"content": "你好，世界"}}]}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = FakeResponse(
        document=None,
        content=raw,
        text=raw.decode("latin-1"),
        headers={"content-type": "application/json"},
    )

    assert wd._read_json_answer(response, 200) == "你好，世界"


def test_chat_reuses_one_keep_alive_session(monkeypatch):
    sessions: list[_FakeSession] = []

    def factory():
        session = _FakeSession(
            post=lambda *args, **kwargs: FakeResponse(lines=_sse("ok"))
        )
        sessions.append(session)
        return session

    monkeypatch.setattr(wd, "_http_session", factory)
    wd.close_chat_sessions()
    assert wd.chat(_credential(), "model-1", "first") == "ok"
    assert wd.chat(_credential(), "model-1", "second") == "ok"
    assert len(sessions) == 1
    assert sessions[0].closed is False


def test_chat_drops_the_session_after_a_connection_error(monkeypatch):
    sessions: list[_FakeSession] = []
    attempts = {"n": 0}

    def post(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise requests.ConnectionError("gateway dropped the connection")
        return FakeResponse(lines=_sse("ok"))

    def factory():
        session = _FakeSession(post=post)
        sessions.append(session)
        return session

    monkeypatch.setattr(wd, "_http_session", factory)
    wd.close_chat_sessions()
    with pytest.raises(wd.WorkBuddyError):
        wd.chat(_credential(), "model-1", "first")
    assert wd.chat(_credential(), "model-1", "second") == "ok"
    assert len(sessions) == 2


def test_chat_honours_the_no_identity_headers(monkeypatch):
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        calls.append(dict(headers or {}))
        return FakeResponse(lines=_sse("ok"))

    _patch_http(monkeypatch, post=fake_post)
    wd.chat(_credential(uid="", enterprise=None, domain=""), "model-1", "hi")
    headers = calls[0]
    assert headers["X-No-User-Id"] == "1"
    assert headers["X-No-Enterprise-Id"] == "1"
    assert headers["X-No-Department-Info"] == "1"
    assert "X-User-Id" not in headers


def test_chat_refreshes_a_rejected_credential_once(monkeypatch):
    chat_calls: list[dict] = []
    refresh_calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        if url.endswith("/v2/plugin/auth/token/refresh"):
            refresh_calls.append(dict(headers or {}))
            return FakeResponse(
                document={
                    "code": 0,
                    "data": {
                        "accessToken": "new-access",
                        "refreshToken": "new-refresh",
                        "expiresIn": 3600,
                        "domain": "www.workbuddy.cn",
                    },
                }
            )
        chat_calls.append(dict(headers or {}))
        if len(chat_calls) == 1:
            return FakeResponse(401, text='{"code":12153,"msg":"session not found"}')
        return FakeResponse(lines=_sse("translated"))

    _patch_http(monkeypatch, post=fake_post)
    answer = wd.chat(_credential(), "model-1", "Translate: Hello")

    assert answer == "translated"
    assert len(chat_calls) == 2
    assert chat_calls[0]["Authorization"] == "Bearer access-token"
    assert chat_calls[1]["Authorization"] == "Bearer new-access"
    assert refresh_calls[0]["X-Refresh-Token"] == "refresh-token"
    assert refresh_calls[0]["X-Auth-Refresh-Source"] == "workbuddy"
    # The refresh call never carries the access token.
    assert "Authorization" not in refresh_calls[0]


def test_chat_reports_credit_exhaustion_readably(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        return FakeResponse(429, text='{"code":6004,"msg":"积分不足，额度用尽"}')

    _patch_http(monkeypatch, post=fake_post)
    with pytest.raises(wd.WorkBuddyError) as caught:
        wd.chat(_credential(), "model-1", "Translate: Hello")
    assert caught.value.kind == "rate_limit"
    assert "access-token" not in str(caught.value)


def test_chat_classifies_a_json_200_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        return FakeResponse(
            document={"code": 14018, "msg": "Credits exhausted"},
            headers={"content-type": "application/json"},
        )

    _patch_http(monkeypatch, post=fake_post)
    with pytest.raises(wd.WorkBuddyError) as caught:
        wd.chat(_credential(), "model-1", "Translate: Hello")
    assert caught.value.kind == "rate_limit"


def test_chat_accepts_a_json_completion(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        return FakeResponse(
            document={
                "choices": [
                    {"message": {"role": "assistant", "content": "translated"}}
                ]
            },
            headers={"content-type": "application/json"},
        )

    _patch_http(monkeypatch, post=fake_post)
    assert wd.chat(_credential(), "model-1", "hi") == "translated"


def test_chat_rejects_an_empty_answer(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        return FakeResponse(lines=["data: [DONE]"])

    _patch_http(monkeypatch, post=fake_post)
    with pytest.raises(wd.WorkBuddyError) as caught:
        wd.chat(_credential(), "model-1", "Translate: Hello")
    assert caught.value.kind == "output"


def test_chat_stops_when_cancelled(monkeypatch):
    import asyncio
    import threading

    cancel = threading.Event()
    cancel.set()

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        return FakeResponse(lines=_sse("never"))

    _patch_http(monkeypatch, post=fake_post)
    with pytest.raises(asyncio.CancelledError):
        wd.chat(_credential(), "model-1", "hi", cancel_event=cancel)


# --- translate_batch -------------------------------------------------------


def test_translate_batch_parses_the_translations_object(monkeypatch):
    monkeypatch.setattr(wd, "workbuddy_credential", lambda **kwargs: _credential())
    prompts: list[str] = []

    def fake_chat(credential, model, prompt, **kwargs):
        prompts.append(prompt)
        return '{"translations": ["甲", "乙"]}'

    monkeypatch.setattr(wd, "chat", fake_chat)
    assert wd.translate_batch("model-1", ["one", "two"], "zh") == ["甲", "乙"]
    assert len(prompts) == 1
    assert '"one"' in prompts[0] and '"two"' in prompts[0]


def test_translate_batch_retries_strictly_then_splits(monkeypatch):
    monkeypatch.setattr(wd, "workbuddy_credential", lambda **kwargs: _credential())
    prompts: list[str] = []

    def fake_chat(credential, model, prompt, **kwargs):
        prompts.append(prompt)
        if len(prompts) <= 2:
            return "not-json"
        return "single"

    monkeypatch.setattr(wd, "chat", fake_chat)
    assert wd.translate_batch("model-1", ["one", "two"], "zh") == ["single", "single"]
    assert len(prompts) == 4
    assert "IMPORTANT" in prompts[1]


def test_translate_batch_custom_prompt_runs_one_paragraph_per_call(monkeypatch):
    monkeypatch.setattr(wd, "workbuddy_credential", lambda **kwargs: _credential())
    prompts: list[str] = []

    def fake_chat(credential, model, prompt, **kwargs):
        prompts.append(prompt)
        return f"<{len(prompts)}>"

    monkeypatch.setattr(wd, "chat", fake_chat)
    assert wd.translate_batch(
        "model-1", ["one", "two"], "zh", custom_prompt="Translate ${text} to ${lang_out}"
    ) == ["<1>", "<2>"]
    assert prompts == [
        "Translate one to zh",
        "Translate two to zh",
    ]


# --- credential file / app probe ------------------------------------------


def _fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "WorkBuddy.exe"
    app.write_text("", encoding="utf-8")
    return app


def _auth_file(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "workbuddy-desktop.info"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_workbuddy_credential_reads_the_auth_file_and_caches(monkeypatch, tmp_path):
    app = _fake_app(tmp_path)
    auth = _auth_file(
        tmp_path,
        {
            "auth": {
                "accessToken": _seal("access-123"),
                "refreshToken": _seal("refresh-456"),
                "domain": _seal("www.workbuddy.cn"),
                "expiresAt": int(time.time()) + 3600,
            },
            "account": {"uid": _seal("uid-1")},
        },
    )
    monkeypatch.setenv(wd.APP_EXECUTABLE_ENV, str(app))
    monkeypatch.setenv(wd.AUTH_FILE_ENV, str(auth))
    key_calls: list[Path] = []

    def fake_key(path=None):
        key_calls.append(Path(path) if path is not None else Path(""))
        return AT_REST

    monkeypatch.setattr(wd, "read_at_rest_key", fake_key)

    first = wd.workbuddy_credential()
    second = wd.workbuddy_credential()

    assert first.access_token == "access-123"
    assert second.access_token == "access-123"
    assert len(key_calls) == 1


def test_workbuddy_credential_refreshes_without_rewriting_the_file(
    monkeypatch, tmp_path
):
    app = _fake_app(tmp_path)
    auth = _auth_file(
        tmp_path,
        {
            "auth": {
                "accessToken": _seal("old-access"),
                "refreshToken": _seal("old-refresh"),
                "domain": _seal("www.workbuddy.cn"),
                "expiresAt": 1,
            }
        },
    )
    before = auth.read_text(encoding="utf-8")
    monkeypatch.setenv(wd.APP_EXECUTABLE_ENV, str(app))
    monkeypatch.setenv(wd.AUTH_FILE_ENV, str(auth))
    monkeypatch.setattr(wd, "read_at_rest_key", lambda path=None: AT_REST)

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        assert url.endswith("/v2/plugin/auth/token/refresh")
        return FakeResponse(
            document={
                "code": 0,
                "data": {"accessToken": "new-access", "expiresIn": 3600},
            }
        )

    _patch_http(monkeypatch, post=fake_post)
    credential = wd.workbuddy_credential()

    assert credential.access_token == "new-access"
    assert credential.refresh_token == "old-refresh"
    assert auth.read_text(encoding="utf-8") == before


def test_desktop_status_reports_a_missing_login(monkeypatch, tmp_path):
    app = _fake_app(tmp_path)
    monkeypatch.setenv(wd.APP_EXECUTABLE_ENV, str(app))
    monkeypatch.setenv(wd.AUTH_FILE_ENV, str(tmp_path / "absent.info"))

    status = wd.desktop_status()
    assert status["available"] is True
    assert status["auth"] == "missing"
    assert status["app_path"] == str(app)


def test_desktop_status_reports_an_unreadable_credential(monkeypatch, tmp_path):
    app = _fake_app(tmp_path)
    auth = _auth_file(tmp_path, {"auth": {"accessToken": _seal("token")}})
    monkeypatch.setenv(wd.APP_EXECUTABLE_ENV, str(app))
    monkeypatch.setenv(wd.AUTH_FILE_ENV, str(auth))

    def no_key(path=None):
        raise wd.WorkBuddyError("key_unavailable", "no key")

    monkeypatch.setattr(wd, "read_at_rest_key", no_key)
    status = wd.desktop_status()
    assert status["available"] is True
    assert status["auth"] == "unknown"
    assert status["error"] == "no key"


def test_desktop_status_without_the_app(monkeypatch, tmp_path):
    monkeypatch.setenv(wd.APP_EXECUTABLE_ENV, str(tmp_path / "missing.exe"))
    status = wd.desktop_status()
    assert status["available"] is False
    assert status["auth"] == "unknown"


# --- catalog integration ---------------------------------------------------


def _app_only_resolution() -> ResolvedAgent:
    return ResolvedAgent(
        defn=get_agent_def("workbuddy"),
        installed=False,
        command=None,
        path=None,
        reason="app_only",
    )


def test_list_models_workbuddy_uses_the_desktop_catalog(monkeypatch):
    monkeypatch.setattr(
        catalog.detect, "resolve_agent", lambda *args, **kwargs: _app_only_resolution()
    )
    monkeypatch.setattr(catalog.detect, "configured_paths", lambda: {})
    monkeypatch.setattr(
        catalog.workbuddy_desktop,
        "desktop_status",
        lambda: {"available": True, "auth": "ok", "app_path": "C:/WorkBuddy.exe"},
    )
    monkeypatch.setattr(
        catalog.workbuddy_desktop,
        "workbuddy_credential",
        lambda refresh=False: _credential(),
    )
    monkeypatch.setattr(
        catalog.workbuddy_desktop,
        "fetch_models",
        lambda credential: [
            {"id": "model-1", "name": "Hy3", "credits": "x0.00 credits"},
            {"id": "model-2", "name": "Kimi", "credits": "x0.77 credits"},
        ],
    )
    catalog._MODELS_CACHE.clear()

    result = catalog.list_models("workbuddy", refresh=True)

    assert result["source"] == "desktop"
    assert result["desktop"]["auth"] == "ok"
    assert result["models"] == [
        {"id": "model-1", "label": "Hy3 · x0.00 credits", "credit": "x0.00 credits"},
        {"id": "model-2", "label": "Kimi · x0.77 credits", "credit": "x0.77 credits"},
    ]


def test_list_models_workbuddy_reports_a_signed_out_desktop(monkeypatch):
    monkeypatch.setattr(
        catalog.detect, "resolve_agent", lambda *args, **kwargs: _app_only_resolution()
    )
    monkeypatch.setattr(catalog.detect, "configured_paths", lambda: {})
    monkeypatch.setattr(
        catalog.workbuddy_desktop,
        "desktop_status",
        lambda: {"available": True, "auth": "ok", "app_path": "C:/WorkBuddy.exe"},
    )

    def signed_out(refresh=False):
        raise wd.WorkBuddyError("not_logged_in", "the app is not signed in")

    monkeypatch.setattr(catalog.workbuddy_desktop, "workbuddy_credential", signed_out)
    catalog._MODELS_CACHE.clear()

    result = catalog.list_models("workbuddy", refresh=True)

    assert result["source"] == "fallback"
    assert result["desktop"]["available"] is True
    assert "not signed in" in result["error"]


def test_list_models_workbuddy_prefers_an_installed_cli(monkeypatch):
    resolved = ResolvedAgent(
        defn=get_agent_def("workbuddy"),
        installed=True,
        command=("codebuddy",),
        path="codebuddy",
        reason=None,
    )
    monkeypatch.setattr(catalog.detect, "resolve_agent", lambda *args, **kwargs: resolved)
    monkeypatch.setattr(catalog.detect, "configured_paths", lambda: {})

    def must_not_probe():
        raise AssertionError("the desktop branch must not run when the CLI exists")

    monkeypatch.setattr(
        catalog.workbuddy_desktop, "desktop_status", lambda: must_not_probe()
    )
    monkeypatch.setattr(
        catalog,
        "_probe_models",
        lambda agent, resolved: ([{"id": "cli-model", "label": "CLI model"}], None),
    )
    catalog._MODELS_CACHE.clear()

    result = catalog.list_models("workbuddy", refresh=True)
    assert result == {
        "models": [{"id": "cli-model", "label": "CLI model"}],
        "source": "cli",
    }


# --- adapter ---------------------------------------------------------------


def _profile(agent: str = "workbuddy", model: str = "model-1"):
    from pdfxlate import config as config_mod

    return config_mod.ResolvedModel(
        kind="agent_cli",
        agent=agent,
        provider=agent,
        alias=model,
        model=model,
    )


def _build_desktop_adapter(monkeypatch, translate):
    resolved = _app_only_resolution()
    monkeypatch.setattr(agent_cli, "resolve_agent", lambda *args, **kwargs: resolved)
    monkeypatch.setattr(agent_cli, "configured_paths", lambda: {})
    monkeypatch.setattr(
        agent_cli.workbuddy_desktop, "workbuddy_app_path", lambda: Path("WorkBuddy.exe")
    )
    monkeypatch.setattr(agent_cli.workbuddy_desktop, "translate_batch", translate)
    return agent_cli.AgentCliAdapter(_profile(), "en", "zh")


def test_adapter_uses_the_desktop_backend_when_the_cli_is_missing(monkeypatch):
    calls: list[tuple] = []

    def fake_translate(model, texts, lang_out, **kwargs):
        calls.append((model, list(texts), lang_out, kwargs))
        return [text.upper() for text in texts]

    adapter = _build_desktop_adapter(monkeypatch, fake_translate)
    try:
        assert adapter._backend == "desktop"
        assert adapter.do_translate("hello") == "HELLO"
    finally:
        adapter.close()

    assert calls
    assert calls[0][0] == "model-1"
    assert calls[0][2] == "zh"


def test_adapter_maps_a_signed_out_desktop_to_auth_error(monkeypatch):
    def fake_translate(model, texts, lang_out, **kwargs):
        raise wd.WorkBuddyError("not_logged_in", "sign in to WorkBuddy")

    adapter = _build_desktop_adapter(monkeypatch, fake_translate)
    try:
        with pytest.raises(runtime.AgentAuthError, match="sign in"):
            adapter.do_translate("hello")
    finally:
        adapter.close()


def test_adapter_maps_a_desktop_output_error(monkeypatch):
    def fake_translate(model, texts, lang_out, **kwargs):
        raise wd.WorkBuddyError("output", "could not parse")

    adapter = _build_desktop_adapter(monkeypatch, fake_translate)
    try:
        with pytest.raises(runtime.AgentOutputError, match="could not parse"):
            adapter.do_translate("hello")
    finally:
        adapter.close()


def test_adapter_keeps_the_cli_when_it_is_installed(monkeypatch):
    resolved = ResolvedAgent(
        defn=get_agent_def("workbuddy"),
        installed=True,
        command=("fake-codebuddy",),
        path="fake-codebuddy",
        reason=None,
    )
    monkeypatch.setattr(agent_cli, "resolve_agent", lambda *args, **kwargs: resolved)
    monkeypatch.setattr(agent_cli, "configured_paths", lambda: {})
    monkeypatch.setattr(
        agent_cli.workbuddy_desktop, "workbuddy_app_path", lambda: Path("WorkBuddy.exe")
    )
    seen: list[tuple] = []

    def fake_run(defn, command, model, texts, lang_out, **kwargs):
        seen.append((defn.id, command, model, list(texts)))
        return [text.upper() for text in texts]

    monkeypatch.setattr(runtime, "run_batch", fake_run)
    adapter = agent_cli.AgentCliAdapter(_profile(), "en", "zh")
    try:
        assert adapter._backend == "cli"
        assert adapter.do_translate("hello") == "HELLO"
    finally:
        adapter.close()
    assert seen and seen[0][1] == ("fake-codebuddy",)


# --- sidecar ---------------------------------------------------------------


def test_agents_models_passes_the_desktop_shape(monkeypatch):
    def fake_list(agent, refresh=False, paths=None):
        return {
            "models": [
                {
                    "id": "model-1",
                    "label": "Hy3 · x0.00 credits",
                    "credit": "x0.00 credits",
                }
            ],
            "source": "desktop",
            "desktop": {"available": True, "auth": "ok", "app_path": "C:/WorkBuddy.exe"},
        }

    monkeypatch.setattr(catalog, "list_models", fake_list)
    result = sidecar.method_agents_models({"agent": "workbuddy", "refresh": True})
    assert result["source"] == "desktop"
    assert result["models"][0]["credit"] == "x0.00 credits"
    assert result["desktop"]["auth"] == "ok"


def test_agents_list_includes_the_desktop_status(monkeypatch):
    # `list_agents(refresh=True)` saves its result; this test uses fake blank
    # detections, so it must not replace the user's real snapshot.
    monkeypatch.setattr(agents_pkg.snapshot, "save", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agents_pkg.detect,
        "detect_agent",
        lambda defn, paths, refresh: detect.blank_detection(defn),
    )

    def fake_models(agent, refresh=False, paths=None):
        if agent == "workbuddy":
            return {
                "models": [],
                "source": "desktop",
                "desktop": {"available": True, "auth": "missing", "app_path": "C:/W.exe"},
            }
        return {"models": [], "source": "fallback"}

    monkeypatch.setattr(agents_pkg.catalog, "list_models", fake_models)
    entries = agents_pkg.list_agents(refresh=True)
    workbuddy = next(entry for entry in entries if entry["id"] == "workbuddy")
    assert workbuddy["desktop"]["auth"] == "missing"
    assert workbuddy["models_source"] == "desktop"
    # Other agents keep the old shape: no desktop key at all.
    assert "desktop" not in next(entry for entry in entries if entry["id"] == "claude")
