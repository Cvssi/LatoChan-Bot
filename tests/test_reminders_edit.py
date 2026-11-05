"""lato-reminders-edit: add/update/done/remove on the pinned-state JSON, local time to UTC, ids, limits."""
from datetime import timedelta, timezone
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

import common

# next to the code in the repo / deploy staging; installed to /usr/local/sbin on the server
SCRIPT = next((p for p in (common.ROOT / "lato-reminders-edit", Path("/usr/local/sbin/lato-reminders-edit")) if p.exists()))
loader = importlib.machinery.SourceFileLoader("lre", str(SCRIPT))
spec = importlib.util.spec_from_loader("lre", loader)
lre = importlib.util.module_from_spec(spec)
loader.exec_module(lre)


class Edit(unittest.TestCase):
    def test_add_converts_local_time_to_utc(self):
        s = lre.apply({"reminders": []}, {"op": "add", "text": "router", "when": "2026-11-09T10:00"})
        self.assertEqual(s["reminders"][0]["due"], "2026-11-09T10:00:00+00:00")
        with patch.object(lre, "operator_timezone", return_value=timezone(timedelta(hours=2))):
            s = lre.apply(s, {"op": "add", "text": "appointment", "when": "2027-07-01T10:00"})
        self.assertEqual(s["reminders"][1]["due"], "2027-07-01T08:00:00+00:00")
        s = lre.apply(s, {"op": "add", "text": "whenever", "when": None})
        self.assertIsNone(s["reminders"][2]["due"])

    def test_update_done_remove_by_id(self):
        s = lre.apply({"reminders": [{"text": "a", "due": "2026-11-09T10:00:00+00:00", "sent": True, "sent_at": "x"},
                                     {"text": "b", "due": None}]},
                      {"op": "done", "id": lre.rid({"text": "b", "due": None})})
        self.assertTrue(s["reminders"][1]["done"])
        a_id = lre.view(s)[0]["id"]
        s = lre.apply(s, {"op": "update", "id": a_id, "when": "2026-11-10T08:30"})
        self.assertEqual(s["reminders"][0]["due"], "2026-11-10T08:30:00+00:00")
        self.assertNotIn("sent", s["reminders"][0], "a new time must fire again")
        s = lre.apply(s, {"op": "remove", "id": lre.view(s)[0]["id"]})
        self.assertEqual([r["text"] for r in s["reminders"]], ["b"])
        self.assertEqual(lre.view(s)[0]["when"], None)

    def test_rejections(self):
        for op in ({"op": "add", "text": "  "}, {"op": "add", "text": "x", "when": "next tuesday"},
                   {"op": "remove", "id": "nope"}, {"op": "explode"}):
            with self.assertRaises(lre.Rejected, msg=op):
                lre.apply({"reminders": []}, op)

    def test_text_cleaned_and_render_limit(self):
        s = lre.apply({"reminders": []}, {"op": "add", "text": "ping @everyone\nnow", "when": None})
        self.assertEqual(s["reminders"][0]["text"], "ping everyone now")
        self.assertIn("⏰ ping everyone now — no time", lre.render("📋 LatoChan state", s))
        big = {"reminders": [{"text": "x" * 300, "due": None}] * 8}
        with self.assertRaises(lre.Rejected):
            lre.render("📋 LatoChan state", big)

    def test_view_shows_configured_local_time(self):
        with patch.object(lre, "operator_timezone", return_value=timezone(timedelta(hours=1))):
            v = lre.view({"reminders": [{"text": "r", "due": "2027-07-01T09:00:00+00:00", "sent": True}]})
        self.assertEqual(v[0]["when"], "2027-07-01T10:00")
        self.assertTrue(v[0]["sent"])


if __name__ == "__main__":
    unittest.main()
