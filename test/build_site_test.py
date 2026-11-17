import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
import io
from PIL import Image
def png(color):
    out=io.BytesIO();Image.new("L",(1072,1448),color).save(out,"PNG");return out.getvalue()
spec=importlib.util.spec_from_file_location('site_builder', Path(__file__).resolve().parents[1]/'tools/build_site.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class SiteTests(unittest.TestCase):
    def test_success_failure_recovery_and_bounded_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            first_layout = {'modules':['weather','news'], 'newsLimit':5}
            self.assertTrue(m.build(out, {}, lambda:(png(0), {}, first_layout), {'runId':'1'}))
            first=json.loads((out/'status.json').read_text())
            def fail(): raise RuntimeError('timeout')
            self.assertFalse(m.build(out, first, fail, {'runId':'2'}))
            failed=json.loads((out/'status.json').read_text())
            self.assertEqual((out/'screen.png').read_bytes(), png(0))
            self.assertEqual(failed['generatedAt'], first['generatedAt'])
            self.assertEqual(failed['state'], 'failed')
            self.assertEqual(failed['layout'], first_layout)
            self.assertEqual(len(failed['history']), 2)
            failed['history'] *= 200
            updated_layout = {'modules':['news'], 'newsLimit':3}
            self.assertTrue(m.build(out, failed, lambda:(png(255), {}, updated_layout), {'runId':'3'}))
            latest=json.loads((out/'status.json').read_text())
            self.assertEqual(latest['state'], 'ready')
            self.assertEqual(latest['layout'], updated_layout)
            self.assertNotIn('error', latest)
            self.assertEqual(len(latest['history']),256)
            self.assertEqual((out/'screen.png').read_bytes(), png(255))
            self.assertFalse((out/'dashboard.json').exists())

    def test_first_failure_does_not_produce_deployable_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fail(): raise RuntimeError('no data')
            with self.assertRaises(RuntimeError): m.build(Path(tmp), {}, fail, {})
            self.assertFalse((Path(tmp)/'status.json').exists())

if __name__=='__main__': unittest.main()
