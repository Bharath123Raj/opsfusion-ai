"""Actual TLS gateway, media access, process restart and portable backup restore.

Requires Playwright Chromium and an explicitly supplied Caddy binary. Uses a
disposable internal test CA; certificate checks are disabled only in this tool.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from opsfusion.accounts import Accounts
from opsfusion.core import Event
from opsfusion.history import History,utc_now
from opsfusion.live import settings
from opsfusion.maintenance import backup,restore
from opsfusion.live_dashboard import evidence_path


def main(caddy):
    import cv2
    import numpy as np
    with tempfile.TemporaryDirectory(prefix='opsfusion-deploy-browser-') as td:
        work=Path(td);site=work/'data';site.mkdir();origin='https://localhost:18810'
        accounts=Accounts(site/'runs/accounts.sqlite3');pw='Deployment browser fixture password 123!'
        accounts.bootstrap('fixture-admin',pw);admin=accounts.login('fixture-admin',pw)
        accounts.add_user(admin,'fixture-viewer','Deployment viewer fixture password 123!','viewer')
        h=History(site/'runs/history.sqlite3');zone={'asset_id':'test-asset','camera_id':'fixture-camera','polygon':[[.5,.1],[.9,.1],[.9,.95],[.5,.95]],'confirm_samples':2,'max_age_seconds':1,'min_iou':.15}
        sid=h.start(zone['camera_id'],zone['asset_id'],'webcam',settings(zone,synthetic=True))
        image=site/'runs/live'/sid/'evidence/camera.jpg';image.parent.mkdir(parents=True)
        frame=np.zeros((240,480,3),dtype=np.uint8);cv2.putText(frame,'SYNTHETIC DEPLOYMENT FIXTURE',(10,125),cv2.FONT_HERSHEY_SIMPLEX,.7,(255,255,255),2);cv2.imwrite(str(image),frame)
        h.ingest(sid,[Event('gateway-camera',utc_now(),zone['asset_id'],'video','zone_entry',zone['camera_id'],image.as_uri(),'Synthetic snapshot for access/restore check',1.,True)])
        h.finish(sid);iid=h.incidents()[0]['incident_id']
        env=dict(os.environ);env.update(PYTHONPATH=str(ROOT),OPSFUSION_ACCESS_MODE='https',OPSFUSION_PUBLIC_ORIGIN=origin,XDG_DATA_HOME=str(work/'caddy-data'),XDG_CONFIG_HOME=str(work/'caddy-config'))
        config=(ROOT/'deploy/Caddyfile.https').read_text().replace('\t','    ').replace('    admin off','    admin off\n    skip_install_trust\n    auto_https disable_redirects').replace('{$OPSFUSION_DOMAIN}','https://localhost:18810').replace('    header {','    bind 127.0.0.1\n    tls internal\n    header {').replace('app:8000','127.0.0.1:18812').replace('app:8504','127.0.0.1:18814')
        assert 'skip_install_trust' in config and 'tls internal' in config
        cfg=work/'Caddyfile';cfg.write_text(config)
        children=[];errors=[];service=None
        def start_service(root,log):
            p=subprocess.Popen([sys.executable,'-m','opsfusion.service','--data-root',str(root),'--api-port','18812','--dashboard-port','18814'],cwd=ROOT,env=env,stdout=log,stderr=log);children.append(p);return p
        def ready(root,at_least=0):
            for _ in range(300):
                try:
                    state=json.loads((root/'runs/service-state.json').read_text())
                    if state['state']=='running' and state['restart_count']>=at_least:return state
                except (OSError,ValueError,KeyError):pass
                time.sleep(.1)
            raise RuntimeError('Supervised services did not become ready')
        with (work/'service.log').open('w') as logs,(work/'gateway.log').open('w') as gatewaylog:
            try:
                service=start_service(site,logs);ready(site)
                gate=subprocess.Popen([str(caddy),'run','--config',str(cfg),'--adapter','caddyfile'],env=env,stdout=gatewaylog,stderr=gatewaylog);children.append(gate)
                with httpx.Client(base_url=origin,verify=False,trust_env=False,follow_redirects=False,timeout=5) as anonymous:
                    for _ in range(100):
                        try:
                            if anonymous.get('/access/login').status_code==200:break
                        except httpx.HTTPError:pass
                        time.sleep(.1)
                    assert anonymous.get('/').status_code==303
                    assert anonymous.get('/media/unknown.jpg').status_code==401
                    assert anonymous.get('/_stcore/stream',headers={'Connection':'Upgrade','Upgrade':'websocket','Sec-WebSocket-Version':'13','Sec-WebSocket-Key':'dGhlIHNhbXBsZSBub25jZQ=='}).status_code==401
                    with sync_playwright() as p:
                        browser=p.chromium.launch(headless=True,args=['--no-sandbox']);context=browser.new_context(ignore_https_errors=True);page=context.new_page()
                        page.on('pageerror',lambda e:errors.append(str(e)))
                        def signin(name,password):
                            page.goto(origin+'/access/login');page.get_by_label('Username',exact=True).fill(name);page.get_by_label('Password',exact=True).fill(password)
                            page.get_by_role('button',name='Sign in',exact=True).click()
                            try:
                                page.get_by_text('Analyse real recordings',exact=True).wait_for(timeout=20000) if name=='fixture-admin' else page.get_by_text('Saved incident history',exact=True).wait_for(timeout=20000)
                            except Exception:
                                print('Browser text:',page.locator('body').inner_text()[:1600])
                                print('Cookie metadata:',[{k:c[k] for k in ('name','secure','httpOnly','domain','path')} for c in context.cookies()])
                                raise
                        def live():
                            page.get_by_role('combobox').first.click();page.get_by_role('option',name='Live monitoring & history',exact=True).click()
                            page.get_by_text('Saved incident history',exact=True).wait_for()
                        signin('fixture-admin',pw);live()
                        cookie=next(c for c in context.cookies() if c['name']=='__Host-opsfusion_session')
                        assert cookie['secure'] and cookie['httpOnly']
                        img=page.locator('[data-testid="stImage"] img').last;img.wait_for();media=img.get_attribute('src')
                        if media.startswith('/'):media=origin+media
                        assert anonymous.get(media).status_code==401
                        assert context.request.get(media).status==200
                        with page.expect_download() as info:page.get_by_role('button',name='Download incident history JSON',exact=True).click()
                        info.value.save_as(work/'history.json');download_url=info.value.url
                        assert anonymous.get(download_url).status_code==401
                        saved=json.loads((work/'history.json').read_text());assert saved['incidents'][0]['incident_id']==iid
                        assert pw not in json.dumps(saved) and cookie['value'] not in json.dumps(saved)
                        state_select=page.locator('[data-testid="stSelectbox"]').filter(has_text='Set review status').get_by_role('combobox')
                        state_select.click();page.get_by_role('option',name='acknowledged',exact=True).click();page.get_by_role('button',name='Save review',exact=True).click()
                        page.wait_for_timeout(700);assert h.incidents()[0]['review_state']=='acknowledged'
                        before=ready(site);os.kill(before['api_pid'],signal.SIGTERM);after=ready(site,1)
                        assert before['api_pid']!=after['api_pid'] and before['dashboard_pid']!=after['dashboard_pid']
                        page.goto(origin);page.get_by_text('Analyse real recordings',exact=True).wait_for(timeout=20000);live()
                        assert h.incidents()[0]['review_state']=='acknowledged'
                        assert context.request.get(media).status==200
                        # Revocation applies to media HTTP requests immediately, even with a remembered URL.
                        accounts.logout(cookie['value']);assert context.request.get(media).status==401
                        signin('fixture-viewer','Deployment viewer fixture password 123!')
                        expect(page.get_by_role('button',name='Save review',exact=True)).to_be_disabled()
                        page.get_by_role('link',name='Sign out',exact=True).click();page.get_by_role('button',name='Sign out',exact=True).click()
                        assert page.url.endswith('/access/login')
                        assert context.request.get(media).status==401
                        assert not errors
                        service.terminate();service.wait(timeout=20);assert service.returncode==0
                        b=backup(site,work/'backups',offline=True);restored=work/'restored';restore(b['archive'],restored,offline=True)
                        restored_h=History(restored/'runs/history.sqlite3');assert restored_h.incidents()[0]['first_report']==h.incidents()[0]['first_report']
                        assert evidence_path(restored,image.as_uri())==restored/image.relative_to(site)
                        service=start_service(restored,logs);ready(restored)
                        signin('fixture-admin',pw);live()
                        page.locator('[data-testid="stImage"] img').last.wait_for()
                        assert restored_h.incidents()[0]['review_state']=='acknowledged'
                        assert page.locator('[data-testid="stException"]').count()==0 and not errors
                        browser.close()
                version=subprocess.check_output([str(caddy),'version'],text=True).split()[0]
                proof={'application_version':'0.15.0','passed':True,'actual_tls_gateway':True,'caddy_version':version,
                       'secure_httponly_cookie':True,'single_browser_login':True,'anonymous_media_denied':True,'anonymous_websocket_denied':True,
                       'authenticated_snapshot_and_download':True,'anonymous_download_denied':True,'revoked_cookie_blocks_media':True,
                       'viewer_review_disabled':True,'supervisor_restarted_both_children_after_api_failure':True,
                       'review_survived_restart':True,'offline_backup_restore_to_new_data_root':True,'old_auth_sessions_removed':True,
                       'restored_original_report_and_portable_image_reference':True,'restored_site_browser_login_and_snapshot':True,
                       'javascript_errors':len(errors),'docker_image_built':False,'public_deployment_performed':False,
                       'scope':'Disposable local HTTPS gateway with internal fixture CA; only this test client ignores its test certificate. Synthetic snapshot and real account/review/persistence paths. Docker engine unavailable; container build and public domain deployment remain unverified. No real-data accuracy claim.'}
                (ROOT/'reports/step15_deployment_browser_validation.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))
            except Exception:
                print((work/'service.log').read_text()[-5000:]);print((work/'gateway.log').read_text()[-5000:]);raise
            finally:
                for child in reversed(children):
                    if child.poll() is None:child.terminate();child.wait(timeout=20)


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--caddy',type=Path,required=True);args=a.parse_args();main(args.caddy.resolve())
