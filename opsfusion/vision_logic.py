"""Geometry, conservative person association and debounced entry detection.

This module is independent of OpenCV and neural network dependencies.
"""
from dataclasses import dataclass
import math


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def on_segment(p, a, b):
    return abs(cross(a,b,p)) < 1e-9 and min(a[0],b[0])-1e-9 <= p[0] <= max(a[0],b[0])+1e-9 and min(a[1],b[1])-1e-9 <= p[1] <= max(a[1],b[1])+1e-9


def intersects(a,b,c,d):
    if any((on_segment(p,x,y) for p,x,y in ((a,c,d),(b,c,d),(c,a,b),(d,a,b)))):
        return True
    return (cross(a,b,c)>0) != (cross(a,b,d)>0) and (cross(c,d,a)>0) != (cross(c,d,b)>0)


def validate_polygon(points):
    if not isinstance(points,list) or not 3 <= len(points) <= 32:
        raise ValueError('polygon needs 3 to 32 normalized vertices')
    for p in points:
        if not isinstance(p,(list,tuple)) or len(p)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in p):
            raise ValueError('polygon coordinates must be finite numbers in [0,1]')
    if len({tuple(p) for p in points}) != len(points):
        raise ValueError('duplicate polygon vertices')
    n=len(points)
    if abs(sum(points[i][0]*points[(i+1)%n][1]-points[(i+1)%n][0]*points[i][1] for i in range(n)))<1e-8:
        raise ValueError('polygon area is zero')
    for i in range(n):
        for j in range(i+1,n):
            if j==i+1 or (i==0 and j==n-1): continue
            if intersects(points[i],points[(i+1)%n],points[j],points[(j+1)%n]):
                raise ValueError('polygon must not intersect itself')
    return points


def point_inside(point, polygon):
    """Ray casting, including boundary. Use person's bottom-center foot point."""
    inside=False
    x,y=point
    for i,a in enumerate(polygon):
        b=polygon[(i+1)%len(polygon)]
        if on_segment(point,a,b): return True
        if (a[1]>y)!=(b[1]>y):
            if x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: inside=not inside
    return inside


def iou(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union>0 else 0


@dataclass
class Track:
    track_id: int
    box: tuple
    last_seen: float
    state: str = 'unknown'
    candidate: str = ''
    streak: int = 0
    candidate_since: float = 0


class ZoneTracker:
    """Greedy IoU association with max_age; no identity or face recognition.

    Entry requires a confirmed outside state followed by confirmed inside.
    A person first seen inside generates presence, never a fabricated crossing.
    Any missed sampled frame resets the consecutive confirmation streak.
    """
    def __init__(self,polygon,confirm_samples=2,max_age=1.0,min_iou=.15):
        self.polygon=validate_polygon(polygon)
        if type(confirm_samples)!=int or confirm_samples<1: raise ValueError('confirm_samples must be positive integer')
        if not math.isfinite(max_age) or max_age<=0: raise ValueError('max_age must be positive')
        if not 0<min_iou<=1: raise ValueError('min_iou must be in (0,1]')
        self.confirm_samples=confirm_samples; self.max_age=max_age; self.min_iou=min_iou
        self.tracks={}; self.next_id=1; self.previous_time=None

    def update(self,seconds,detections):
        if not math.isfinite(seconds) or seconds<0 or (self.previous_time is not None and seconds<=self.previous_time):
            raise ValueError('sample times must strictly increase')
        self.previous_time=seconds
        for d in detections:
            b=d['box']; c=d['confidence']
            if len(b)!=4 or any(not math.isfinite(v) for v in b) or not (0<=b[0]<b[2]<=1 and 0<=b[1]<b[3]<=1):
                raise ValueError('invalid normalized detection box')
            if not math.isfinite(c) or not 0<=c<=1: raise ValueError('invalid detection confidence')
        self.tracks={k:t for k,t in self.tracks.items() if seconds-t.last_seen<=self.max_age}
        pairs=sorted(((iou(t.box,d['box']),tid,di) for tid,t in self.tracks.items() for di,d in enumerate(detections)),reverse=True)
        used_tracks=set(); assignments={}
        for score,tid,di in pairs:
            if score>=self.min_iou and tid not in used_tracks and di not in assignments:
                assignments[di]=tid; used_tracks.add(tid)
        for tid,t in self.tracks.items():
            if tid not in used_tracks: t.streak=0; t.candidate=''
        results=[]
        for di,d in enumerate(detections):
            if di not in assignments:
                tid=self.next_id; self.next_id+=1
                self.tracks[tid]=Track(tid,tuple(d['box']),seconds)
                assignments[di]=tid
            t=self.tracks[assignments[di]]
            t.box=tuple(d['box']); t.last_seen=seconds
            foot=((t.box[0]+t.box[2])/2,t.box[3])
            candidate='inside' if point_inside(foot,self.polygon) else 'outside'
            if t.candidate==candidate: t.streak+=1
            else: t.candidate=candidate; t.streak=1; t.candidate_since=seconds
            transition=None
            if t.streak>=self.confirm_samples and t.state!=candidate:
                previous=t.state; t.state=candidate
                if candidate=='inside': transition='entry' if previous=='outside' else 'initial_presence'
                elif previous=='inside': transition='exit'
            results.append({'track_id':t.track_id,'box':list(t.box),'confidence':d['confidence'],
                'foot':foot,'inside':candidate=='inside','state':t.state,'transition':transition,
                'candidate_since':t.candidate_since})
        return results
