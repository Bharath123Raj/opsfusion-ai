"""Actual API + paced decoded fixture + browser joint review; disposable site."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from opsfusion.accounts import Accounts
from opsfusion.correlation import WatchStore
from opsfusion.stream_store import StreamStore


def main():
    with tempfile.TemporaryDirectory(prefix='opsfusion-linked-browser-') as td:
        site=Path(td);shutil.copy(ROOT/'dashboard.py',site/'dashboard.py');shutil.copytree(ROOT/'data',site/'data')
        accounts=Accounts(site/'runs/accounts.sqlite3');password='Browser linked fixture password 123!'
        accounts.bootstrap('fixture-admin',password);token=accounts.login('fixture-admin',password)
        accounts.add_user(token,'fixture-viewer','Browser viewer password 123!','viewer')
        streams=StreamStore(site);streams.register(token,ROOT/'data/sensor-demo/train.csv',ROOT/'data/sensor-demo/validation.csv')
        watches=WatchStore(site);env=dict(os.environ);env['PYTHONPATH']=str(ROOT)
        children=[];errors=[]
        with (site/'server.log').open('w') as server_log,(site/'sender.log').open('w') as sender_log:
            try:
                for cmd in [
                    [sys.executable,'-m','uvicorn','opsfusion.api:app','--host','127.0.0.1','--port','18612'],
                    [sys.executable,'-m','streamlit','run','dashboard.py','--server.port','18614','--server.address','127.0.0.1']]:
                    children.append(subprocess.Popen(cmd,cwd=site,env=env,stdout=server_log,stderr=server_log))
                for url in ('http://127.0.0.1:18612/health','http://127.0.0.1:18614/_stcore/health'):
                    for attempt in range(120):
                        try:urllib.request.urlopen(url,timeout=1);break
                        except Exception:
                            if attempt==119:raise RuntimeError('Fixture server did not start')
                            time.sleep(.1)
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page()
                    page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:18614')
                    def signin(name,pw):
                        page.get_by_label('Username',exact=True).fill(name);page.get_by_label('Password',exact=True).fill(pw)
                        page.get_by_role('button',name='Sign in',exact=True).click()
                    def live():
                        page.get_by_text('Analyse real recordings',exact=True).wait_for()
                        page.get_by_role('combobox').first.click();page.get_by_role('option',name='Live monitoring & history',exact=True).click()
                    signin('fixture-admin',password);live()
                    sender=subprocess.Popen([sys.executable,'-m','opsfusion.watch','demo','--username','fixture-admin','--url','http://127.0.0.1:18612'],cwd=site,env=env,stdin=subprocess.PIPE,stdout=sender_log,stderr=sender_log,text=True)
                    children.append(sender);sender.stdin.write(password+'\n');sender.stdin.flush()
                    for _ in range(200):
                        rows=watches.watches(token)
                        if rows and rows[0]['event_count']>=2:break
                        if sender.poll() is not None:break
                        page.wait_for_timeout(100)
                    assert rows, (site/'sender.log').read_text()
                    wid=rows[0]['watch_id']
                    page.get_by_role('button',name='Close asset watch',exact=True).wait_for(timeout=15000)
                    sender.communicate(timeout=35)
                    assert sender.returncode==0,(site/'sender.log').read_text()
                    data=watches.export(token,wid)
                    assert data['watch']['state']=='closed' and data['watch']['close_reason']=='linked_source_closed'
                    assert len(data['events'])==5 and len(data['incidents'])==2,data
                    assert len(data['supporting_inputs'])==5
                    assert all(r['current_report']['synthetic'] and r['current_report']['hypothesis']['status']=='unconfirmed' for r in data['incidents'])
                    assert len(streams.history.notices())==7
                    status=streams.get(token,data['watch']['http_stream'])
                    assert (status['reading_count'],status['log_count'],status['event_count'])==(18,2,3)
                    camera=streams.history.session(data['watch']['camera_session'])
                    assert camera['sampled_frames']==36 and camera['event_count']==2
                    assert len(list((site/'runs/live'/camera['session_id']/'evidence').glob('*.jpg')))==2
                    page.get_by_text('Joint reports in selected watch: 2',exact=True).wait_for(timeout=15000)
                    page.get_by_text('Local unread alerts: 7',exact=True).wait_for(timeout=15000)
                    with page.expect_download() as download:
                        page.get_by_role('button',name='Download linked evidence JSON',exact=True).click()
                    download.value.save_as(site/'linked.json');saved=json.loads((site/'linked.json').read_text())
                    assert len(saved['incidents'])==2 and len(saved['supporting_inputs'])==5
                    assert password not in json.dumps(saved) and token not in json.dumps(saved)
                    page.get_by_role('button',name='Refresh history',exact=True).click()
                    report=data['incidents'][0]
                    selector=page.locator('[data-testid="stSelectbox"]').filter(has_text='Saved incident').get_by_role('combobox')
                    selector.click();page.get_by_role('option',name=report['incident_id'],exact=True).click()
                    page.get_by_text('Zone activity and machine signals observed close in time',exact=True).wait_for()
                    # Both snapshot and raw acquisition evidence are available from the joint report.
                    evidence=page.locator('[data-testid="stSelectbox"]').filter(has_text='Saved observation snapshot').get_by_role('combobox')
                    video=next(e for e in report['first_report']['timeline'] if e['modality']=='video')
                    evidence.click();page.get_by_role('option',name=video['event_id'],exact=True).click()
                    assert page.locator('[data-testid="stImage"]').count()>=1
                    sensor=next(e for e in report['first_report']['timeline'] if e['modality']=='sensor')
                    evidence.click();page.get_by_role('option',name=sensor['event_id'],exact=True).click()
                    page.get_by_text('Saved acquisition declarations and scores at confirmation;',exact=False).wait_for()
                    state=page.locator('[data-testid="stSelectbox"]').filter(has_text='Set review status').get_by_role('combobox')
                    state.click();page.get_by_role('option',name='acknowledged',exact=True).click()
                    page.get_by_label('Review note',exact=True).fill('Synthetic joint evidence reviewed in browser fixture')
                    page.get_by_role('button',name='Save review',exact=True).click();page.wait_for_timeout(700)
                    assert next(r for r in watches.export(token,wid)['incidents'] if r['incident_id']==report['incident_id'])['review_state']=='acknowledged'
                    page.reload();page.get_by_text('Sign in to OpsFusion',exact=True).wait_for();signin('fixture-admin',password);live()
                    page.get_by_text('Joint reports in selected watch: 2',exact=True).wait_for()
                    assert next(r for r in watches.export(token,wid)['incidents'] if r['incident_id']==report['incident_id'])['review_state']=='acknowledged'
                    page.get_by_role('button',name='Sign out',exact=True).click();page.get_by_text('Sign in to OpsFusion',exact=True).wait_for()
                    signin('fixture-viewer','Browser viewer password 123!')
                    page.get_by_text('Linked asset watches',exact=True).wait_for()
                    expect(page.get_by_role('button',name='Save review',exact=True)).to_be_disabled()
                    assert page.locator('[data-testid="stException"]').count()==0 and not errors
                    browser.close()
                proof={'application_version':'0.13.0','passed':True,'actual_loopback_http':True,'paced_seconds':18,
                       'decoded_synthetic_frames':36,'sensor_readings':18,'controller_records':2,
                       'zone_entry_observations':2,'machine_observations':3,'linked_evidence_events':5,
                       'joint_reports':2,'all_new_reports_including_source_reports':7,'local_notices':7,
                       'supporting_measurements_and_logs_in_download':5,'synthetic_propagated':True,
                       'camera_snapshot_and_raw_input_preview':True,'browser_review_saved_and_survived_reload':True,
                       'source_close_closes_watch':True,'viewer_read_only':True,'javascript_errors':len(errors),
                       'external_notifications_sent':0,'scope':'Generated video with a green-rectangle fixture detector and generated readings/controller records. Tests transport, tracking, fusion, evidence, review and persistence; no hardware or YOLO/person-detection accuracy measurement.'}
                (ROOT/'reports/step13_linked_browser_validation.json').write_text(json.dumps(proof,indent=2)+'\n')
                print(json.dumps(proof,indent=2))
            except Exception:
                print((site/'sender.log').read_text()[-4000:]);print((site/'server.log').read_text()[-4000:]);raise
            finally:
                for child in reversed(children):
                    if child.poll() is None:child.terminate();child.wait(timeout=10)


if __name__=='__main__':main()
