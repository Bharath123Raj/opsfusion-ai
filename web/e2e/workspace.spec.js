// Browser contract tests use mocked cloud responses, never real credentials.
import { test, expect } from '@playwright/test'

const actor = '11111111-1111-4111-8111-111111111111'
const lease = '22222222-2222-4222-8222-222222222222'
const jobId = '33333333-3333-4333-8333-333333333333'
const user = { id: actor, aud: 'authenticated', role: 'authenticated', email: 'operator@example.invalid', app_metadata: {}, user_metadata: {}, created_at: '2026-10-15T10:00:00Z' }
const report = { incident_id: 'test-incident', type: 'operational_warning', risk: 'medium', start: '2026-10-15T10:00:00Z', end: '2026-10-15T10:00:07Z', synthetic: true,
  observed_facts: [{ event_id: 'test-log', summary: 'Synthetic controller overload fixture' }], timeline: [{ event_id: 'test-log' }],
  hypothesis: { statement: 'An operating condition needs inspection.', status: 'unconfirmed' }, recommended_action: 'Inspect the controller evidence.' }

async function setup(page, { role = 'operator', completed = false } = {}) {
  const state = { jobs: [], reviews: [], uploads: [], manifest: null }
  if (completed) state.jobs.push({ id: jobId, asset_id: 'machine-01', status: 'succeeded', created_at: '2026-10-15T10:00:00Z', result: {
    observations: 1, incidents: 1, artifacts: [{ name: 'incidents.json', path: `${jobId}/${lease}/incidents.json` }, { name: 'inputs/controller_logs.csv', path: `${jobId}/${lease}/inputs/controller_logs.csv` }],
    evidence: { 'test-log': { record: `${jobId}/${lease}/inputs/controller_logs.csv` } },
  } })
  await page.route('https://test-project.supabase.co/**', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method()
    let body = null
    if (request.postData()) { try { body = request.postDataJSON() } catch {} }
    let data = [], status = 200
    if (url.pathname === '/auth/v1/token') data = { access_token: 'fixture-access-token', token_type: 'bearer', expires_in: 3600, refresh_token: 'fixture-refresh-token', user }
    else if (url.pathname === '/auth/v1/user') data = user
    else if (url.pathname === '/auth/v1/logout') status = 204
    else if (url.pathname.includes('/opsfusion_members')) data = role ? [{ role }] : []
    else if (url.pathname.includes('/opsfusion_workers')) data = []
    else if (url.pathname.includes('/opsfusion_jobs')) {
      if (method === 'POST') { state.manifest = body.input; state.jobs.unshift({ ...body, created_at: new Date().toISOString(), status: 'draft' }); data = null; status = 201 }
      else if (method === 'PATCH') { state.jobs[0].status = body.status; data = [{ id: state.jobs[0].id }] }
      else data = state.jobs
    } else if (url.pathname.includes('/opsfusion_incidents')) data = completed ? [{ job_id: jobId, incident_id: report.incident_id, first_report: report, current_report: report }] : []
    else if (url.pathname.includes('/opsfusion_reviews')) {
      if (method === 'POST') { state.reviews.unshift({ ...body, id: 1, actor, created_at: new Date().toISOString() }); data = null; status = 201 }
      else data = state.reviews
    } else if (url.pathname.includes('/storage/v1/object/')) {
      if (method === 'POST') { state.uploads.push(url.pathname); data = { Key: url.pathname } }
      else return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ synthetic: true }) })
    } else return route.abort()
    await route.fulfill({ status, contentType: 'application/json', body: status === 204 ? undefined : JSON.stringify(data) })
  })
  await page.goto('/')
  await page.getByLabel('Email address').fill(user.email)
  await page.getByLabel('Password', { exact: true }).fill('fixture-password-no-real-account')
  await page.getByRole('button', { name: 'Sign in →' }).click()
  await expect(page.getByRole('heading', { name: 'Operational overview' })).toBeVisible()
  return state
}

test('operator uploads a real CSV payload and queues its checksum manifest', async ({ page }) => {
  const state = await setup(page)
  await page.getByRole('button', { name: '+ New analysis' }).click()
  await page.getByLabel('Asset ID', { exact: true }).fill('machine-01')
  await page.getByLabel('Controller logs', { exact: false }).setInputFiles({ name: 'my-controller.csv', mimeType: 'text/csv', buffer: Buffer.from('timestamp,asset_id,source_id,level,code,message,synthetic\n2026-10-15T10:00:00Z,machine-01,plc-01,WARN,MOTOR_OVERLOAD,Test,true\n') })
  await page.getByRole('button', { name: 'Upload & queue analysis →' }).click()
  await expect(page.locator('tbody .status')).toHaveText('queued')
  expect(state.uploads).toHaveLength(1)
  expect(state.manifest.files.logs.name).toBe('logs.csv')
  expect(state.manifest.files.logs.sha256).toMatch(/^[0-9a-f]{64}$/)
  expect(state.manifest.settings.video_start).toBeUndefined()
})

test('admin opens New analysis from the sidebar', async ({ page }) => {
  await setup(page, { role: 'admin' })
  await page.getByRole('navigation').getByRole('button', { name: /New analysis/ }).click()
  await expect(page.getByRole('heading', { name: 'Analyse recordings' })).toBeVisible()
  await expect(page.getByLabel('Asset ID', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upload & queue analysis →' })).toBeEnabled()
})

test('evidence downloads and saved reviews remain visible after reload', async ({ page }) => {
  await setup(page, { completed: true })
  await page.getByRole('button', { name: 'Open →' }).click()
  await expect(page.getByText('Synthetic controller overload fixture', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/qa-desktop.png', fullPage: true })
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '↓ Evidence · test-log' }).click()
  expect((await download).suggestedFilename()).toBe('controller_logs.csv')
  await page.getByLabel('Review decision').selectOption('resolved')
  await page.getByLabel('Review note').fill('Checked the synthetic test evidence.')
  await page.getByRole('button', { name: 'Save review' }).click()
  await expect(page.getByText('Saved review history (1)')).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: 'Open →' }).click()
  await page.getByText('Saved review history (1)').click()
  await expect(page.getByText('Checked the synthetic test evidence.')).toBeVisible()
})

test('viewer cannot submit or review', async ({ page }) => {
  await setup(page, { role: 'viewer', completed: true })
  await expect(page.getByRole('button', { name: /New analysis/ })).toBeDisabled()
  await expect(page.getByText('Your viewer role can read reports. New analysis and review changes require admin or operator access.')).toBeVisible()
  await page.getByRole('button', { name: 'Open →' }).click()
  await expect(page.getByRole('button', { name: 'Save review' })).toHaveCount(0)
})

test('signed-in nonmember sees no site data', async ({ page }) => {
  await setup(page, { role: null })
  await expect(page.getByRole('heading', { name: 'Your account is signed in' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Recording analyses' })).toHaveCount(0)
  await expect(page.getByText('No workspace membership is assigned to', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Use another account' }).click()
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
})

test('mobile sign-in fits the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/qa-mobile.png', fullPage: true })
})

test('Google sign-in requests the current origin as its callback', async ({ page }) => {
  await page.route('https://test-project.supabase.co/auth/v1/authorize?**', route => route.fulfill({ contentType: 'text/html', body: 'OAuth fixture' }))
  await page.goto('/')
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await expect(page).toHaveURL(/auth\/v1\/authorize\?/)
  const url = new URL(page.url())
  expect(url.searchParams.get('provider')).toBe('google')
  expect(url.searchParams.get('prompt')).toBe('select_account')
  expect(url.searchParams.get('redirect_to')).toBe('http://127.0.0.1:4173')
})
