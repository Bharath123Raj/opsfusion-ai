import unittest
from dataclasses import replace
from datetime import timedelta
from opsfusion.core import fuse
from opsfusion.demo import generate

class FusionTests(unittest.TestCase):
    def setUp(self):
        self.events, self.truth = generate()
        self.a, self.b = self.events[:2]
    def test_demo(self):
        self.assertEqual({(i['asset_id'],i['type']) for i in fuse(self.events)['incidents']}, {(i['asset_id'],i['type']) for i in self.truth})
    def test_asset_isolation(self):
        self.assertEqual(fuse([self.a,replace(self.b,asset_id='other')])['incidents'],[])
    def test_single_modality(self):
        self.assertEqual(fuse([self.a,replace(self.a,event_id='second')])['incidents'],[])
    def test_far_apart(self):
        self.assertEqual(fuse([self.a,replace(self.b,timestamp=(self.a.time+timedelta(seconds=91)).isoformat())])['incidents'],[])
    def test_boundary(self):
        self.assertEqual(len(fuse([self.a,replace(self.b,timestamp=(self.a.time+timedelta(seconds=90)).isoformat())])['incidents']),1)
    def test_no_bridge(self):
        second=replace(self.a,event_id='second',timestamp=(self.a.time+timedelta(seconds=80)).isoformat())
        third=replace(self.b,event_id='third',timestamp=(self.a.time+timedelta(seconds=160)).isoformat())
        self.assertEqual(fuse([self.a,second,third])['incidents'],[])
    def test_order(self):
        self.assertEqual(fuse(self.events),fuse(list(reversed(self.events))))
    def test_dedup(self):
        self.assertEqual(fuse(self.events),fuse(self.events+self.events))
    def test_conflicting_id(self):
        with self.assertRaises(ValueError): fuse([self.a,replace(self.a,summary='different')])
    def test_low_confidence(self):
        self.assertEqual(fuse([self.a,replace(self.b,confidence=.1)])['incidents'],[])
    def test_timezone_required(self):
        with self.assertRaises(ValueError): replace(self.a,timestamp='2026-01-01T10:00:00')
    def test_timezone_normalized(self):
        self.assertEqual(replace(self.a,timestamp='2026-01-01T15:30:00+05:30').time,self.a.time)
    def test_bad_confidence(self):
        for value in (float('nan'),float('inf'),-1,2,True):
            with self.assertRaises(ValueError): replace(self.a,confidence=value)
    def test_empty_evidence(self):
        with self.assertRaises(ValueError): replace(self.a,evidence_ref='')
    def test_grounded_report(self):
        known={e.event_id for e in self.events}
        for incident in fuse(self.events)['incidents']:
            self.assertTrue(set(incident['trigger_event_ids'])<=known)
            self.assertEqual(incident['hypothesis']['status'],'unconfirmed')
            self.assertFalse(incident['automatic_action_executed'])
            self.assertTrue(incident['synthetic'])
    def test_reproducible(self): self.assertEqual(generate(42),generate(42))
    def test_bad_window(self):
        for value in (0,-1,float('nan')):
            with self.assertRaises(ValueError): fuse(self.events,window_seconds=value)
    def test_empty(self): self.assertEqual(fuse([])['incidents'],[])

if __name__=='__main__': unittest.main()
