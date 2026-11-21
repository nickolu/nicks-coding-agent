"""Make the pure helper modules next to sophie-bot/bot.py importable.

Do not import `bot.py` itself in tests: it reads /etc/sophie-discord/config.json
and imports discord at import time. Pure logic lives in sibling modules.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "sophie-bot"))
