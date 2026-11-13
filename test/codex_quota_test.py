import unittest

from tools.report_codex_quota import summarize


class CodexQuotaTests(unittest.TestCase):
    now = 1794585000

    def test_projects_only_the_two_codex_windows(self):
        five = {"windowDurationMins": 300, "usedPercent": 0, "resetsAt": self.now + 60,
                "accountId": "private"}
        week = {"windowDurationMins": 10080, "usedPercent": 100, "resetsAt": self.now + 600}
        result = {"rateLimitsByLimitId": {
            "codex": {"primary": week, "secondary": five, "credits": {"balance": "private"}},
            "other": {"primary": {"usedPercent": 50}},
        }, "rateLimitResetCredits": {"id": "private"}}

        self.assertEqual(summarize(result, self.now), {"ts": self.now * 1000, "quotas": {"codex": {
            "ok": True, "windows": {"fiveHour": {"usedPercent": 0, "resetsAt": self.now + 60},
                                    "weekly": {"usedPercent": 100, "resetsAt": self.now + 600}}}}})

    def test_missing_or_malformed_windows_abort_upload(self):
        five = {"windowDurationMins": 300, "usedPercent": 15, "resetsAt": self.now + 60}
        week = {"windowDurationMins": 10080, "usedPercent": 32, "resetsAt": self.now + 600}
        for invalid in (None, {**week, "usedPercent": True}, {**week, "usedPercent": 101},
                        {**week, "resetsAt": (self.now + 600) * 1000}, five):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                summarize({"rateLimits": {"limitId": "codex", "primary": five,
                                          "secondary": invalid}}, self.now)
