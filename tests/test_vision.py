import unittest
from opsfusion.vision_logic import ZoneTracker,validate_polygon,point_inside
from opsfusion.core import Event,fuse
P=[[.5,.1],[.9,.1],[.9,.95],[.5,.95]]
def det(x=.3): return {'box':[x,.2,x+.3,.8],'confidence':.9}

class VisionTests(unittest.TestCase):
    def test_polygon(self):
        self.assertTrue(point_inside((.6,.5),P));self.assertFalse(point_inside((.2,.5),P))
    def test_boundary(self): self.assertTrue(point_inside((.5,.5),P))
    def test_invalid_polygon(self):
        for polygon in ([[0,0],[1,1]],[[0,0],[.5,.5],[1,1]],[[0,0],[1,1],[0,1],[1,0]],[[0,0],[2,0],[1,1]],[[0,0],[1,0],[1,0],[0,1]]):
            with self.assertRaises(ValueError): validate_polygon(polygon)
    def test_confirmed_entry(self):
        t=ZoneTracker(P)
        t.update(0,[det()]);t.update(.2,[det()])
        self.assertIsNone(t.update(.4,[det(.4)])[0]['transition'])
        r=t.update(.6,[det(.4)])[0]
        self.assertEqual(r['transition'],'entry');self.assertEqual(r['track_id'],1)
    def test_initial_presence_not_entry(self):
        t=ZoneTracker(P);t.update(0,[det(.4)])
        self.assertEqual(t.update(.2,[det(.4)])[0]['transition'],'initial_presence')
    def test_no_repeated_alert(self):
        t=ZoneTracker(P);t.update(0,[det(.4)]);t.update(.2,[det(.4)])
        self.assertIsNone(t.update(.4,[det(.4)])[0]['transition'])
    def test_boundary_jitter(self):
        t=ZoneTracker(P);t.update(0,[det()]);t.update(.2,[det()])
        t.update(.4,[det(.4)]);t.update(.6,[det()])
        self.assertIsNone(t.update(.8,[det(.4)])[0]['transition'])
    def test_missing_frame_resets_streak(self):
        t=ZoneTracker(P);t.update(0,[det()]);t.update(.2,[det()]);t.update(.4,[det(.4)]);t.update(.6,[])
        self.assertIsNone(t.update(.8,[det(.4)])[0]['transition'])
    def test_expired_identity_not_entry(self):
        t=ZoneTracker(P);t.update(0,[det()]);t.update(.2,[det()]);t.update(2,[det(.4)])
        self.assertEqual(t.update(2.2,[det(.4)])[0]['transition'],'initial_presence')
    def test_exit_reentry(self):
        t=ZoneTracker(P);t.update(0,[det(.4)]);t.update(.2,[det(.4)]);t.update(.4,[det()])
        self.assertEqual(t.update(.6,[det()])[0]['transition'],'exit')
        t.update(.8,[det(.4)]);self.assertEqual(t.update(1,[det(.4)])[0]['transition'],'entry')
    def test_multi_person_one_to_one(self):
        t=ZoneTracker(P);a=det(.0);b=det(.55)
        ids={r['track_id'] for r in t.update(0,[a,b])}
        self.assertEqual(len(ids),2);self.assertEqual({r['track_id'] for r in t.update(.2,[b,a])},ids)
    def test_nonmonotonic_time_rejected(self):
        t=ZoneTracker(P);t.update(0,[])
        with self.assertRaises(ValueError):t.update(0,[])
    def test_invalid_box(self):
        with self.assertRaises(ValueError):ZoneTracker(P).update(0,[{'box':[.5,.5,.1,.1],'confidence':.9}])
    def test_presence_fuses_truthfully(self):
        e=Event('v1','2026-01-01T10:00:00Z','z','video','zone_presence','c','file:///snapshot.jpg','Observed presence',.9,False)
        self.assertEqual(fuse([e])['incidents'][0]['type'],'restricted_zone_presence')
