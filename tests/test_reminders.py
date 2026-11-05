"""lato-reminders (the 2-minute timer): a reminder is saved as sent the moment it is posted, one odd entry can't stop
the others, and the state message never gets cut in half."""
import importlib.machinery
import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import common

SCRIPT = next(p for p in (common.ROOT / "lato-reminders", Path("/usr/local/sbin/lato-reminders")) if p.exists())
loader = importlib.machinery.SourceFileLoader("lrem", str(SCRIPT))
spec = importlib.util.spec_from_loader("lrem", loader)
lrem = importlib.util.module_from_spec(spec)
loader.exec_module(lrem)

NOW = datetime.now(timezone.utc)


def iso(delta_min):
    return (NOW + timedelta(minutes=delta_min)).isoformat(timespec="seconds")


def pinned(reminders):
    state = {"reminders": reminders}
    return {"id": "555", "author": {"id": lrem.BOT_ID}, "content": lrem.render(state)}


class Fake:
    """Stands in for lrem.rest: serves the pin, records POST/PATCH, can fail on the Nth POST."""

    def __init__(self, reminders, fail_post=None):
        self.pin, self.calls, self.fail_post, self.posts = pinned(reminders), [], fail_post, 0

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            return [self.pin]
        if method == "POST":
            self.posts += 1
            if self.posts == self.fail_post:
                raise OSError("discord went away")
        if method == "PATCH":
            self.pin["content"] = body["content"]
        return None

    def state(self):
        return json.loads(self.pin["content"].split("```json", 1)[1].rsplit("```", 1)[0])

    def count(self, method):
        return sum(1 for c in self.calls if c[0] == method)


class Timer(unittest.TestCase):
    def run_main(self, fake):
        old = lrem.rest
        lrem.rest = fake
        try:
            lrem.main()
        finally:
            lrem.rest = old

    def test_due_reminder_is_posted_once_and_saved(self):
        f = Fake([{"text": "bins", "due": iso(-1)}, {"text": "later", "due": iso(60)}])
        self.run_main(f)
        self.assertEqual(f.count("POST"), 1)
        self.assertTrue(f.state()["reminders"][0]["sent"])
        self.assertNotIn("sent", f.state()["reminders"][1])
        self.run_main(f)                                              # next timer tick: nothing more to send
        self.assertEqual(f.count("POST"), 1)

    def test_first_reminder_is_saved_even_if_the_second_post_fails(self):
        f = Fake([{"text": "one", "due": iso(-5)}, {"text": "two", "due": iso(-4)}], fail_post=2)
        with self.assertRaises(OSError):
            self.run_main(f)
        self.assertTrue(f.state()["reminders"][0]["sent"], "'one' must already be remembered as sent")
        self.assertNotIn("sent", f.state()["reminders"][1])

    def test_odd_entries_do_not_stop_the_others(self):
        f = Fake([{"text": "junk", "due": "next tuesday"}, {"text": "naive", "due": (NOW - timedelta(minutes=2)).replace(tzinfo=None).isoformat()},
                  {"text": "none"}, {"text": "ok", "due": iso(-1)}])
        self.run_main(f)
        self.assertEqual([c[2]["content"].split("⏰ ", 1)[1] for c in f.calls if c[0] == "POST"], ["naive", "ok"])

    def test_old_finished_reminders_are_pruned(self):
        f = Fake([{"text": "old", "due": iso(-4000), "sent": True, "sent_at": iso(-3000)}, {"text": "new", "due": iso(30)}])
        self.run_main(f)
        self.assertEqual([r["text"] for r in f.state()["reminders"]], ["new"])

    def test_state_message_is_never_cut_in_half(self):
        many = [{"text": "x" * 120, "due": iso(-3000 - i), "sent": True, "sent_at": iso(-10 - i)} for i in range(14)]
        many.append({"text": "now", "due": iso(-1)})
        f = Fake(many)
        f.pin["content"] = "placeholder"                                # too long to hold as-is; main() re-renders it
        f.pin = {"id": "555", "author": {"id": lrem.BOT_ID},
                 "content": f"{lrem.TAG}\n```json\n{json.dumps({'reminders': many}, separators=(',', ':'))}\n```"}
        self.run_main(f)
        self.assertLessEqual(len(f.pin["content"]), 2000)
        self.assertEqual(f.state()["reminders"][-1]["text"], "now")      # still valid JSON, the live reminder survived


if __name__ == "__main__":
    unittest.main()
