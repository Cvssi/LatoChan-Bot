"""LatoChan: Discord bot with a local LLM brain (Ollama on server). Entry point for latochan.service.

Everything lives in two places:
  core/      Discord plumbing, who she answers (router), the reply pipeline, settings, history, the model call.
  modules/   one folder per feature (gifs, memory, sleep, server actions, pings, drawing...).
             Each can be switched on/off live and removed without touching anything else. See MODULES.md.

v1 (a single 1,100-line gateway.py) is in git history and in the Stable snapshots.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from core.app import Lato  # noqa: E402

if __name__ == "__main__":
    Lato().run()
