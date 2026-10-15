"""Authenticated UI fixture in a disposable folder; never bootstrap the user's DB."""
from pathlib import Path
import shutil
import tempfile
from streamlit.testing.v1 import AppTest
from opsfusion.accounts import Accounts


def authenticated_app(testcase,root,role='admin'):
    temporary=tempfile.TemporaryDirectory(prefix='opsfusion-ui-');testcase.addCleanup(temporary.cleanup)
    folder=Path(temporary.name);shutil.copy(root/'dashboard.py',folder/'dashboard.py')
    shutil.copytree(root/'data',folder/'data')
    accounts=Accounts(folder/'runs/accounts.sqlite3')
    accounts.bootstrap('fixture-admin','Fixture password 123!')
    if role!='admin':
        token=accounts.login('fixture-admin','Fixture password 123!')
        accounts.add_user(token,'fixture-user','Fixture user password 123!',role);accounts.logout(token)
        token=accounts.login('fixture-user','Fixture user password 123!')
    else:token=accounts.login('fixture-admin','Fixture password 123!')
    app=AppTest.from_file(str(folder/'dashboard.py'),default_timeout=30)
    app.session_state.security_token=token
    return app.run()
