"""Browser -> durable queue -> actual worker CLI -> local TLS SMTP, never a real inbox.

Optional validation requires Playwright/Chromium. Public test-only certificate is
trusted explicitly using SSL_CERT_FILE in the disposable child processes only.
"""
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from opsfusion.accounts import Accounts
from opsfusion.history import History
from opsfusion.notify import Outbox
from opsfusion.live import settings
from test_email_delivery import SMTPFixture
from test_live_history import ZONE, event


def main():
    with tempfile.TemporaryDirectory(prefix='opsfusion-email-browser-') as td:
        site=Path(td)
        shutil.copy(ROOT/'dashboard.py',site/'dashboard.py');shutil.copytree(ROOT/'data',site/'data')
        accounts=Accounts(site/'runs/accounts.sqlite3');password='Browser fixture password 123!'
        accounts.bootstrap('fixture-admin',password);token=accounts.login('fixture-admin',password)
        accounts.add_user(token,'fixture-viewer','Browser viewer password 123!','viewer')
        fixture=SMTPFixture(site)
        env=dict(os.environ);env['PYTHONPATH']=str(ROOT)
        env['OPSFUSION_EMAIL_SETTINGS']=json.dumps(fixture.config);env['OPSFUSION_SMTP_PASSWORD']='Public SMTP fixture password'
        env['SSL_CERT_FILE']=str(site/'localhost.pem')
        worker=None;server=None
        with (site/'server.log').open('w') as log, (site/'worker.log').open('w') as worker_log:
            try:
                server=subprocess.Popen([sys.executable,'-m','streamlit','run','dashboard.py','--server.port','18511','--server.address','127.0.0.1'],cwd=site,env=env,stdout=log,stderr=log)
                for _ in range(100):
                    try:urllib.request.urlopen('http://127.0.0.1:18511/_stcore/health',timeout=1);break
                    except Exception:time.sleep(.1)
                history=History(site/'runs/history.sqlite3');box=Outbox(history,accounts)
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
                    page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto('http://127.0.0.1:18511')
                    def signin(username,pw):
                        page.get_by_label('Username',exact=True).fill(username);page.get_by_label('Password',exact=True).fill(pw)
                        page.get_by_role('button',name='Sign in',exact=True).click()
                    signin('fixture-admin',password);page.get_by_text('Analyse real recordings',exact=True).wait_for()
                    page.get_by_role('combobox').first.click();page.get_by_role('option',name='Live monitoring & history',exact=True).click()
                    page.get_by_text('Admin · notification destination',exact=True).click()
                    page.get_by_text('Enable delivery of new alerts to this destination',exact=True).click()
                    page.get_by_role('button',name='Save destination settings',exact=True).click()
                    page.get_by_text('Destination settings saved.',exact=False).wait_for()
                    page.get_by_role('button',name='Queue test email',exact=True).click()
                    page.get_by_text('Test email queued:',exact=False).wait_for()
                    rows=box.rows(token);assert len(rows)==1 and rows[0]['state']=='pending'
                    assert history.incidents()==[] and fixture.received==[]
                    # Add one explicitly non-synthetic authored incident for complete alert formatting.
                    sid=history.start('camera-1','zone-1','webcam',settings(ZONE))
                    history.ingest(sid,[event(synthetic=False)]);history.finish(sid)
                    worker=subprocess.Popen([sys.executable,'-m','opsfusion.notify','work','--username','fixture-admin','--max-seconds','5'],cwd=site,env=env,stdin=subprocess.PIPE,stdout=worker_log,stderr=worker_log,text=True)
                    worker.communicate(password+'\n',timeout=20)
                    assert worker.returncode==0
                    rows=box.rows(token);emails=[r for r in rows if r['channel']=='email']
                    assert len(emails)==2 and all(r['state']=='delivered' and r['status_code']==250 for r in emails),emails
                    assert len(fixture.received)==2 and fixture.auth_tls==[True,True]
                    assert any('TEST' in m['Subject'] for m in fixture.received)
                    assert any('restricted_zone' in m['Subject'] for m in fixture.received)
                    assert all('Public SMTP fixture password' not in m.as_string() for m in fixture.received)
                    page.get_by_role('button',name='Refresh delivery status',exact=True).click()
                    with page.expect_download() as info:
                        page.get_by_role('button',name='Download notification delivery records',exact=True).click()
                    info.value.save_as(site/'deliveries.json')
                    download=(site/'deliveries.json').read_text();assert 'Public SMTP fixture password' not in download
                    assert fixture.config['to'] not in download
                    exported=json.loads(download);assert len([r for r in exported['deliveries'] if r['state']=='delivered'])==2
                    page.reload();page.get_by_text('Sign in to OpsFusion',exact=True).wait_for();signin('fixture-admin',password)
                    page.get_by_text('Analyse real recordings',exact=True).wait_for()
                    page.get_by_role('combobox').first.click();page.get_by_role('option',name='Live monitoring & history',exact=True).click()
                    assert len([r for r in box.rows(token) if r['state']=='delivered'])==2
                    page.get_by_role('button',name='Sign out',exact=True).click();page.get_by_text('Sign in to OpsFusion',exact=True).wait_for()
                    signin('fixture-viewer','Browser viewer password 123!');page.get_by_text('Saved incident history',exact=True).wait_for()
                    assert page.get_by_text('Admin · notification destination',exact=True).count()==0
                    assert page.get_by_role('button',name='Queue test email',exact=True).count()==0
                    expect(page.get_by_role('button',name='Build notification previews and queue',exact=True)).to_be_disabled()
                    assert page.locator('[data-testid="stException"]').count()==0;assert not errors
                    browser.close()
                record={'application_version':'0.11.0','passed':True,'browser_config_and_test_queue':True,
                        'ui_did_not_send':True,'actual_worker_cli':True,'local_tls_smtp_messages':2,
                        'test_email_and_incident_email':True,'smtp_status':250,'download_excludes_credentials_and_recipient':True,
                        'reload_retains_deliveries':True,'viewer_cannot_configure_or_test':True,
                        'javascript_errors':len(errors),'external_messages_sent':0,
                        'scope':'Actual browser and subprocess worker with local TLS SMTP fixture only. No remote inbox/provider or real accuracy measurement.'}
                (ROOT/'reports/step11_email_browser_validation.json').write_text(json.dumps(record,indent=2)+'\n')
                print(json.dumps(record,indent=2))
            finally:
                for child in (worker,server):
                    if child is not None and child.poll() is None:child.terminate();child.wait(timeout=10)
                fixture.close()


if __name__=='__main__':main()
