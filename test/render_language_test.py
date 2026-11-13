import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('renderer', Path(__file__).parents[1] / 'tools/render_screen.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

class LanguageTests(unittest.TestCase):
    def tearDown(self):
        r.LANG = 'zh'

    def test_english_content_and_chinese_payload_are_independent(self):
        data = {'news': {'items': [{'title': '中文新闻'}]}, 'newsEn': {'items': [{'title': 'English news'}]},
                'weather': {'city': '深圳', 'code': 0, 'text': '晴'},
                'stocks': {'items': [{'sym': 'AAPL', 'label': '苹果'}]},
                'clocks': {'items': [{'city': '伦敦', 'tz': 'Europe/London', 'date': '09/11周五'}]},
                'mlb': {'items': [{'abbr': 'LAD', 'cn': '道奇', 'next': {'opp': '教士', 'live': True}}]}}
        r.LANG = 'en'
        en = r.localized_data(data)
        self.assertEqual(en['news']['items'][0]['title'], 'English news')
        self.assertEqual(en['weather']['text'], 'Clear')
        self.assertEqual(en['weather']['city'], 'Shenzhen')
        self.assertEqual(en['stocks']['items'][0]['label'], 'AAPL')
        self.assertEqual(en['clocks']['items'][0]['city'], 'London')
        self.assertEqual(en['mlb']['items'][0]['next']['opp'], 'SD')
        self.assertEqual(en['mlb']['items'][0]['next']['time'], 'Live')
        self.assertEqual(r.tr('世界时钟'), 'World Clocks')
        r.LANG = 'zh'
        self.assertEqual(r.localized_data(data), data)
        self.assertEqual(data['weather']['text'], '晴')
        self.assertEqual(r.tr('世界时钟'), '世界时钟')

    def test_missing_english_feed_does_not_show_chinese_headlines(self):
        r.LANG = 'en'
        self.assertEqual(r.localized_data({'news': {'items': [{'title':'中文'}]}})['news']['items'], [])
        self.assertTrue(all(line.isascii() for line in r.load_quotes()))

    def test_every_tr_literal_with_chinese_has_an_english_label(self):
        # 漏翻译不会报错，只会在英文版图上直接露出中文（曾经真发生过：
        # tr(' ·旧') 的键多了一个前导空格，字典里是 '·旧' —— 英文版显示 " ·旧"；
        # tr('Codex 文件') 字典里根本没有这个键）。
        import re
        src = (Path(__file__).parents[1] / 'tools/render_screen.py').read_text(encoding='utf-8')
        literals = set(re.findall(r"tr\('([^']+)'\)", src))
        self.assertTrue(literals, 'no tr() literals found — the scan itself is broken')

        def has_cjk(s):
            return any('\u4e00' <= ch <= '\u9fff' for ch in s)

        chinese = sorted(x for x in literals if has_cjk(x))
        missing = sorted(x for x in chinese if x not in r.LABELS)
        self.assertEqual(missing, [], 'tr() literals without an English label: %r' % (missing,))

        r.LANG = 'en'
        untranslated = sorted(x for x in chinese if r.tr(x) == x)
        self.assertEqual(untranslated, [], 'labels that stay Chinese in English mode: %r' % (untranslated,))

        # 两条曾经真出问题的具体条目
        self.assertEqual(r.tr('·旧'), '·stale')
        self.assertEqual(r.tr('Codex 文件'), 'Codex files')
        # 陈旧标记在英文版必须带分隔空格，不能糊在数字后面
        self.assertEqual('412345 / 300000' + ' ' + r.tr('·旧'), '412345 / 300000 ·stale')

    def test_english_guard_detects_chinese_that_reaches_the_canvas(self):
        # 关卡本身要有效：漏翻译时它必须真的报出来，否则等于没装。
        from PIL import Image, ImageDraw
        img = Image.new('L', (60, 60), 255)
        draw = ImageDraw.Draw(img)
        r._DRAWN.clear()
        r._record_texts(draw)
        draw.text((0, 0), 'Context 1/2')
        self.assertEqual(r._cjk_leak(), [])
        draw.text((0, 20), '42 条')
        self.assertEqual(r._cjk_leak(), ['42 条'])
        r._DRAWN.clear()

    def test_english_render_produces_no_chinese(self):
        # 端到端：整张英文图里不许出现中文。以前 '2 次' / '42 条 · 4 会话'
        # 就是因为单位硬编码在格式串里，标签翻了、单位没翻。
        import tempfile
        from datetime import datetime, timedelta
        from unittest.mock import patch
        from zoneinfo import ZoneInfo
        now = datetime(2026, 11, 13, 23, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
        data = {
            'ok': True,
            'weather': {'ok': True, 'city': '北京', 'text': '晴', 'temp': 26, 'high': 29, 'low': 22, 'humidity': 60},
            'fx': {'ok': True, 'cny': 7.1, 'inr': 83, 'base': 'USD'},
            'stocks': {'ok': True, 'items': [{'sym': 'SPY', 'label': '标普500', 'price': '700',
                                              'currency': 'USD', 'changePct': -1.2, 'spark': {'closes': [1, 2, 3]}}]},
            'clocks': {'ok': True, 'items': [{'city': '伦敦', 'tz': 'Europe/London', 'time': '08:00', 'date': '10/31 Fri'}]},
            'news': {'ok': True, 'items': [{'title': '中文标题'}]},
            'newsEn': {'ok': True, 'items': [{'title': 'English headline'}]},
            'mlb': {'ok': True, 'items': []},
            'quotas': {
                'workbuddy': {'ok': True, 'model': 'hy3',
                              'context': {'used': 100, 'size': 1000, 'percent': 10},
                              'credit': {'used': 1.5, 'count': 3}},
                'claudecode': {'ok': True, 'used7d': 42, 'sessions7d': 4},
                'codex': {'ok': True, 'stale': True, 'windows': {
                    'fiveHour': {'usedPercent': 0, 'resetsAt': int((now + timedelta(hours=2)).timestamp())},
                    'weekly': {'usedPercent': 100, 'resetsAt': int((now - timedelta(minutes=5)).timestamp())},
                }},
            }
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(r, 'display_now', return_value=now):
            out = Path(tmp) / 'en.png'
            r.render(data, str(out), 'en')
            self.assertTrue(out.exists())
            self.assertEqual(r._cjk_leak(), [], 'English render leaked Chinese onto the canvas')
            self.assertIn('Beijing', r._DRAWN)
            self.assertIn('Used 0% · Resets 11/14 01:00 ·stale', r._DRAWN)
            self.assertIn('Update pending', r._DRAWN)

    def test_legacy_quota_snapshot_renders_consistently(self):
        # 云端 runtime-data 分支里可能还是旧格式（没有 context 字段）——
        # 上报 token 失效时就是这样。那份数据只有"剩余"一个口径，
        # 标签和进度条必须一起换，不能画成"上下文 254392/300000"却按已用率填充。
        import tempfile
        data = {
            'ok': True,
            'weather': {'ok': True, 'city': '深圳', 'text': '晴', 'temp': 26, 'high': 29, 'low': 22, 'humidity': 60},
            'fx': {'ok': True, 'cny': 7.1, 'inr': 83, 'base': 'USD'},
            'stocks': {'ok': True, 'items': []},
            'clocks': {'ok': True, 'items': []},
            'news': {'ok': True, 'items': []},
            'newsEn': {'ok': True, 'items': []},
            'mlb': {'ok': True, 'items': []},
            'quotas': {'workbuddy': {'ok': True, 'model': 'hy3', 'used': 100, 'cap': 1000,
                                     'remaining': 900, 'percent': 10},
                       'claudecode': {'ok': True, 'used7d': 42, 'sessions7d': 4}}
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'en.png'
            r.render(data, str(out), 'en')
            self.assertTrue(out.exists())
            self.assertEqual(r._cjk_leak(), [], 'legacy snapshot leaked Chinese onto the canvas')
        r.LANG = 'en'
        self.assertEqual(r.tr('剩余'), 'Remaining')
        self.assertEqual(r.tr('上下文'), 'Context')
