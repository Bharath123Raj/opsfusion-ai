import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { configured, supabase } from './client'
import { canonicalName, recordingSettings, resultPath, slots, workerOnline } from './contract'
import './style.css'

const defaults = { asset_id: '', camera_id: 'camera-01', polygon: '', video_start: '', audio_start: '',
  audio_train_start: '', audio_validation_start: '', max_video_seconds: 20, sample_fps: 5,
  normal_periods_reviewed: false, video_synthetic: false, audio_synthetic: false, audio_baseline_synthetic: false }
const title = value => String(value ?? '').replaceAll('_', ' ')
const date = value => new Date(value).toLocaleString()
const fail = result => { if (result.error) throw result.error; return result.data }

function Brand() { return <div className="brand"><span className="brand-icon">O<span>F</span></span><span>OpsFusion<small>OPERATIONS INTELLIGENCE</small></span></div> }
function Notice({ children, error = false }) { return <div className={`notice ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div> }

function SignIn() {
  const [email, setEmail] = useState(''), [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('')
    try { fail(await supabase.auth.signInWithPassword({ email, password })); setPassword('') }
    catch { setError('Sign-in failed. Check your account details or contact your administrator.') }
    finally { setBusy(false) }
  }
  async function google() {
    setBusy(true); setError('')
    try { fail(await supabase.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin, queryParams: { prompt: 'select_account' } } })) }
    catch { setError('Google sign-in could not start. Contact your administrator.'); setBusy(false) }
  }
  return <div className="login-shell"><aside className="login-story"><Brand/><div><p className="eyebrow">EVIDENCE BEFORE ASSUMPTIONS</p><h1>One workspace.<br/>A clearer operational picture.</h1><p>Bring recordings, machine measurements and controller events together for incident review.</p></div><small>Human review. Traceable evidence. Unconfirmed causes.</small></aside>
    <main className="login-main"><div className="login-card"><p className="eyebrow">YOUR INCIDENT WORKSPACE</p><h2>Welcome back</h2><p className="muted">Sign in with your organization account.</p>
      {!configured ? <Notice error>This workspace is not configured. Contact your administrator.</Notice> : <form onSubmit={submit}>
        <label>Email address<input type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)}/></label>
        <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)}/></label>
        {error && <Notice error>{error}</Notice>}<button className="primary wide" disabled={busy}>{busy ? 'Signing in…' : 'Sign in →'}</button>
        <button type="button" className="secondary wide" disabled={busy} onClick={google}>Continue with Google</button>
      </form>}<p className="fine">Access is managed by your site administrator. Signing in does not automatically grant workspace access.</p>
    </div></main></div>
}

function ZoneEditor({ file, value, onChange }) {
  const video = useRef(null), [url, setUrl] = useState(''), [broken, setBroken] = useState(false), [duration, setDuration] = useState(0)
  useEffect(() => { const next = URL.createObjectURL(file); setUrl(next); setBroken(false); return () => URL.revokeObjectURL(next) }, [file])
  let points = []
  try { const parsed = JSON.parse(value); if (Array.isArray(parsed) && parsed.every(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite))) points = parsed } catch { /* Text field remains editable. */ }
  function add(event) {
    if (points.length >= 30) return
    const bounds = event.currentTarget.getBoundingClientRect()
    onChange(JSON.stringify([...points, [Number(((event.clientX - bounds.left) / bounds.width).toFixed(4)), Number(((event.clientY - bounds.top) / bounds.height).toFixed(4))]]))
  }
  return <div className="zone-editor"><div className="section-line"><strong>Define the restricted ground zone</strong><button type="button" className="text-button" onClick={() => onChange('')}>Clear points</button></div>
    {!broken && <><div className="zone-stage"><video ref={video} src={url} muted playsInline preload="auto" onError={() => setBroken(true)} onLoadedMetadata={() => setDuration(video.current.duration || 0)} />
      <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" onClick={add} role="img" aria-label="Click the recording preview to add zone points">
        {points.length > 1 && <polygon points={points.map(p => `${p[0] * 1000},${p[1] * 1000}`).join(' ')} fill="#32d3ad40" stroke="#2ce7b9" strokeWidth="5"/>}
        {points.map((p, i) => <circle key={i} cx={p[0] * 1000} cy={p[1] * 1000} r="9" fill="#fff"/>)}
      </svg></div><label className="fine">Preview position<input type="range" min="0" max={Math.max(duration - 0.01, 0)} step="0.1" defaultValue="0" onChange={e => { if (video.current) video.current.currentTime = Number(e.target.value) }}/></label></>}
    <p className="fine">{broken ? 'This browser could not preview the clip. Enter normalized coordinates below.' : 'Click at least three corners on the preview, or enter normalized [x,y] coordinates below. The worker validates polygon geometry.'}</p>
    <label>Zone polygon<textarea rows="3" value={value} onChange={e => onChange(e.target.value)} placeholder="[[0.5,0.1],[0.9,0.1],[0.9,0.95],[0.5,0.95]]"/></label>
  </div>
}

function NewRecording({ session, online, onSubmitted }) {
  const [files, setFiles] = useState({}), [values, setValues] = useState(defaults)
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('')
  const update = (name, value) => setValues(previous => ({ ...previous, [name]: value }))
  async function submit(event) {
    event.preventDefault(); setBusy(true); setMessage(''); setError('')
    try {
      const settings = recordingSettings(values, files), id = crypto.randomUUID(), metadata = {}
      for (const [slot, file] of Object.entries(files)) {
        setMessage(`Checking ${slot.replaceAll('_', ' ')}…`)
        const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
        metadata[slot] = { name: canonicalName(slot, file), bytes: file.size, sha256: [...new Uint8Array(hash)].map(x => x.toString(16).padStart(2, '0')).join('') }
      }
      fail(await supabase.from('opsfusion_jobs').insert({ id, asset_id: values.asset_id.trim(), camera_id: values.camera_id.trim(), input: { version: 1, files: metadata, settings } }))
      for (const [slot, file] of Object.entries(files)) {
        setMessage(`Uploading ${slot.replaceAll('_', ' ')}…`)
        fail(await supabase.storage.from('opsfusion-inputs').upload(`${session.user.id}/${id}/${metadata[slot].name}`, file, { upsert: false, cacheControl: '0', contentType: file.type || 'application/octet-stream' }))
      }
      const rows = fail(await supabase.from('opsfusion_jobs').update({ status: 'queued' }).eq('id', id).select('id'))
      if (rows.length !== 1) throw new Error('The uploaded job could not be queued. Refresh the job list before trying again.')
      setMessage('Recordings uploaded and queued.'); onSubmitted(id)
    } catch (err) { setError(err.message || 'Upload failed. Review the job list before trying again.') }
    finally { setBusy(false) }
  }
  const field = (name, label, placeholder = '') => <label key={name}>{label}<input value={values[name]} onChange={e => update(name, e.target.value)} placeholder={placeholder}/></label>
  const check = (name, label) => <label className="check" key={name}><input type="checkbox" checked={values[name]} onChange={e => update(name, e.target.checked)}/><span>{label}</span></label>
  return <form className="recording-form" onSubmit={submit}><div className="panel-heading"><div><p className="eyebrow">NEW ANALYSIS</p><h2>Bring the evidence together</h2><p className="muted">Use aligned asset IDs and actual recording times.</p></div><span className="pill">Uploaded recordings</span></div>
    {!online && <Notice>No processing worker has checked in recently. You can upload a job; it will remain queued until a worker connects.</Notice>}
    <div className="form-grid">{field('asset_id', 'Asset ID', 'For example: motor-01')}{field('camera_id', 'Camera ID')}</div>
    <div className="source-grid">{['Video', 'Sensors', 'Audio', 'Logs'].map(group => <section className="source-card" key={group}><h3><span className="source-dot"/>{group}</h3>{slots.filter(s => s.group === group).map(slot => <label className="file-label" key={slot.id}>{slot.label}<input type="file" accept={slot.accept} disabled={busy} onChange={event => setFiles(previous => { const next = { ...previous }; if (event.target.files[0]) next[slot.id] = event.target.files[0]; else delete next[slot.id]; return next })}/><small>Up to {slot.limit} MiB{group === 'Audio' ? ' · mono 16-kHz WAV' : ''}</small></label>)}</section>)}</div>
    {files.video && <section className="form-section"><h3>Video context</h3><div className="form-grid three">{field('video_start', 'Recording start (with timezone)', '2026-10-15T10:00:00Z')}{field('max_video_seconds', 'Analyse seconds (up to 60)')}{field('sample_fps', 'Sample FPS (1–10)')}</div><ZoneEditor file={files.video} value={values.polygon} onChange={value => update('polygon', value)}/>{check('video_synthetic', 'This is a staged or synthetic video recording')}</section>}
    {files.audio && <section className="form-section"><h3>Audio recording clocks</h3><div className="form-grid three">{field('audio_start', 'Capture start', 'ISO timestamp + timezone')}{field('audio_train_start', 'Normal training start', 'ISO timestamp + timezone')}{field('audio_validation_start', 'Normal calibration start', 'ISO timestamp + timezone')}</div>{check('audio_synthetic', 'The capture audio is synthetic')}{check('audio_baseline_synthetic', 'The normal baseline audio is synthetic')}</section>}
    {(files.sensor || files.audio) && <section className="form-section">{check('normal_periods_reviewed', 'I reviewed the training and calibration periods and confirm that they represent known normal operation.')}</section>}
    <p className="fine">CSV asset IDs and event timestamps are preserved. Sources are not automatically synchronized. Synthetic flags stay attached to generated reports.</p>
    {error && <Notice error>{error}</Notice>}{message && <Notice>{message}</Notice>}
    <div className="form-footer"><span className="fine">Causes remain unconfirmed. No machine actions execute.</span><button className="primary" disabled={busy}>{busy ? 'Preparing job…' : 'Upload & queue analysis →'}</button></div>
  </form>
}

function JobDetail({ job, incidents, reviews, canReview, refresh }) {
  const [error, setError] = useState(''), [saving, setSaving] = useState(false), [scope, setScope] = useState('current_report')
  async function download(artifact) {
    setError('')
    try {
      resultPath(job.id, artifact.path)
      const blob = fail(await supabase.storage.from('opsfusion-results').download(artifact.path))
      const url = URL.createObjectURL(blob), link = document.createElement('a')
      link.href = url; link.download = artifact.name.split('/').pop(); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch { setError('This private file could not be downloaded. Refresh your session and try again.') }
  }
  async function review(event, incident) {
    event.preventDefault(); setSaving(true); setError('')
    const form = new FormData(event.target)
    try {
      fail(await supabase.from('opsfusion_reviews').insert({ job_id: job.id, incident_id: incident.incident_id, status: form.get('status'), note: form.get('note') }))
      await refresh()
    } catch { setError('Review could not be saved. Check your workspace permissions.') }
    finally { setSaving(false) }
  }
  return <section className="panel job-detail"><div className="section-line"><div><p className="eyebrow">ANALYSIS DETAILS</p><h2>{job.asset_id}</h2></div><span className={`status ${job.status}`}>{title(job.status)}</span></div><p className="fine">Job {job.id} · submitted {date(job.created_at)}</p>
    {job.status !== 'succeeded' && <Notice error={job.status === 'failed'}>{job.error || job.progress || (job.status === 'draft' ? 'Upload was not submitted. Drafts are not processed.' : 'Waiting for a processing worker.')}</Notice>}
    {error && <Notice error>{error}</Notice>}
    {job.result && <><div className="detail-stats"><strong>{job.result.observations} <span>observations</span></strong><strong>{job.result.incidents} <span>incidents</span></strong><span className="pill">Human review required</span></div>
      <div className="download-row">{(job.result.artifacts || []).filter(a => ['review.zip', 'source-evidence.zip', 'incidents.json', 'explanations.json', 'video/annotated.mp4'].includes(a.name)).map(a => <button key={a.path} className="secondary" onClick={() => download(a)}>↓ {a.name}</button>)}</div></>}
    {incidents.length > 0 && <div className="section-line"><h3>Incident reports</h3><label className="inline-label">Report scope<select value={scope} onChange={e => setScope(e.target.value)}><option value="current_report">Final context</option><option value="first_report">Frozen first alert</option></select></label></div>}
    {incidents.map(incident => { const report = incident[scope], audit = reviews.filter(r => r.incident_id === incident.incident_id); return <article key={incident.incident_id} className="incident-card"><div className="section-line"><strong>{title(report.type || report.kind || 'Incident')}</strong><span className="pill">{audit[0] ? title(audit[0].status) : 'New'}</span></div><p className="fine">{incident.incident_id}{report.synthetic ? ' · Contains synthetic evidence' : ''}</p>
      <p><strong>Risk:</strong> {title(report.risk)} · {date(report.start)} — {date(report.end)}</p>
      <ul>{(report.observed_facts || []).map(fact => <li key={fact.event_id}>{fact.summary}</li>)}</ul>
      <p><strong>Possible explanation:</strong> {report.hypothesis?.statement || 'No cause established.'} <span className="muted">(unconfirmed)</span></p>
      <p><strong>Recommended action:</strong> {report.recommended_action || 'Review the source evidence.'}</p>
      <details><summary>Evidence-backed report</summary><pre>{JSON.stringify(report, null, 2)}</pre></details>
      {(report.timeline || report.observed_facts || []).map(event => { const paths = job.result?.evidence?.[event.event_id]; const media = paths?.media || paths?.record; const artifact = job.result?.artifacts?.find(a => a.path === media); return artifact ? <button key={event.event_id} className="text-button evidence-button" onClick={() => download(artifact)}>↓ Evidence · {event.event_id}</button> : null })}
      {canReview && <form key={audit[0]?.id || 'unreviewed'} className="review-form" onSubmit={event => review(event, incident)}><label>Review decision<select name="status" defaultValue={audit[0]?.status || 'acknowledged'}><option value="new">New</option><option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option><option value="false_alarm">False alarm</option></select></label><label>Review note<input name="note" maxLength="2000" placeholder="Record what you checked"/></label><button className="secondary" disabled={saving}>Save review</button></form>}
      {audit.length > 0 && <details className="audit"><summary>Saved review history ({audit.length})</summary>{audit.map(row => <p key={row.id}><strong>{title(row.status)}</strong> · {date(row.created_at)}<br/>{row.note || 'No note'}<br/><small>Actor {row.actor}</small></p>)}</details>}
    </article> })}
    {job.status === 'succeeded' && incidents.length === 0 && <p className="empty-note">Analysis completed without fused incidents. Download the review bundle to inspect the observations.</p>}
  </section>
}

function Workspace({ session }) {
  const [role, setRole] = useState(undefined), [tab, setTab] = useState('overview'), [jobs, setJobs] = useState([])
  const [workers, setWorkers] = useState([]), [selected, setSelected] = useState(''), [incidents, setIncidents] = useState([]), [reviews, setReviews] = useState([])
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const online = workerOnline(workers), canReview = ['admin', 'operator'].includes(role)
  const job = jobs.find(j => j.id === selected)
  async function signOut() {
    try { fail(await supabase.auth.signOut({ scope: 'local' })) }
    catch { setError('Could not sign out of this browser. Please try again.') }
  }
  async function refresh() {
    setBusy(true)
    try {
      const user = fail(await supabase.auth.getUser()).user
      if (!user || user.id !== session.user.id) throw new Error('Your session changed. Sign in again.')
      const membership = fail(await supabase.from('opsfusion_members').select('role').eq('user_id', user.id).maybeSingle())
      setRole(membership?.role || null)
      if (!membership) { setJobs([]); setWorkers([]); setIncidents([]); setReviews([]); return }
      const [nextJobs, nextWorkers] = await Promise.all([
        supabase.from('opsfusion_jobs').select('*').order('created_at', { ascending: false }).limit(100),
        supabase.from('opsfusion_workers').select('*').order('last_seen', { ascending: false }).limit(10),
      ])
      setJobs(fail(nextJobs)); setWorkers(fail(nextWorkers)); setError('')
    } catch (err) { setError(err.message || 'Workspace could not refresh.'); setJobs([]); setWorkers([]); setIncidents([]); setReviews([]); setRole(undefined) }
    finally { setBusy(false) }
  }
  async function refreshDetail() {
    if (!selected || !role) return
    const [items, audit] = await Promise.all([
      supabase.from('opsfusion_incidents').select('*').eq('job_id', selected),
      supabase.from('opsfusion_reviews').select('*').eq('job_id', selected).order('id', { ascending: false }).limit(1000),
    ])
    setIncidents(fail(items)); setReviews(fail(audit))
  }
  useEffect(() => { refresh(); const timer = setInterval(refresh, 10000); return () => clearInterval(timer) }, [session.user.id])
  useEffect(() => { setIncidents([]); setReviews([]); refreshDetail().catch(() => setError('Incident details could not refresh.')); const timer = setInterval(() => refreshDetail().catch(() => { setIncidents([]); setReviews([]) }), 10000); return () => clearInterval(timer) }, [selected, role])
  return <div className="app-shell"><aside className="sidebar"><Brand/><p className="nav-label">WORKSPACE</p><nav>{[['overview', '◫', 'Overview'], ['recordings', '↥', 'New analysis'], ['incidents', '≡', 'Incident review']].map(([id, icon, label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)} disabled={id === 'recordings' && !canReview} title={id === 'recordings' && !canReview ? 'New analysis requires admin or operator access.' : undefined}><span>{icon}</span>{label}</button>)}</nav><div className="sidebar-bottom"><p>Connected account</p><strong>{session.user.email}</strong><span className="role">{role || 'Access pending'}</span><button onClick={signOut}>Sign out ↗</button></div></aside>
    <main className="workspace"><header><div><p className="eyebrow">OPERATIONS / {tab === 'recordings' ? 'NEW ANALYSIS' : tab === 'incidents' ? 'INCIDENT REVIEW' : 'OVERVIEW'}</p><h1>{tab === 'recordings' ? 'Analyse recordings' : tab === 'incidents' ? 'Incident review' : 'Operational overview'}</h1></div><button className="secondary" disabled={busy} onClick={refresh}>{busy ? 'Refreshing…' : '↻ Refresh'}</button></header>
      {error && <Notice error>{error}</Notice>}
      {role === undefined ? <section className="panel empty-state"><h2>Checking workspace access</h2><p>Loading your account permissions…</p></section> : role === null ? <section className="panel empty-state"><h2>Your account is signed in</h2><p>No workspace membership is assigned to <strong>{session.user.email}</strong>. New analysis requires an admin or operator role. Use your approved account, or ask your administrator to grant access.</p><div className="download-row"><button className="secondary" onClick={signOut}>Use another account</button><button className="secondary" onClick={refresh}>Check access again</button></div></section> : <>
      <div className="workspace-strip"><span><i className={online ? 'online' : 'offline'}/>{online ? 'Processing worker connected' : 'No recent worker heartbeat'}</span><span>Private site · {role} access</span></div>
      {!canReview && <Notice>Your viewer role can read reports. New analysis and review changes require admin or operator access.</Notice>}
      {tab === 'recordings' && canReview ? <section className="panel"><NewRecording session={session} online={online} onSubmitted={id => { setSelected(id); setTab('incidents'); refresh() }}/></section> : <>
        {tab === 'overview' && <><div className="stat-grid"><div className="stat"><span>Saved analysis jobs</span><strong>{jobs.length}</strong><small>Most recent 100 jobs</small></div><div className="stat"><span>Queued / processing</span><strong>{jobs.filter(j => ['queued', 'running'].includes(j.status)).length}</strong><small>{online ? 'Worker is available' : 'Waiting for a processing host'}</small></div><div className="stat"><span>Completed analyses</span><strong>{jobs.filter(j => j.status === 'succeeded').length}</strong><small>Open a job to review its evidence</small></div></div><div className="overview-note"><span>01</span><div><strong>Observations become context. People make the call.</strong><p>Correlate aligned recordings and measurements, then check the source evidence before resolving an incident.</p></div></div></>}
        <section className="panel"><div className="section-line"><div><p className="eyebrow">RECENT ACTIVITY</p><h2>Recording analyses</h2></div>{canReview && <button className="primary" onClick={() => setTab('recordings')}>+ New analysis</button>}</div>
          {!jobs.length ? <div className="empty-state"><span className="empty-symbol">◫</span><h3>No analyses yet</h3><p>Upload a recording or measurement file to start building your incident history.</p></div> : <div className="table-wrap"><table><thead><tr><th>Asset / job</th><th>Submitted</th><th>Status</th><th>Incidents</th><th/></tr></thead><tbody>{jobs.map(item => <tr key={item.id} className={selected === item.id ? 'selected' : ''}><td><strong>{item.asset_id}</strong><small>{item.id.slice(0, 8)}</small></td><td>{date(item.created_at)}</td><td><span className={`status ${item.status}`}>{title(item.status)}</span></td><td>{item.result?.incidents ?? '—'}</td><td><button className="text-button" onClick={() => { setSelected(item.id); setTab('incidents') }}>Open →</button></td></tr>)}</tbody></table></div>}
        </section>{job && <JobDetail key={job.id} job={job} incidents={incidents.filter(row => row.job_id === job.id)} reviews={reviews.filter(row => row.job_id === job.id)} canReview={canReview} refresh={refreshDetail}/>}</>}
      </>}
      <footer>OpsFusion · Evidence-backed incident review <span>Causes remain unconfirmed</span></footer>
    </main></div>
}

function App() {
  const [session, setSession] = useState(null), [loading, setLoading] = useState(configured)
  useEffect(() => {
    if (!configured) return
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); setLoading(false) }).catch(() => setLoading(false))
    const { data } = supabase.auth.onAuthStateChange((_event, next) => { setSession(next); setLoading(false) })
    return () => data.subscription.unsubscribe()
  }, [])
  if (loading) return <main className="loading"><Brand/><p>Opening your workspace…</p></main>
  return session ? <Workspace key={session.user.id} session={session}/> : <SignIn/>
}

createRoot(document.getElementById('root')).render(<App/> )
