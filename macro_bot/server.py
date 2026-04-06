from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request, HTTPException

from .app import MacroBotApp
from .config import BotConfig
from .notifiers import TelegramNotifier
from .telegram_deep_dive import TelegramDeepDiveCallbackProcessor, TelegramDeepDiveUpdateStateStore
from .telegram_overview import TelegramOverviewCallbackProcessor, TelegramOverviewUpdateStateStore


app = FastAPI()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"ok": "1"}


@app.post("/run")
def run_job(request: Request) -> dict[str, Any]:
    cfg = BotConfig.from_env()
    token = (cfg.run_token or "").strip()
    if token:
        auth = request.headers.get("authorization") or ""
        if auth.strip() != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Unauthorized")
    code = MacroBotApp.build_default().run()
    return {"ok": True, "exit_code": code}


@app.post("/telegram/webhook")
async def telegram_webhook(req: Request) -> dict[str, Any]:
    cfg = BotConfig.from_env()
    payload = await req.json()
    if not isinstance(payload, dict):
        return {"ok": True}

    notifier = TelegramNotifier(token=cfg.telegram_token, chat_id=cfg.telegram_chat_id, dry_run=False)

    # Reuse callback processors but handle one update in realtime.
    bot = MacroBotApp.build_default()

    cb = payload.get("callback_query")
    if isinstance(cb, dict):
        data = cb.get("data") or ""
        if isinstance(data, str) and data.startswith("deep_dive:"):
            deep_dive_store = None
            deep_dive_update = TelegramDeepDiveUpdateStateStore(cfg.deep_dive_update_state_file)
            # build_default() already wires Firestore vs JSON for stores in app.run(),
            # but callback processor needs store instance; simplest: create a dummy app and access deep dive store via config.
            # Here we just instantiate processor with JSON stores; if FIRESTORE_ENABLED is on, app.run will have stored to Firestore,
            # but deep dive callback reads from the configured store file or Firestore via app path.
            # To keep it consistent, deep dive callback is handled by the overview article click (which already shows analysis)
            # and deep dive uses the existing DeepDiveStore file when not on Firestore.
            from .telegram_deep_dive import TelegramDeepDiveStore
            from .firestore_stores import FirestoreConfig, FirestoreDeepDiveStore

            if cfg.deep_dive_enabled and cfg.firestore_enabled and cfg.firestore_project_id:
                deep_dive_store = FirestoreDeepDiveStore(FirestoreConfig(project_id=cfg.firestore_project_id, prefix=cfg.firestore_prefix))
            else:
                deep_dive_store = TelegramDeepDiveStore(cfg.deep_dive_store_file)

            proc = TelegramDeepDiveCallbackProcessor(
                token=cfg.telegram_token,
                chat_id=cfg.telegram_chat_id,
                analyzer=bot.analyzer,
                deep_dive_store=deep_dive_store,  # type: ignore[arg-type]
                update_state_store=deep_dive_update,
                timeout_sec=cfg.article_fetch_timeout_sec,
                max_age_days=cfg.deep_dive_max_age_days,
            )
            proc.handle_callback_query(cb, notifier=notifier)
            return {"ok": True}

        if isinstance(data, str) and data.startswith("ov:"):
            from .telegram_overview import TelegramOverviewStore
            from .firestore_stores import FirestoreConfig, FirestoreOverviewStore

            if cfg.overview_enabled and cfg.firestore_enabled and cfg.firestore_project_id:
                ov_store = FirestoreOverviewStore(FirestoreConfig(project_id=cfg.firestore_project_id, prefix=cfg.firestore_prefix))
            else:
                ov_store = TelegramOverviewStore(cfg.overview_store_file)
            ov_update = TelegramOverviewUpdateStateStore(cfg.overview_update_state_file)
            proc = TelegramOverviewCallbackProcessor(
                token=cfg.telegram_token,
                chat_id=cfg.telegram_chat_id,
                store=ov_store,  # type: ignore[arg-type]
                update_state_store=ov_update,
                timeout_sec=cfg.article_fetch_timeout_sec,
                max_age_days=cfg.overview_max_age_days,
            )
            # Call internal handler directly for realtime.
            msg = cb.get("message") or {}
            if isinstance(msg, dict):
                proc._handle_callback(cb=cb, msg=msg, data=str(data))  # type: ignore[attr-defined]
            return {"ok": True}

    # Ignore non-callback updates for now.
    return {"ok": True}


def _maybe_set_webhook() -> None:
    """
    Optional helper when running locally.
    """
    pass

