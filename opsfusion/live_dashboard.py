"""Local camera controls and durable operator review, separate from replay state."""
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import streamlit as st

from .evidence import restored_path
from .history import ACTIVE, History, REVIEW_STATES, utc_now
from .core import parse_time
from .live import launch
from .raw_dashboard import DEFAULT_POLYGON
from .auth_dashboard import authorize, can, account_store
from .notify import Outbox
from .stream_store import StreamStore
from .correlation import WatchStore

LIVE_SOURCE='Live monitoring & history'


def evidence_path(root, ref):
    """Only generated evidence inside this installation's run folders is previewed."""
    parts=urlsplit(ref)
    if parts.scheme!='file' or parts.netloc:
        return None
    text=unquote(parts.path)
    if os.name=='nt' and re.match(r'^/[A-Za-z]:/',text):
        text=text[1:]
    candidate=Path(text).resolve()
    remapped=restored_path(root,ref)
    if remapped is not None:candidate=remapped
    for folder in ('live','raw-ingestion'):
        try:
            candidate.relative_to((Path(root)/'runs'/folder).resolve())
            if candidate.is_file() and candidate.suffix.lower() in ('.jpg','.jpeg','.png') and candidate.stat().st_size<=5*1024*1024:
                return candidate
        except ValueError:
            pass
    return None


@st.fragment(run_every=2)
def status_panel(root):
    try:authorize(root,'read')
    except PermissionError:st.rerun()
    history=History(Path(root)/'runs/history.sqlite3')
    sessions=[s for s in history.sessions() if s['mode'] not in ('http_stream','asset_watch')]
    st.subheader('Camera worker status')
    if not sessions:
        st.info('No saved camera sessions yet. Camera controls and HTTP data streams are independent.')
    rows=[]
    for s in sessions:
        age=max(0,(parse_time(utc_now())-parse_time(s['heartbeat'])).total_seconds())
        rows.append({'session':s['session_id'],'camera':s['camera_id'],'mode':s['mode'],
                     'state':s['state'],'sampled frames':s['sampled_frames'],
                     'observations':s['event_count'],'heartbeat age (s)':round(age),
                     'diagnostic':s['error_code'] or ''})
    if rows:st.dataframe(rows,use_container_width=True,hide_index=True)
    active=[s for s in sessions if s['state'] in ACTIVE]
    for s in active:
        sid=s['session_id']
        cols=st.columns([3,1,1])
        cols[0].write(f"{s['camera_id']} · {s['state']} · session {sid}")
        if cols[1].button('Stop camera',key='stop_'+sid,disabled=not can(root,'capture')):
            authorize(root,'capture')
            history.request_stop(sid);st.success('Stop requested. The worker will release the camera.')
        if cols[2].button('Recover stale worker',key='recover_'+sid,disabled=not can(root,'capture')):
            try:
                authorize(root,'capture')
                history.recover_stale(sid);st.success('Stale session released. A new worker can start.')
            except (ValueError,PermissionError) as exc:
                st.error(str(exc))
    # Latest JPEG is a sampled preview, not a full-motion stream or saved recording.
    camera_sessions=[s for s in sessions if s['mode']!='import']
    if camera_sessions:
        options=[s['session_id'] for s in camera_sessions]
        if st.session_state.get('live_preview_session') not in options:
            st.session_state.live_preview_session=options[0]
        sid=st.selectbox('Camera preview session',options,key='live_preview_session')
        if re.fullmatch('[0-9a-f]{32}',sid):
            image=Path(root)/'runs/live'/sid/'latest.jpg'
            if image.is_file():
                st.image(image.read_bytes(),caption='Latest sampled frame; receipt-time overlay. This image may remain after capture stops.',use_container_width=True)
    notices=history.notices()
    st.write(f'Local unread alerts: {len(notices)}' + (' (showing newest 100)' if len(notices)==100 else ''))
    for n in notices[:10]:
        cols=st.columns([5,1])
        cols[0].caption(f"{n['timestamp']} · {n['reason']} · {n['incident_id']}")
        if cols[1].button('Dismiss',key='dismiss_'+str(n['notice_id']),disabled=not can(root,'review')):
            authorize(root,'review')
            history.dismiss_notice(n['notice_id'])


def render_history(root):
    identity=authorize(root,'read')
    history=History(Path(root)/'runs/history.sqlite3')
    st.subheader('Saved incident history')
    st.caption('New reviews use the signed-in account. Earlier labels remain marked legacy_label. Review status does not change detector facts or confirm the cause.')
    state=st.selectbox('Review status filter',['all',*REVIEW_STATES],key='history_filter')
    if st.button('Refresh history',key='history_refresh'):
        st.session_state.pop('history_snapshot',None)
    rows=history.incidents(state)
    if not rows:
        st.info('No incidents match this filter.')
    else:
        st.dataframe([{'incident':r['incident_id'],'asset':r['asset_id'],'type':r['kind'],
                       'review':r['review_state'],'revision':r['revision'],
                       'synthetic':r['current_report']['synthetic'],'detected at':r['current_report']['detected_at']} for r in rows],hide_index=True,use_container_width=True)
        ids=[r['incident_id'] for r in rows]
        if st.session_state.get('history_incident') not in ids:
            st.session_state.history_incident=ids[0]
        iid=st.selectbox('Saved incident',ids,key='history_incident')
        snapshot=st.session_state.get('history_snapshot')
        if snapshot is None or snapshot['incident_id']!=iid:
            snapshot=next(r for r in rows if r['incident_id']==iid)
            st.session_state.history_snapshot=snapshot
        st.caption(f"Reviewing saved snapshot revision {snapshot['revision']}. Refresh history to load changes from the worker.")
        scope=st.radio('Saved evidence scope',['first','current'],horizontal=True,key='history_scope')
        report=snapshot['first_report'] if scope=='first' else snapshot['current_report']
        st.write(report['hypothesis']['statement'])
        st.write('Detector conclusion: '+report['hypothesis']['status']+' · priority: '+report['risk'])
        st.dataframe(report['observed_facts'],hide_index=True,use_container_width=True)
        evidence={f['event_id']:f['evidence_ref'] for f in report['observed_facts']}
        if evidence:
            ids=list(evidence)
            if st.session_state.get('history_evidence') not in ids:
                st.session_state.history_evidence=ids[0]
            event_id=st.selectbox('Saved observation snapshot',ids,key='history_evidence')
            path=evidence_path(root,evidence[event_id])
            if path:
                st.image(path.read_bytes(),caption=event_id,use_container_width=True)
            elif evidence[event_id].startswith('opsfusion://stream/'):
                parts=urlsplit(evidence[event_id]);bits=parts.path.strip('/').split('/')
                if len(bits)==3 and re.fullmatch('[0-9a-f]{32}',bits[0]) and bits[1]=='input' and re.fullmatch('[A-Za-z0-9_.-]{1,120}',bits[2]):
                    store=StreamStore(root,account_store(root).path)
                    with store.history.connect() as db:
                        row=db.execute('SELECT payload,result FROM stream_inputs WHERE stream_id=? AND message_id=?',(bits[0],bits[2])).fetchone()
                        if row:
                            result=json.loads(row['result']);ids=result.get('confirmation_input_ids',[]) or [bits[2]]
                            records=[]
                            for identifier in ids:
                                r=db.execute('SELECT payload,result FROM stream_inputs WHERE stream_id=? AND message_id=?',(bits[0],identifier)).fetchone()
                                if r:records.append({'input':json.loads(r['payload']),'detector_result':json.loads(r['result'])})
                            st.json(records)
                            st.caption('Saved acquisition declarations and scores at confirmation; no future readings are included.')
            else:
                st.caption('Snapshot is unavailable here. Evidence references remain in the saved report.')
        with st.form('history_review_form'):
            st.caption('Operator account: '+identity['username'])
            review_state=st.selectbox('Set review status',REVIEW_STATES,key='history_review_state')
            note=st.text_area('Review note',max_chars=2000,key='history_note')
            submitted=st.form_submit_button('Save review',disabled=not can(root,'review'))
        if submitted:
            try:
                actor=authorize(root,'review')['username']
                history.review(iid,review_state,actor,note,snapshot['revision'],authenticated=True)
                st.session_state.pop('history_snapshot',None)
                st.success('Review saved with audit record.')
                st.rerun()
            except (ValueError,PermissionError) as exc:
                st.error(str(exc))
        with st.expander('Operator audit trail'):
            st.json(history.audit(iid))
    st.download_button('Download incident history JSON',json.dumps(history.export(),indent=2).encode(),
                       'opsfusion-history.json','application/json',key='history_export',on_click='ignore')
    st.caption('History persists in runs/history.sqlite3 across dashboard restarts. Keep the complete runs folder to preserve snapshots; JSON does not include media.')


def render_live(root):
    authorize(root,'read')
    st.subheader('Live monitoring & history')
    st.write('Capture runs on the computer hosting this dashboard. Closing the browser does not stop the camera worker; use Stop camera or its configured duration.')
    if st.session_state.get('live_last_started'):
        st.success('Worker started: '+st.session_state.pop('live_last_started'))
    with st.expander('Connect a camera',expanded=can(root,'capture')):
        mode=st.selectbox('Camera connection',['Webcam','RTSP camera'],key='live_mode')
        webcam=0;url=''
        if mode=='Webcam':
            webcam=int(st.number_input('Webcam index',min_value=0,max_value=20,value=0,key='live_webcam'))
        else:
            if st.session_state.pop('clear_rtsp_input',False):
                st.session_state.live_rtsp=''
            url=st.text_input('RTSP URL',type='password',key='live_rtsp',placeholder='rtsp://user:password@camera-address:554/stream')
            st.caption('The URL is passed in memory to the local worker. It is excluded from history, reports and process arguments.')
        asset=st.text_input('Live asset ID',value='warehouse-zone-01',key='live_asset')
        camera=st.text_input('Live camera ID',value='camera-live-01',key='live_camera')
        polygon=st.text_area('Live zone polygon',value=DEFAULT_POLYGON,key='live_polygon')
        st.caption('Vertices use normalized x/y coordinates from 0 to 1. The example must be adjusted to your camera view; the person’s foot position determines membership.')
        fps=st.number_input('Target live sample FPS',min_value=1.0,max_value=10.0,value=5.0,key='live_fps')
        duration=st.number_input('Stop after N seconds',min_value=1,max_value=86400,value=3600,key='live_duration')
        synthetic=st.checkbox('This camera source is generated/composed',key='live_synthetic')
        st.caption('UTC timestamps use this computer’s frame receipt time, not the camera’s original capture clock. Initial presence is distinct from entry. CPU speed determines achieved sample rate.')
        if st.button('Start camera worker',type='primary',key='live_start',disabled=not can(root,'capture')):
            try:
                authorize(root,'capture')
                zone={'asset_id':asset,'camera_id':camera,'polygon':json.loads(polygon),
                      'confirm_samples':2,'max_age_seconds':1,'min_iou':.15}
                sid=launch(root,zone,'webcam' if mode=='Webcam' else 'rtsp',webcam if mode=='Webcam' else url,fps,duration,synthetic)
                if mode=='RTSP camera':
                    st.session_state.clear_rtsp_input=True
                st.session_state.live_last_started=sid
                st.rerun()
            except (ValueError,RuntimeError,OSError,PermissionError) as exc:
                st.error(str(exc))
    status_panel(root)
    stream_panel(root)
    watch_panel(root)
    render_history(root)
    render_notifications(root)


@st.fragment(run_every=2)
def stream_panel(root):
    try:authorize(root,'read')
    except PermissionError:st.rerun()
    store=StreamStore(root,account_store(root).path);token=st.session_state.security_token
    st.subheader('Live sensor & controller streams')
    st.caption('HTTP input sessions share this installation’s history. Receipt age describes incoming data, not machine health. Test streams and synthetic baselines keep their synthetic flag. Use Linked asset watches to correlate selected camera and HTTP sessions.')
    streams=store.streams(token)
    if not streams:
        st.info('No HTTP streams yet. Prepare a normal baseline, start the local API, then run the marked test sender or connect a device client.')
        return
    st.dataframe([{'stream':s['stream_id'],'asset':s['asset_id'],'source':s['source_id'],'state':s['state'],
                   'input status':s['input_status'],'readings':s['reading_count'],'controller records':s['log_count'],
                   'observations':s['event_count'],'sensor gaps':s['gap_count'],'synthetic':s['effective_synthetic'],
                   'receipt age (s)':None if s['last_receipt_age_seconds'] is None else round(s['last_receipt_age_seconds'],1)} for s in streams],hide_index=True,use_container_width=True)
    ids=[s['stream_id'] for s in streams]
    if st.session_state.get('http_stream_selected') not in ids:st.session_state.http_stream_selected=ids[0]
    sid=st.selectbox('HTTP stream',ids,key='http_stream_selected')
    if next(s for s in streams if s['stream_id']==sid)['state']=='open':
        if st.button('Close HTTP stream',key='http_stream_close',disabled=not can(root,'analyse')):
            authorize(root,'analyse');store.close(token,sid);st.rerun()
    records=list(reversed(store.records(token,sid,100)))
    measurements=[{**r['payload'],**(r['result']['score'] or {}),'effective_synthetic':r['result']['synthetic']} for r in records if r['kind']=='sensor']
    if measurements:
        st.write('Latest accepted measurements (up to 100 inputs)')
        st.dataframe(measurements,hide_index=True,use_container_width=True)
    controller=[r['payload'] for r in records if r['kind']=='log']
    if controller:
        st.write('Latest controller records');st.dataframe(controller,hide_index=True,use_container_width=True)
    st.download_button('Download HTTP stream evidence JSON',json.dumps(store.export(token,sid),indent=2).encode(),
                       'opsfusion-http-stream.json','application/json',key='http_stream_export',on_click='ignore')


@st.fragment(run_every=2)
def watch_panel(root):
    try:authorize(root,'read')
    except PermissionError:st.rerun()
    store=WatchStore(root,account_store(root).path);token=st.session_state.security_token
    st.subheader('Linked asset watches')
    st.caption('Link one active camera and one open HTTP stream for exactly the same asset. Only new observations after linking can form joint reports. Camera presence does not prove entry or a machine fault. Gaps reset pairing; closing either source closes its watch.')
    rows=store.watches(token)
    if rows:
        st.dataframe([{k:w[k] for k in ('watch_id','asset_id','state','horizon_seconds','event_count','excluded_count','reset_count','close_reason')} for w in rows],hide_index=True,use_container_width=True)
        ids=[w['watch_id'] for w in rows]
        if st.session_state.get('asset_watch_selected') not in ids:st.session_state.asset_watch_selected=ids[0]
        wid=st.selectbox('Asset watch',ids,key='asset_watch_selected')
        if next(w for w in rows if w['watch_id']==wid)['state']=='active':
            if st.button('Close asset watch',disabled=not can(root,'analyse'),key='watch_close'):
                store.close(token,wid);st.rerun()
        data=store.export(token,wid)
        st.write(f"Joint reports in selected watch: {len(data['incidents'])}")
        st.download_button('Download linked evidence JSON',json.dumps(data,indent=2).encode(),'opsfusion-linked-evidence.json','application/json',key='watch_export',on_click='ignore')
    else:st.info('No linked asset watches yet. The optional marked integration demo needs no camera hardware.')
    sessions=store.history.sessions(500)
    cameras={s['session_id']:s for s in sessions if s['mode'] in ('webcam','rtsp','file_replay') and s['state'] in ACTIVE and not s['stop_requested']}
    streams={s['session_id']:s for s in sessions if s['mode']=='http_stream' and s['state']=='running'}
    used_camera={w['camera_session'] for w in rows if w['state']=='active'}
    used_http={w['http_stream'] for w in rows if w['state']=='active'}
    cameras={k:v for k,v in cameras.items() if k not in used_camera}
    streams={k:v for k,v in streams.items() if k not in used_http}
    with st.expander('Link active sources'):
        if cameras and streams:
            camera=st.selectbox('Camera session to link',list(cameras),format_func=lambda k:cameras[k]['camera_id']+' · '+cameras[k]['asset_id']+' · '+k,key='watch_camera')
            matches=[k for k,v in streams.items() if v['asset_id']==cameras[camera]['asset_id']]
            if matches:
                stream=st.selectbox('HTTP stream to link',matches,key='watch_http')
                horizon=st.number_input('Cooccurrence window (seconds)',min_value=1.,max_value=30.,value=10.,key='watch_horizon')
                if st.button('Link selected sources',disabled=not can(root,'analyse'),key='watch_open'):
                    try:
                        store.open(token,camera,stream,horizon);st.success('Sources linked for new observations.');st.rerun()
                    except (ValueError,PermissionError) as exc:st.error(str(exc))
            else:st.info('No unlinked open HTTP stream has this camera’s exact asset ID.')
        else:st.info('Start a camera and an HTTP stream before linking. Each source can belong to one active watch.')


def render_notifications(root):
    authorize(root,'read')
    outbox=Outbox(History(Path(root)/'runs/history.sqlite3'),account_store(root))
    token=st.session_state.security_token
    st.subheader('Notification queue')
    st.caption('Preview entries never send. An admin must enable a configured email or webhook destination, then run the notification worker. Only new notices after enabling enter that destination; synthetic sources are blocked unless explicitly allowed.')
    st.button('Refresh delivery status',key='notify_refresh')
    if st.button('Build notification previews and queue',key='notify_sync',disabled=not can(root,'notify_admin')):
        try:
            authorize(root,'notify_admin');count=outbox.sync(token)
            st.success(f'Queued {count} new preview/delivery records. No network request was made.')
        except PermissionError as exc:st.error(str(exc))
    rows=outbox.rows(token)
    st.dataframe([{k:r[k] for k in ('id','destination','channel','notice_id','state','attempts','status_code','error_code')} for r in rows],hide_index=True,use_container_width=True)
    if rows:
        ids=[r['id'] for r in rows]
        if st.session_state.get('notification_record') not in ids:st.session_state.notification_record=ids[0]
        identifier=st.selectbox('Notification record',ids,key='notification_record')
        with st.expander('Notification payload preview'):
            st.json(next(r['payload'] for r in rows if r['id']==identifier))
    st.download_button('Download notification delivery records',json.dumps(outbox.export(token),indent=2).encode(),
                       'opsfusion-notifications.json','application/json',key='notify_export',on_click='ignore')
    if can(root,'notify_admin'):
        with st.expander('Admin · notification destination'):
            st.dataframe(outbox.destinations(token),hide_index=True,use_container_width=True)
            channel=st.selectbox('Delivery channel',['Email inbox','HTTPS webhook'],key='notify_channel')
            kind='email' if channel=='Email inbox' else 'webhook'
            with st.form('notification_config'):
                name=st.text_input('Destination name',value='EmailInbox' if kind=='email' else 'OperationsWebhook',max_chars=64,key='notify_name_'+kind)
                env_name=st.text_input('Settings environment variable' if kind=='email' else 'Webhook URL environment variable',value='OPSFUSION_EMAIL_SETTINGS' if kind=='email' else 'OPSFUSION_WEBHOOK_URL',max_chars=90,key='notify_env_'+kind)
                enabled=st.checkbox('Enable delivery of new alerts to this destination',value=False,key='notify_enabled')
                synthetic=st.checkbox('Allow synthetic/demo alerts at this destination',value=False)
                saved=st.form_submit_button('Save destination settings')
            if saved:
                try:
                    authorize(root,'notify_admin');outbox.configure(token,name,env_name,enabled,synthetic,kind)
                    st.success('Destination settings saved. Previous pending deliveries for this destination were cancelled; future notices use these settings.')
                except (ValueError,PermissionError) as exc:st.error(str(exc))
            st.caption('Email: run .\\tools\\set_email_environment.ps1 in both dashboard and worker terminals. SMTP uses verified TLS and one inbox. Addresses/passwords stay in the process environment and are excluded from reports. Slack formatting is not included.')
            emails=[d['name'] for d in outbox.destinations(token) if d['channel']=='email' and d['enabled']]
            if emails:
                test_name=st.selectbox('Test email destination',emails,key='notify_test_destination')
                if st.button('Queue test email',key='notify_test_email'):
                    try:
                        authorize(root,'notify_admin');identifier=outbox.queue_test_email(token,test_name)
                        st.success('Test email queued: '+identifier+'. Run the notification worker, then Refresh delivery status. No incident was created.')
                    except (ValueError,PermissionError) as exc:st.error(str(exc))
            else:
                st.info('Enable an email destination to queue a connection test.')
            st.caption('Email delivered means the SMTP server accepted the submission. Check your inbox and spam folder; inbox placement and human receipt are not guaranteed.')
            st.code('conda run --no-capture-output -n opsfusion312 python -m opsfusion.notify work --username YOUR_ADMIN_USERNAME',language='powershell')
        with st.expander('Admin · accounts and access audit'):
            st.dataframe(account_store(root).users(token),hide_index=True,use_container_width=True)
            st.json(account_store(root).audit(token))
