from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any


@dataclass
class WatchlistState:
    enabled_symbols: list[str]
    last_update_id: int


class WatchlistStore:
    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> WatchlistState:
        if not self.path or not os.path.exists(self.path):
            return WatchlistState(enabled_symbols=[], last_update_id=0)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            enabled = data.get("enabled_symbols", [])
            if not isinstance(enabled, list):
                enabled = []
            enabled_syms = []
            for it in enabled:
                s = str(it or "").strip().upper()
                if s and s not in enabled_syms:
                    enabled_syms.append(s)
            last_update_id = int(data.get("last_update_id", 0) or 0)
            return WatchlistState(enabled_symbols=enabled_syms, last_update_id=last_update_id)
        except Exception:
            return WatchlistState(enabled_symbols=[], last_update_id=0)

    def save(self, state: WatchlistState) -> None:
        payload: dict[str, Any] = {
            "enabled_symbols": list(dict.fromkeys([s.upper() for s in (state.enabled_symbols or []) if s])),
            "last_update_id": int(state.last_update_id or 0),
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

