"""Friends list: file store, validation, CLI apply, and the bot picking up changes live (no restart)."""
import asyncio
import json
import os
import subprocess
import sys
import unittest

import common
import friends
from test_core import fake_msg

NEW = f'{10**17 + 5}'


class Store(unittest.TestCase):
    def setUp(self):
        self.path = str(common.TMP / "friends-store.json")
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_seeded_from_env_until_first_change(self):
        self.assertEqual([f["id"] for f in friends.read(self.path)], [f'{10**17 + 1}'])
        friends.add(NEW, "dana", self.path)
        self.assertEqual([f["id"] for f in friends.read(self.path)], [f'{10**17 + 1}', NEW])
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o664")

    def test_validation(self):
        for bad in ("123", "abc", str(10**15), str(10**20), ""):
            with self.assertRaises(friends.FriendError, msg=bad):
                friends.add(bad, "", self.path)
        with self.assertRaises(friends.FriendError):
            friends.remove(NEW, self.path)

    def test_trust_flag(self):
        friends.add(NEW, "dana", self.path)
        self.assertFalse([f for f in friends.read(self.path) if f["id"] == NEW][0]["trusted"])
        friends.trust(NEW, True, self.path)
        self.assertTrue([f for f in friends.read(self.path) if f["id"] == NEW][0]["trusted"])
        friends.add(NEW, "danny", self.path)          # renaming keeps the flag
        self.assertTrue([f for f in friends.read(self.path) if f["id"] == NEW][0]["trusted"])
        live = friends.Live(self.path)
        self.assertEqual(live.trusted_ids(), {int(NEW)})
        friends.trust(NEW, False, self.path)
        live.refresh(force=True)
        self.assertEqual(live.trusted_ids(), set())
        with self.assertRaises(friends.FriendError):
            friends.trust(f'{10**17 + 4}', True, self.path)     # not a friend

    def test_old_files_without_flag_read_as_untrusted(self):
        with open(self.path, "w") as f:
            json.dump({"friends": [{"id": NEW, "name": "dana"}]}, f)
        self.assertEqual(friends.read(self.path), [{"id": NEW, "name": "dana", "trusted": False}])

    def test_access_levels(self):
        import types
        from core import access
        t = lambda owner=False, trusted=False, known=True: types.SimpleNamespace(is_owner=owner, trusted=trusted, known=known)
        self.assertTrue(all(access.allows(t(owner=True, trusted=True), w) for w in access.WHO))
        self.assertEqual([access.allows(t(trusted=True), w) for w in access.WHO], [False, True, True])
        self.assertEqual([access.allows(t(), w) for w in access.WHO], [False, False, True])
        self.assertEqual([access.allows(t(known=False), w) for w in access.WHO], [False, False, False])
        self.assertFalse(access.allows(t(trusted=True), "nonsense"))

    def test_add_again_renames_and_names_are_cleaned(self):
        friends.add(NEW, "", self.path)
        friends.add(NEW, "<@everyone> dana\n", self.path)
        f = [x for x in friends.read(self.path) if x["id"] == NEW][0]
        self.assertEqual(f["name"], "everyone dana")
        self.assertEqual(len(friends.read(self.path)), 2)

    def test_remove_and_fill_names(self):
        friends.add(NEW, "", self.path)
        friends.fill_names({NEW: "Danny"}, self.path)
        self.assertEqual([x["name"] for x in friends.read(self.path) if x["id"] == NEW], ["Danny"])
        friends.remove(NEW, self.path)
        self.assertNotIn(NEW, [x["id"] for x in friends.read(self.path)])

    def test_cli_apply(self):
        env = {**os.environ, "FRIENDS_PATH": self.path}
        r = subprocess.run([sys.executable, str(common.ROOT / "friends.py"), "apply"], input=json.dumps({"op": "add", "id": NEW, "name": "d"}),
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(NEW, r.stdout)
        r = subprocess.run([sys.executable, str(common.ROOT / "friends.py"), "apply"], input='{"op": "add", "id": "x; rm -rf /"}',
                           capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("rejected", r.stderr)


class Live(unittest.TestCase):
    def test_bot_sees_new_friend_without_restart(self):
        from core.app import Lato
        lato = Lato()
        asyncio.run(lato.setup_modules())
        lato.friends.ttl = 0
        common.TMP.joinpath("settings.json").write_text(json.dumps({"sleep_mode": False, "mod_pings": False}))
        lato.S.refresh(force=True)
        prompts = []

        async def chat(messages, think=False, **kw):
            prompts.append(messages[-1]["content"])
            return "hi", ""
        lato.llm.chat = chat
        m, _ = fake_msg("hey", author=int(NEW))
        asyncio.run(lato.pipeline.respond([m], "pinged"))
        self.assertIn("(stranger)", prompts[-1])
        friends.add(NEW, "dana", str(common.TMP / "friends.json"))
        m, _ = fake_msg("hey", author=int(NEW))
        asyncio.run(lato.pipeline.respond([m], "pinged"))
        self.assertNotIn("(stranger)", prompts[-1])
        friends.remove(NEW, str(common.TMP / "friends.json"))
        self.assertFalse(lato.is_known(int(NEW)))


if __name__ == "__main__":
    unittest.main()
