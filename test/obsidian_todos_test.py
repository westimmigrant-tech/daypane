import sys
from datetime import date
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from obsidian_todos import DAILY, WEEKLY, extract_tasks, read_tasks, week_following_saturday


class ObsidianTodoTests(unittest.TestCase):
    def test_daily_selects_only_open_tasks_in_intended_section(self):
        text = '''---
- [ ] frontmatter
---
###### 晨间日记
- [ ] unrelated
###### 今日推进
- [ ] [[folder/a|First task]]
- [x] Done
%%
- [ ] Comment
%%
```md
- [ ] Example
```
- [ ] Second task
###### 晚间日记
- [ ] Another section
'''
        self.assertEqual(extract_tasks(text, DAILY), ['First task', 'Second task'])

    def test_week_uses_saturday_and_keeps_only_goal_section(self):
        self.assertEqual(week_following_saturday(date(2026, 11, 20)), '2026-W47')
        self.assertEqual(week_following_saturday(date(2026, 11, 21)), '2026-W48')
        self.assertEqual(extract_tasks('''# **本周目标（周六填写）**
## 一组目标
- First goal
- [x] Done
# 本周记录
- unrelated
''', WEEKLY, plain=True), ['First goal'])

    def test_reads_only_two_target_notes(self):
        calls = []
        data = {
            ('read', 'path=Daily/2026-11-17.md'): '# 今日推进\n- [ ] Today',
            ('files', 'folder=Weekly/2026', 'ext=md'):
                'Weekly/2026/2026-W46.md\nWeekly/2026/2026-W47-11月.md\nWeekly/2026/2026-W48.md\n',
            ('read', 'path=Weekly/2026/2026-W47-11月.md'): '# 本周目标\n- This week',
        }
        def read(vault, *args):
            self.assertEqual(vault, 'Vault')
            calls.append(args)
            return data[args]
        config = {'vault': 'Vault', 'dailyPath': 'Daily/{date}.md', 'weeklyFolder': 'Weekly/{year}'}
        self.assertEqual(read_tasks(config, date(2026, 11, 17), read),
                         {'daily': ['Today'], 'weekly': ['This week']})
        self.assertEqual(calls, list(data))
