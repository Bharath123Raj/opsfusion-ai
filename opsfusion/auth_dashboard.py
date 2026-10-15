"""Streamlit login and fresh permission checks, including fragment reruns."""
from pathlib import Path
import streamlit as st
from .accounts import Accounts, PERMISSIONS
from .access import settings as access_settings


def account_store(root):return Accounts(Path(root)/'runs/accounts.sqlite3')


def authorize(root,action):
    return account_store(root).require(st.session_state.get('security_token'),action)


def can(root,action):
    try:authorize(root,action);return True
    except PermissionError:return False


def require_login(root):
    accounts=account_store(root)
    access=access_settings()
    if access is not None:
        token=st.context.cookies.get(access.cookie)
        if token!=st.session_state.get('security_token'):
            st.session_state.clear()
            if token:st.session_state.security_token=token
    try:who=accounts.identity(st.session_state.get('security_token'))
    except PermissionError:who=None
    if who is None:
        if access is not None:
            st.session_state.clear()
            st.info('Your session ended. Sign in to continue.')
            st.markdown('<a href="/access/login" target="_self">Sign in to OpsFusion</a>',unsafe_allow_html=True)
            st.stop()
        # Never leave a previous user's replay/media/credential state after expiry.
        if any(key in st.session_state for key in ('security_token','bundle','history_snapshot')):
            st.session_state.clear()
            st.rerun()
        st.subheader('Sign in to OpsFusion')
        if not accounts.initialized():
            st.info('Create the first admin in PowerShell before signing in.')
            st.code('conda run --no-capture-output -n opsfusion312 python -m opsfusion.accounts bootstrap --username bharath',language='powershell')
            st.stop()
        with st.form('signin_form'):
            name=st.text_input('Username',key='signin_username',max_chars=64)
            password=st.text_input('Password',type='password',key='signin_password',max_chars=256)
            submitted=st.form_submit_button('Sign in')
        if submitted:
            try:
                token=accounts.login(name,password)
                st.session_state.security_token=token
                del st.session_state.signin_password
                st.rerun()
            except PermissionError:st.error('Sign-in failed. Check your credentials or retry in five minutes.')
        st.stop()
    with st.sidebar:
        st.caption(f"Signed in: {who['username']} · {who['role']}")
        if access is not None:
            st.markdown('<a href="/access/logout" target="_self">Sign out</a>',unsafe_allow_html=True)
        elif st.button('Sign out',key='signout'):
            accounts.logout(st.session_state.security_token)
            st.session_state.clear();st.rerun()
        with st.expander('Change my password'):
            with st.form('password_change'):
                old=st.text_input('Current password',type='password',max_chars=256)
                new=st.text_input('New password (12+ characters)',type='password',max_chars=256)
                confirm=st.text_input('Confirm new password',type='password',max_chars=256)
                changed=st.form_submit_button('Change password and sign out')
            if changed:
                try:
                    if new!=confirm:raise ValueError('Passwords do not match')
                    accounts.change_password(st.session_state.security_token,old,new)
                    st.session_state.clear();st.rerun()
                except (ValueError,PermissionError) as exc:st.error(str(exc))
    return who
