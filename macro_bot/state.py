from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from typing import Protocol


class StateStore(Protocol):
    def load_fingerprints(self) -> dict[str, str]: ...
    def save_fingerprint(self, fp: str, timestamp_iso: str) -> None: ...
    def cleanup(self, max_age_days: int) -> int: ...


@dataclass
class JsonFileStateStore:
    path: str

    def load_fingerprints(self) -> dict[str, str]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            fps = data.get("fingerprints", {})
            return fps if isinstance(fps, dict) else {}
        except Exception:
            return {}

    def save_fingerprint(self, fp: str, timestamp_iso: str) -> None:
        data: dict = {"fingerprints": {}}
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {"fingerprints": {}}
        except Exception:
            data = {"fingerprints": {}}

        fps = data.get("fingerprints")
        if not isinstance(fps, dict):
            fps = {}
            data["fingerprints"] = fps
        fps[fp] = timestamp_iso

        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def cleanup(self, max_age_days: int) -> int:
        if not os.path.exists(self.path):
            return 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            fps = data.get("fingerprints", {})
            if not isinstance(fps, dict):
                return 0

            cutoff = datetime.now() - timedelta(days=max_age_days)
            cutoff_iso = cutoff.isoformat()
            cleaned = {fp: ts for fp, ts in fps.items() if isinstance(ts, str) and ts >= cutoff_iso}
            removed = len(fps) - len(cleaned)
            data["fingerprints"] = cleaned
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return max(0, removed)
        except Exception:
            return 0

