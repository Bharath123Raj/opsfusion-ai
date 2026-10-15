"""Actual paced HTTP sender + local API + browser history/alerts; synthetic only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from opsfusion.accounts import Accounts
from opsfusion.stream_store import StreamStore


def main():
    with tempfile.TemporaryDirectory(prefix='opsfusion-stream-browser-') as td:
        site=Path(td);shutil.copy(ROOT/'dashboard.py',site/'dashboard.py');shutil.copytree(ROOT/'data',site/'data')
        accounts=Accounts(site/'runs/accounts.sqlite3');password='Browser stream fixture password 123!'
        accounts.bootstrap('fixture-admin',password);token=accounts.login('fixture-admin',password)
        accounts.add_user(token,'fixture-viewer','Browser viewer password 123!','viewer')
        store=StreamStore(site);baseline=store.register(token,ROOT/'data/sensor-demo/train.csv',ROOT/'data/sensor-demo/validation.csv')
        env=dict(os.environ);env['PYTHONPATH']=str(ROOT)
        children=[];errors=[]
        with (site/'server.log').open('w') as server_log,(site/'sender.log').open('w') as sender_log:
            try:
                api=subprocess.Popen([sys.executable,'-m','uvicorn','opsfusion.api:app','--host','127.0.0.1','--port','18512'],cwd=site,env=env,stdout=server_log,stderr=server_log);children.append(api)
                dashboard=subprocess.Popen([sys.executable,'-m','streamlit','run','dashboard.py','--server.port','18514','--server.address','127.0.0.1'],cwd=site,env=env,stdout=server_log,stderr=server_log);children.append(dashboard)
                for url in ('http://127.0.0.1:18512/health','http://127.0.0.1:18514/_stcore/health'):
                    for _ in range(100):
                        try:urllib.request.urlopen(url,timeout=1);break
                        except Exception:time.sleep(.1)
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page()
                    page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:18514')
                    def signin(name,pw):
                        page.get_by_label('Username',exact=True).fill(name);page.get_by_label('Password',exact=True).fill(pw)
                        page.get_by_role('button',name='Sign in',exact=True).click()
                    def live():
                        page.get_by_text('Analyse real recordings',exact=True).wait_for()
                        page.get_by_role('combobox').first.click();page.get_by_role('option',name='Live monitoring & history',exact=True).click()
                    signin('fixture-admin',password);live()
                    sender=subprocess.Popen([sys.executable,'-m','opsfusion.stream','demo','--username','fixture-admin','--url','http://127.0.0.1:18512','--baseline-id',baseline['baseline_id'],'--seconds','35'],cwd=site,env=env,stdin=subprocess.PIPE,stdout=sender_log,stderr=sender_log,text=True);children.append(sender)
                    sender.stdin.write(password+'\n');sender.stdin.flush()
                    sid=None
                    for _ in range(120):
                        streams=store.streams(token)
                        if streams:
                            sid=streams[0]['stream_id']
                            if streams[0]['reading_count']>=15:break
                        page.wait_for_timeout(150)
                    assert sid and store.get(token,sid)['reading_count']>=15
                    page.get_by_text('Latest accepted measurements',exact=False).wait_for()
                    assert store.history.notices()
                    sender.communicate(timeout=45);assert sender.returncode==0
                    status=store.get(token,sid)
                    assert status['state']=='closed' and status['reading_count']==35 and status['log_count']==3 and status['event_count']==5,status
                    reports=[r for r in store.history.incidents() if r['session_id']==sid]
                    assert {r['kind'] for r in reports}=={'sensor_anomaly','operational_warning','machine_degradation','abnormal_shutdown'}
                    assert all(r['first_report']['synthetic'] and r['current_report']['hypothesis']['status']=='unconfirmed' for r in reports)
                    assert len(store.history.notices())==4
                    page.wait_for_timeout(2500)
                    page.get_by_text('Local unread alerts: 4',exact=True).wait_for()
                    with page.expect_download() as info:
                        page.get_by_role('button',name='Download HTTP stream evidence JSON',exact=True).click()
                    info.value.save_as(site/'download.json');data=json.loads((site/'download.json').read_text())
                    assert len(data['inputs'])==38 and len(data['events'])==5
                    assert password not in json.dumps(data) and token not in json.dumps(data)
                    page.get_by_role('button',name='Refresh history',exact=True).click()
                    incident=next(r for r in reports if r['kind']=='sensor_anomaly')
                    select=page.locator('[data-testid="stSelectbox"]').filter(has_text='Saved incident').get_by_role('combobox')
                    select.click();page.get_by_role('option',name=incident['incident_id'],exact=True).click()
                    page.get_by_text('Saved acquisition declarations and scores at confirmation;',exact=False).wait_for()
                    page.reload();page.get_by_text('Sign in to OpsFusion',exact=True).wait_for();signin('fixture-admin',password);live()
                    page.get_by_text('Latest accepted measurements',exact=False).wait_for()
                    page.get_by_role('button',name='Sign out',exact=True).click();page.get_by_text('Sign in to OpsFusion',exact=True).wait_for()
                    signin('fixture-viewer','Browser viewer password 123!')
                    page.get_by_text('Live sensor & controller streams',exact=True).wait_for()
                    expect(page.get_by_role('button',name='Save review',exact=True)).to_be_disabled()
                    assert page.locator('[data-testid="stException"]').count()==0 and not errors
                    browser.close()
                proof={'application_version':'0.12.0','passed':True,'actual_http_sender_and_api':True,'paced_readings':35,
                       'controller_records':3,'adapter_observations':5,'saved_incidents':4,'local_first_alert_notices':4,
                       'causes_unconfirmed':True,'synthetic_propagated':True,'evidence_download_inputs':38,
                       'browser_live_counts_and_evidence':True,'reload_persists':True,'viewer_read_only':True,
                       'browser_local_alerts_visible_without_camera':True,
                       'javascript_errors':len(errors),'external_notifications_sent':0,
                       'scope':'Generated readings and authored controller messages over actual loopback HTTP, baseline inference and browser. No physical hardware or real accuracy measurement.'}
                (ROOT/'reports/step12_stream_browser_validation.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))
            finally:
                for child in reversed(children):
                    if child.poll() is None:child.terminate();child.wait(timeout=10)


if __name__=='__main__':main()
