"""Run the golden scenarios and compare with tests/golden_snapshot.json.

    python tests/golden.py            compare (exit 1 on any difference)
    python tests/golden.py --update   re-make the snapshot (only when you change behaviour on purpose)

The snapshot covers the public configuration with synthetic fixtures.
"""
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TMP = Path(tempfile.mkdtemp(prefix="lato-golden-"))
os.environ.update(DISCORD_BOT_TOKEN="x", SLEEP_MODE="1", TIMEZONE="UTC", GUILD_ID="200", FRIEND_IDS=f'{10**17 + 1}', CHANNEL_IDS="100", PRIVATE_CHANNEL_IDS="100",
                  OWNER_ID=f'{10**17 + 2}',
                  SETTINGS_PATH=str(TMP / "settings.json"), GIF_DIR=str(TMP / "gifs"), MEMORY_DB=str(TMP / "memory.db"), FRIENDS_PATH=str(TMP / "friends.json"),
                  LATO_DATA=str(TMP), PERSONA_PATH=str(HERE / "fixtures" / "persona.md"), LATO_TEST="1")
for mood, links in {"happy": "https://tenor.com/happy-1", "waking": "https://tenor.com/waking-1",
                    "sleepy": "https://tenor.com/sleepy-1", "bored": "# empty"}.items():
    (TMP / "gifs" / mood).mkdir(parents=True)
    (TMP / "gifs" / mood / "links.txt").write_text(links + "\n")
sys.path[:0] = [str(ROOT), str(HERE)]

import golden_harness  # noqa: E402

SNAP = HERE / "golden_snapshot.json"


def main():
    from adapter_new import Adapter
    api = Adapter(TMP)
    got = asyncio.run(golden_harness.run_all(api))
    got = json.loads(re.sub(r"\b[0-9]{15,20}\b", "[synthetic-id]", json.dumps(got)))
    if "--update" in sys.argv:
        SNAP.write_text(json.dumps(got, indent=1, ensure_ascii=False))
        print(f"snapshot written: {len(got['respond'])} reply + {len(got['route'])} routing scenarios")
        return
    want = json.loads(SNAP.read_text())
    bad = 0
    for part in ("respond", "route"):
        for name, w in want[part].items():
            g = got[part].get(name)
            if g != w:
                bad += 1
                print(f"DIFF {part}/{name}")
                for k in w:
                    if g is None or g.get(k) != w[k]:
                        print(f"   {k}:\n     want {w[k]!r}\n     got  {None if g is None else g.get(k)!r}")
    n = len(want["respond"]) + len(want["route"])
    print(f"golden: {n - bad}/{n} scenarios identical")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
