"""Gap-delimited episodes with family-specific qualifying activity.

A silence in sparse adapter observations is a heuristic, not verified recovery.
"""
import math
from .core import fuse,build_incident

MECHANICAL={'vibration_high','temperature_high','sensor_anomaly','abnormal_noise','motor_warning','shutdown'}
INDICATORS=MECHANICAL-{'shutdown'}
SAFETY={'zone_entry','zone_presence'}
CONTEXT={'recent_repair','operator_note'}


def qualified_pair(events,window_seconds):
    """Return first confirmation time and unique corroborating observations.

    Each supporting pair must be within the correlation horizon. A continuing
    episode can outlive that horizon without duplicating an existing alert.
    """
    first=None;support={}
    for index,right in enumerate(events):
        for left in events[:index]:
            if right.modality!=left.modality and (right.time-left.time).total_seconds()<=window_seconds:
                if first is None:first=right.time
                support[left.event_id]=left;support[right.event_id]=right
    return first,sorted(support.values(),key=lambda e:(e.time,e.event_id))


def fuse_episodes(events,window_seconds=90,quiet_gap_seconds=20,minimum_confidence=.6):
    if not math.isfinite(quiet_gap_seconds) or quiet_gap_seconds<=0:raise ValueError('quiet gap must be positive and finite')
    # Share legacy validation, including confidence, duplicate IDs and horizon.
    fuse(events,window_seconds=window_seconds,minimum_confidence=minimum_confidence)
    unique={e.event_id:e for e in events}
    accepted=sorted([e for e in unique.values() if e.confidence>=minimum_confidence],key=lambda e:(e.time,e.event_id))
    contexts={};groups={}
    for e in accepted:
        if e.kind in CONTEXT:contexts.setdefault(e.asset_id,[]).append(e);continue
        family='mechanical' if e.kind in MECHANICAL else 'safety'
        groups.setdefault((e.asset_id,family),[]).append(e)
    incidents=[];episodes=[];edges={}
    for (asset,family),observations in sorted(groups.items()):
        clusters=[]
        for e in observations:
            if not clusters or (e.time-clusters[-1][-1].time).total_seconds()>quiet_gap_seconds:clusters.append([e])
            else:clusters[-1].append(e)
        for cluster in clusters:
            anchor=cluster[0];episode_id=f'episode-v1:{asset}:{family}:{anchor.event_id}'
            # Nearby context never changes boundaries or the stable anchor ID.
            context=[e for e in contexts.get(asset,[]) if abs((e.time-anchor.time).total_seconds())<=window_seconds]
            evidence=sorted(cluster+context,key=lambda e:(e.time,e.event_id))
            episodes.append({'episode_id':episode_id,'asset_id':asset,'family':family,'start':anchor.time.isoformat(),
                'last_qualifying_at':cluster[-1].time.isoformat(),'qualifying_event_ids':[e.event_id for e in cluster],
                'context_event_ids':[e.event_id for e in context],
                'note':'Gap split is inferred from observation availability, not verified physical recovery.'})
            for index,left in enumerate(evidence):
                for right in evidence[index+1:]:
                    # Temporal graph edges are bounded even when episode spans hours.
                    delta=(right.time-left.time).total_seconds()
                    if delta<=window_seconds:
                        key=(left.event_id,right.event_id)
                        edges[key]={'source':left.event_id,'target':right.event_id,'relationship':'same_episode_temporal_cooccurrence','delta_seconds':delta}
            def add(kind,triggers,confirmed):
                i=build_incident(asset,kind,evidence,triggers)
                i['incident_id']=f'{episode_id}:{kind}';i['episode_id']=episode_id
                i['detected_at']=confirmed.isoformat();i['segmentation']='quiet_gap_v1'
                i['start']=anchor.time.isoformat();i['end']=cluster[-1].time.isoformat()
                incidents.append(i)
            if family=='mechanical':
                indicators=[e for e in cluster if e.kind in INDICATORS]
                first,support=qualified_pair(indicators,window_seconds)
                if first is not None:add('machine_degradation',support,first)
                for event_kind,report_kind in [('sensor_anomaly','sensor_anomaly'),('abnormal_noise','acoustic_anomaly'),('motor_warning','operational_warning')]:
                    triggers=[e for e in cluster if e.kind==event_kind]
                    if triggers:add(report_kind,triggers,triggers[0].time)
                stops=[e for e in cluster if e.kind=='shutdown'];support={};confirmed=None
                for stop in stops:
                    prior=[e for e in indicators if 0<=(stop.time-e.time).total_seconds()<=window_seconds]
                    if prior:
                        if confirmed is None:confirmed=stop.time
                        for e in prior+[stop]:support[e.event_id]=e
                if confirmed is not None:add('abnormal_shutdown',sorted(support.values(),key=lambda e:(e.time,e.event_id)),confirmed)
            else:
                for event_kind,report_kind in [('zone_entry','restricted_zone'),('zone_presence','restricted_zone_presence')]:
                    triggers=[e for e in cluster if e.kind==event_kind]
                    if triggers:add(report_kind,triggers,triggers[0].time)
    return {'schema_version':'1.0','engine':'quiet_gap_episode_fusion_v1','incidents':incidents,
        'episodes':episodes,'event_graph':{'nodes':[e.to_dict() for e in accepted],'edges':list(edges.values())},
        'input_events':len(unique),'accepted_events':len(accepted),'segmentation_config':{'quiet_gap_seconds':quiet_gap_seconds,'correlation_horizon_seconds':window_seconds},
        'limitations':['Sparse observations cannot establish that a machine recovered during a gap.',
            'Safety and mechanical activity are segmented separately; neither can bridge the other.',
            'Continuous sparse warnings with gaps above the threshold can be split unnecessarily.',
            'Corroboration uses same-asset different-modality pairs within the horizon; no causal diagnosis.']}
