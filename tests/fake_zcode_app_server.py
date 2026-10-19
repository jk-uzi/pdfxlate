"""A fake ZCode app-server for tests: one JSON object per line, no `jsonrpc`."""

from __future__ import annotations

import json
import os
import sys
import time


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    # The real app-server speaks UTF-8; keep the fake deterministic on Windows
    # consoles too.
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    send({"method": "startup/storageState", "params": {"phase": "ready"}})
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        request_id = request.get("id")
        method = request.get("method")
        if method == "runtime/capabilities":
            send({"id": request_id, "result": {"independentPlanState": True}})
            continue
        if method == "provider/testModelConnectivity":
            target = os.environ.get("FAKE_ZCODE_SELECTION_FILE")
            if target:
                with open(target, "w", encoding="utf-8") as handle:
                    json.dump(request.get("params"), handle, ensure_ascii=False)
            if os.environ.get("FAKE_ZCODE_ERROR"):
                send(
                    {
                        "id": request_id,
                        "error": {"code": -1, "message": "boom"},
                    }
                )
                continue
            delay = float(os.environ.get("FAKE_ZCODE_DELAY", "0") or 0)
            if delay:
                time.sleep(delay)
            send({"id": request_id, "result": {"success": True}})
            continue
        if method == "workspace/generateText":
            target = os.environ.get("FAKE_ZCODE_TRANSLATE_FILE")
            if target:
                with open(target, "w", encoding="utf-8") as handle:
                    json.dump(request.get("params"), handle, ensure_ascii=False)
            if os.environ.get("FAKE_ZCODE_ERROR"):
                send(
                    {
                        "id": request_id,
                        "error": {"code": -1, "message": "boom"},
                    }
                )
                continue
            delay = float(os.environ.get("FAKE_ZCODE_DELAY", "0") or 0)
            if delay:
                time.sleep(delay)
            text = os.environ.get(
                "FAKE_ZCODE_TRANSLATE_TEXT", '{"translations": ["你好"]}'
            )
            send({"id": request_id, "result": {"text": text}})
            continue
        send({"id": request_id, "error": {"code": -32601, "message": "no such method"}})


if __name__ == "__main__":
    main()
