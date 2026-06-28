#!/usr/bin/env python3
"""Write Cursor BYOK settings for the local Substantia proxy."""

from __future__ import annotations

import json
import os
import sqlite3
from copy import deepcopy
from pathlib import Path

DB = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
STORAGE_KEY = "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser"
OPENAI_KEY_KEY = "cursorAuth/openAIKey"

API_KEY = os.environ.get("SUBSTANTIA_API_KEY", "")
BASE_URL = os.environ.get("CURSOR_OPENAI_BASE", "https://api.substantia.ai/v1")
MODEL = os.environ.get("SUBSTANTIA_MODEL", "claude-opus-4-8")


def _set_model(data: dict, model: str) -> None:
    ai = data.setdefault("aiSettings", {})
    cfg = ai.setdefault("modelConfig", {})
    for mode in ("composer", "background-composer", "plan-execution", "cmd-k", "quick-agent"):
        cfg[mode] = {
            "modelName": model,
            "maxMode": mode == "background-composer",
            "selectedModels": [{"modelId": model, "parameters": []}],
        }
    ai["composerModel"] = model
    ai["backgroundComposerModel"] = model
    ai["cmdKModel"] = model
    # keep only substantia model + legacy gpt-* if present; drop typos like Claude-opus-4-8 / hh
    keep = {model}
    for m in ai.get("userAddedModels") or []:
        if m.startswith("gpt-"):
            keep.add(m)
    ai["userAddedModels"] = sorted(keep)
    enabled = set(ai.get("modelOverrideEnabled") or [])
    enabled.discard("Claude-opus-4-8")
    enabled.discard("hh")
    enabled.add(model)
    ai["modelOverrideEnabled"] = sorted(enabled)


def main() -> None:
    if not API_KEY:
        raise SystemExit("SUBSTANTIA_API_KEY is required")
    if not DB.exists():
        raise SystemExit(f"Cursor DB not found: {DB}")

    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT value FROM ItemTable WHERE key = ?", (STORAGE_KEY,)).fetchone()
    if not row:
        raise SystemExit("Cursor applicationUser storage missing")

    data = json.loads(row[0])
    backup = deepcopy(data)

    data["useOpenAIKey"] = True
    data["openAIBaseUrl"] = BASE_URL
    data["useClaudeKey"] = False
    _set_model(data, MODEL)

    conn.execute("UPDATE ItemTable SET value = ? WHERE key = ?", (json.dumps(data, separators=(",", ":")), STORAGE_KEY))
    conn.execute(
        "INSERT INTO ItemTable(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (OPENAI_KEY_KEY, API_KEY),
    )
    conn.commit()
    conn.close()

    print("Cursor configured:")
    print(f"  openAIBaseUrl = {BASE_URL}")
    print(f"  useOpenAIKey  = true")
    print(f"  default model = {MODEL}")
    print("Restart Cursor (Cmd+Q then reopen) for changes to apply.")


if __name__ == "__main__":
    main()
