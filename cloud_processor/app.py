"""Streamlit entrypoint for the card-free, on-demand processing console."""
import os
from pathlib import Path
import sys

# Keep CPU inference within the shared host's resources. Set before ML imports.
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
from opsfusion.on_demand import Runtime, authorized, run_next, settings_for

PORTAL = 'https://opsfusion-ai-bharath.netlify.app/'
st.set_page_config(page_title='OpsFusion processor', page_icon='⚙️', layout='centered')
st.title('OpsFusion processor')
st.caption('Run one queued recording job on demand. Reports and evidence return to the cloud portal.')

try:
    settings = settings_for(st.secrets)
except Exception:
    st.info('The owner must configure the processor secrets in Streamlit app settings before this console can connect.')
    st.link_button('Open OpsFusion', PORTAL)
    st.stop()

if st.session_state.get('_processor_auth') != settings.session_stamp:
    st.info('Administrator access is required to start analysis. Use the processor access code, which is separate from your Google or local account password.')
    with st.form('processor_unlock', clear_on_submit=True):
        code = st.text_input('Processor access code', type='password', max_chars=128)
        submitted = st.form_submit_button('Unlock processor')
    if submitted:
        if authorized(code, settings):
            st.session_state['_processor_auth'] = settings.session_stamp
            st.rerun()
        else:
            st.error('The processor access code is incorrect.')
    st.stop()


@st.cache_resource
def runtime():
    return Runtime()


st.success('Processor unlocked for this session')
st.link_button('Open recordings and reports', PORTAL)
st.write('Upload a recording in OpsFusion, then click below to process the next queued job. Each click handles at most one job across this shared site.')
st.info('Free demo limits: 20 seconds of video at 1–5 FPS; video 20 MiB; CSV 1 MiB each; WAV 5 MiB each; 40 MiB total. Keep this page open while the job runs.')
st.caption('This is an on-demand console. It does not poll while idle or provide continuous monitoring. If the free host sleeps, reopen and wake the app before processing.')

if st.button('Process next queued job', type='primary'):
    try:
        with st.spinner('Processing one job. Follow its progress in the OpsFusion portal…'):
            outcome = run_next(settings, st.session_state.get('_processor_auth'), runtime())
        st.session_state['_processor_outcome'] = outcome
    except Exception:
        st.session_state['_processor_outcome'] = {'status': 'connection_error',
            'message': 'The processor could not reach the cloud API. Check the private host settings and try again; an interrupted job can be reclaimed after its lease expires.'}

outcome = st.session_state.get('_processor_outcome')
if outcome:
    if outcome['status'] == 'succeeded':
        st.success(f"Completed job {outcome['job_id']}: {outcome['observations']} observations, {outcome['incidents']} incidents. Open OpsFusion to review the saved evidence.")
    elif outcome['status'] in ('failed', 'connection_error'):
        st.error(outcome['message'])
    else:
        st.info(outcome['message'])

def lock_processor():
    for name in ('_processor_auth', '_processor_outcome'):
        st.session_state.pop(name, None)


st.button('Lock processor', on_click=lock_processor)
