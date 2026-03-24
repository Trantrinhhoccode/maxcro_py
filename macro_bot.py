"""
Backward-compatible entrypoint for Macro Bot.

Run locally:
  pip install -r requirements.txt
  export GEMINI_API_KEY=...
  export TELEGRAM_TOKEN=...
  export TELEGRAM_CHAT_ID=...
  python macro_bot.py
"""

from macro_bot.app import MacroBotApp


def main() -> int:
    return MacroBotApp.build_default().run()


if __name__ == "__main__":
    raise SystemExit(main())
