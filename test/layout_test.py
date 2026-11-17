import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('layout',Path(__file__).parents[1]/'tools/layout.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class LayoutTests(unittest.TestCase):
    def check(self,display):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json';p.write_text(json.dumps({'display':display}));return m.load_layout(p)
    def test_presets_and_independent_units(self):
        self.assertEqual(self.check({'preset':'minimal','temperatureUnit':'F','timezone':'Asia/Shanghai'})['modules'],['weather','clocks','quote'])
        self.assertEqual(self.check({})['width'],1072)
        self.assertEqual(self.check({})['newsLimit'], 4)
        self.assertEqual(self.check({'newsLimit': 8})['newsLimit'], 8)
    def test_invalid_layout_rejected(self):
        for value in [{'modules':['weather','weather']},{'modules':['unknown']},{'width':0},{'fontScale':3},{'temperatureUnit':'K'},{'preset':'bad'},{'newsLimit':9}]:
            with self.assertRaises(ValueError):self.check(value)

    def test_runtime_layout_preserves_screen_and_rejects_extra_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            override = Path(tmp) / 'layout.json'
            config.write_text('{"display":{"width":900,"modules":["weather"],"newsLimit":8}}')
            override.write_text('{"modules":["quote","news"],"newsLimit":5}')
            layout = m.load_layout(config, override)
            self.assertEqual(m.editable_layout(layout), {'modules':['quote','news'], 'newsLimit':5})
            self.assertEqual(layout['width'], 900)
            override.write_text('{"modules":["news"],"newsLimit":5,"timezone":"UTC"}')
            with self.assertRaises(ValueError):
                m.load_layout(config, override)
