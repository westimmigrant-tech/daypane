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
