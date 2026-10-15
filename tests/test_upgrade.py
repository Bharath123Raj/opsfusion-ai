from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import shutil

from opsfusion.history import History
from opsfusion.live import settings
from opsfusion.upgrade import upgrade
from test_live_history import ZONE, event


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);base=Path(self.temp.name)
        self.old=base/'step9';self.new=base/'step10'
        for root,version in [(self.old,'0.9.0'),(self.new,'0.10.0')]:
            (root/'opsfusion').mkdir(parents=True);(root/'dashboard.py').write_text(version)
            (root/'opsfusion/__init__.py').write_text("__version__ = '"+version+"'")
        (self.new/'opsfusion/accounts.py').write_text('new-account-code')
        (self.new/'runs').mkdir();(self.new/'runs/private.txt').write_text('must-not-copy')
        (self.new/'.env').write_text('must-not-copy-secret')
        self.h=History(self.old/'runs/history.sqlite3');self.sid=self.h.start('camera-1','zone-1','webcam',settings(ZONE))
        evidence=self.old/'runs/live'/self.sid/'evidence/a.jpg';evidence.parent.mkdir(parents=True);evidence.write_bytes(b'original evidence bytes')
        self.h.ingest(self.sid,[event(evidence_ref=evidence.as_uri())]);self.h.finish(self.sid)

    def test_preserves_history_evidence_and_saves_code_backup(self):
        before=self.h.incidents();result=upgrade(self.old,self.new)
        self.assertEqual(self.h.incidents(),before)
        self.assertEqual((self.old/'dashboard.py').read_text(),'0.10.0')
        self.assertEqual((Path(result['backup'])/'dashboard.py').read_text(),'0.9.0')
        self.assertFalse((self.old/'.env').exists());self.assertFalse((self.old/'runs/private.txt').exists())
        self.assertTrue((self.old/'opsfusion/accounts.py').is_file())

    def test_active_worker_blocks_upgrade(self):
        self.h.start('camera-2','zone-1','webcam',settings(ZONE))
        with self.assertRaises(ValueError):upgrade(self.old,self.new)
        self.assertEqual((self.old/'dashboard.py').read_text(),'0.9.0')

    def test_copy_failure_rolls_back_code(self):
        original=shutil.copy2
        def copy(src,dst,*args,**kwargs):
            if Path(src)==self.new/'opsfusion/accounts.py':raise OSError('fixture copy failure')
            return original(src,dst,*args,**kwargs)
        with patch('opsfusion.upgrade.shutil.copy2',side_effect=copy),self.assertRaises(OSError):upgrade(self.old,self.new)
        self.assertEqual((self.old/'dashboard.py').read_text(),'0.9.0')
        self.assertEqual((self.old/'opsfusion/__init__.py').read_text(),"__version__ = '0.9.0'")
        self.assertFalse((self.old/'opsfusion/accounts.py').exists())

    def test_wrong_target_rejected(self):
        with self.assertRaises(ValueError):upgrade(self.new,self.new)
        with self.assertRaises(ValueError):upgrade(self.new,self.old)
