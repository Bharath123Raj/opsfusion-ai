"""Optional browser integration check. Requires Playwright + installed Chromium.

Uses a temporary dashboard root and authored synthetic observations. No camera
hardware, external messages, or accuracy measurements are involved.
"""
import argparse
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
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from opsfusion.history import History
from opsfusion.accounts import Accounts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtsp-video',type=Path,help='Optional composed loopback camera start/stop check; needs FFmpeg')
    parser.add_argument('--weights',type=Path,help='Existing YOLOv8n weights for the optional camera check')
    args=parser.parse_args()
    if args.rtsp_video and (not args.weights or not args.weights.is_file() or not args.rtsp_video.is_file()):
        parser.error('Optional RTSP check needs existing --rtsp-video and --weights')
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as td:
        site=Path(td)
        shutil.copy(root/'dashboard.py',site/'dashboard.py')
        shutil.copytree(root/'data',site/'data')
        if args.weights:shutil.copy(args.weights,site/'yolov8n.pt')
        accounts=Accounts(site/'runs/accounts.sqlite3')
        accounts.bootstrap('fixture-admin','Browser fixture password 123!')
        bootstrap_token=accounts.login('fixture-admin','Browser fixture password 123!')
        accounts.add_user(bootstrap_token,'fixture-viewer','Browser viewer password 123!','viewer')
        accounts.logout(bootstrap_token)
        env=dict(os.environ);env['PYTHONPATH']=str(root)
        with (site/'server.log').open('w') as log:
            server=subprocess.Popen([sys.executable,'-m','streamlit','run','dashboard.py','--server.port','18509','--server.address','127.0.0.1'],
                                    cwd=site,env=env,stdout=log,stderr=log)
            fixture=None
            try:
                for _ in range(100):
                    try:urllib.request.urlopen('http://127.0.0.1:18509/_stcore/health',timeout=1);break
                    except Exception:time.sleep(.1)
                h=History(site/'runs/history.sqlite3')
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
                    page=browser.new_page();errors=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto('http://127.0.0.1:18509')
                    def signin(name,password):
                        page.get_by_label('Username',exact=True).fill(name)
                        page.get_by_label('Password',exact=True).fill(password)
                        page.get_by_role('button',name='Sign in',exact=True).click()
                    page.get_by_text('Sign in to OpsFusion',exact=True).wait_for()
                    assert page.get_by_role('combobox').count()==0
                    signin('fixture-admin','Browser fixture password 123!')
                    page.get_by_text('Analyse real recordings',exact=True).wait_for()
                    page.get_by_role('combobox').first.click()
                    page.get_by_role('option',name='Synthetic incident demo',exact=True).click()
                    expect(page.locator('[data-testid="stMetricValue"]')).to_have_text(['24','23','26','26'])
                    page.get_by_role('tab',name='Downloads',exact=True).click()
                    page.get_by_role('button',name='Save this run to incident history',exact=True).click()
                    page.get_by_text('Run saved.',exact=False).wait_for(state='attached')
                    assert len(h.incidents())==26
                    page.get_by_role('combobox').first.click()
                    page.get_by_role('option',name='Live monitoring & history',exact=True).click()
                    page.get_by_text('Saved incident history',exact=True).wait_for()
                    # Streamlit selectbox exposes the accessible input as a combobox.
                    status=page.locator('[data-testid="stSelectbox"]').filter(has_text='Set review status').get_by_role('combobox')
                    status.click();page.get_by_role('option',name='acknowledged',exact=True).click()
                    page.get_by_label('Review note',exact=True).fill('Synthetic browser review check')
                    page.get_by_role('button',name='Save review',exact=True).click()
                    for _ in range(50):
                        if any(r['review_state']=='acknowledged' for r in h.incidents()):break
                        page.wait_for_timeout(100)
                    reviewed=next(r for r in h.incidents() if r['review_state']=='acknowledged')
                    assert h.audit(reviewed['incident_id'])[0]['actor']=='fixture-admin'
                    assert h.audit(reviewed['incident_id'])[0]['actor_origin']=='authenticated_account'
                    page.get_by_text('Reviewing saved snapshot revision 2.',exact=False).wait_for()
                    page.wait_for_timeout(400)
                    with page.expect_download() as info:
                        page.get_by_role('button',name='Download incident history JSON',exact=True).click()
                    download=info.value;download.save_as(site/'download.json')
                    exported=json.loads((site/'download.json').read_text())
                    assert len(exported['incidents'])==26 and len(exported['audit'])==1
                    page.get_by_role('button',name='Build notification previews and queue',exact=True).click()
                    page.get_by_text('Queued 26 new preview/delivery records.',exact=False).wait_for()
                    with page.expect_download() as info:
                        page.get_by_role('button',name='Download notification delivery records',exact=True).click()
                    info.value.save_as(site/'notifications.json')
                    notifications=json.loads((site/'notifications.json').read_text())
                    assert len(notifications['deliveries'])==26
                    assert all(r['state']=='preview' for r in notifications['deliveries'])
                    page.get_by_role('button',name='Sign out',exact=True).click()
                    page.get_by_text('Sign in to OpsFusion',exact=True).wait_for()
                    signin('fixture-viewer','Browser viewer password 123!')
                    page.get_by_text('Saved incident history',exact=True).wait_for()
                    expect(page.get_by_role('button',name='Start camera worker',exact=True,include_hidden=True)).to_be_disabled()
                    expect(page.get_by_role('button',name='Build notification previews and queue',exact=True)).to_be_disabled()
                    expect(page.get_by_role('button',name='Save review',exact=True)).to_be_disabled()
                    assert page.get_by_text('Admin · notification destination',exact=True).count()==0
                    page.get_by_role('button',name='Sign out',exact=True).click()
                    page.reload()
                    page.get_by_text('Sign in to OpsFusion',exact=True).wait_for()
                    signin('fixture-admin','Browser fixture password 123!')
                    page.get_by_text('Analyse real recordings',exact=True).wait_for()
                    page.get_by_role('combobox').first.click()
                    page.get_by_role('option',name='Live monitoring & history',exact=True).click()
                    page.get_by_text('Saved incident history',exact=True).wait_for()
                    assert any(r['review_state']=='acknowledged' for r in h.incidents())
                    camera_checked=False;camera_frames=0
                    if args.rtsp_video:
                        from tools.validate_rtsp import RTSPFixture
                        fixture=RTSPFixture(args.rtsp_video)
                        connection=page.locator('[data-testid="stSelectbox"]').filter(has_text='Camera connection').get_by_role('combobox')
                        connection.click();page.get_by_role('option',name='RTSP camera',exact=True).click()
                        page.get_by_label('RTSP URL',exact=True).fill(f'rtsp://fixture-user:browser-fixture-secret@127.0.0.1:{fixture.port}/fixture')
                        page.get_by_label('RTSP URL',exact=True).press('Tab');page.wait_for_timeout(400)
                        page.get_by_text('This camera source is generated/composed',exact=True).click()
                        page.wait_for_timeout(400)
                        page.get_by_role('button',name='Start camera worker',exact=True).click()
                        page.get_by_text('Worker started:',exact=False).wait_for()
                        expect(page.get_by_label('RTSP URL',exact=True)).to_have_value('')
                        camera=None
                        for _ in range(100):
                            cameras=[s for s in h.sessions() if s['mode']=='rtsp']
                            camera=cameras[0] if cameras else None
                            if camera and camera['sampled_frames']>=8 and camera['event_count']>0:break
                            page.wait_for_timeout(200)
                        assert camera and camera['sampled_frames']>=8 and camera['event_count']>0,camera
                        camera_frames=camera['sampled_frames']
                        page.get_by_role('button',name='Stop camera',exact=True).click()
                        for _ in range(50):
                            camera=h.session(camera['session_id'])
                            if camera['state']=='stopped':break
                            page.wait_for_timeout(100)
                        assert camera['state']=='stopped',camera
                        camera_reports=[r for r in h.incidents() if r['session_id']==camera['session_id']]
                        assert camera_reports and all(r['current_report']['synthetic'] for r in camera_reports)
                        assert 'browser-fixture-secret' not in json.dumps(h.export())
                        camera_checked=True
                    assert page.locator('[data-testid="stException"]').count()==0
                    assert not errors,errors
                    browser.close()
                result={'application_version':'0.11.0','passed':True,'saved_reports':26,
                        'operator_review_and_audit':True,'downloaded_history_json':True,
                        'browser_reload_retains_history':True,'javascript_errors':0,
                        'actual_signin_signout':True,'authenticated_audit_actor':True,'viewer_actions_disabled':True,
                        'notification_previews':26,'external_notifications_sent':0,
                        'loopback_rtsp_camera_start_stop':camera_checked,'camera_sampled_frames_at_stop_request':camera_frames,
                        'scope':'Authored history fixture; optional actual RTSP/YOLO composed-loopback camera. No hardware or accuracy test.'}
                (root/'reports/step11_browser_validation.json').write_text(json.dumps(result,indent=2)+'\n')
                print(json.dumps(result,indent=2))
            finally:
                for session in History(site/'runs/history.sqlite3').sessions():
                    if session['state'] in ('starting','running','reconnecting','stopping'):
                        History(site/'runs/history.sqlite3').request_stop(session['session_id'])
                if fixture is not None:fixture.close()
                server.terminate();server.wait(timeout=10)


if __name__=='__main__':main()
