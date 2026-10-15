import test from 'node:test'
import assert from 'node:assert/strict'
import { canonicalName, recordingSettings, resultPath, workerOnline } from '../src/contract.js'

const values = { asset_id: 'machine-01', camera_id: 'camera-01', video_start: '2026-10-15T10:00:00Z', polygon: '[[0,0],[1,0],[1,1]]', max_video_seconds: 20, sample_fps: 5 }
const csv = { name: 'capture.csv', size: 100 }
test('canonical slots cannot select object paths or upload models', () => {
  assert.equal(canonicalName('logs', { ...csv, name: '../../capture.CSV' }), 'logs.csv')
  assert.throws(() => canonicalName('sensor_model', { name: 'model.joblib', size: 1 }))
  assert.throws(() => canonicalName('video', { name: 'clip.mp4', size: 51 * 1024 * 1024 }))
  assert.throws(() => canonicalName('logs', { ...csv, size: 0 }))
})
test('logs need no fabricated clocks or baselines', () => {
  const settings = recordingSettings(values, { logs: csv })
  assert.equal(settings.video_start, undefined)
  assert.equal(settings.audio_synthetic, false)
})
test('video requires timezone, normalized polygon and bounded sampling', () => {
  const files = { video: { name: 'clip.mp4', size: 100 } }
  assert.equal(recordingSettings(values, files).video_start, values.video_start)
  for (const change of [{ video_start: '2026-10-15T10:00:00' }, { polygon: '[[0,0],[1,1]]' }, { polygon: '[[0,0],[2,0],[1,1]]' }, { max_video_seconds: 61 }, { sample_fps: 0 }]) {
    assert.throws(() => recordingSettings({ ...values, ...change }, files))
  }
})
test('normal training and calibration require explicit confirmation', () => {
  assert.throws(() => recordingSettings(values, { sensor: csv }))
  assert.throws(() => recordingSettings(values, { logs: csv, sensor_train: csv }))
  const files = { sensor: csv, sensor_train: csv, sensor_validation: csv }
  assert.throws(() => recordingSettings(values, files))
  assert.equal(recordingSettings({ ...values, normal_periods_reviewed: true }, files).normal_periods_reviewed, true)
})
test('stale or future worker clocks do not report availability', () => {
  const now = Date.parse('2026-10-15T10:00:00Z')
  assert.equal(workerOnline([{ last_seen: '2026-10-15T09:59:00Z' }], now), true)
  assert.equal(workerOnline([{ last_seen: '2026-10-15T09:58:00Z' }], now), false)
  assert.equal(workerOnline([{ last_seen: '2026-10-16T10:00:00Z' }], now), false)
  assert.equal(workerOnline([{ last_seen: 'invalid' }], now), false)
})
test('downloads stay inside the selected job and a worker attempt', () => {
  const job = '11111111-1111-4111-8111-111111111111'
  const lease = '22222222-2222-4222-8222-222222222222'
  assert.equal(resultPath(job, `${job}/${lease}/incidents.json`), `${job}/${lease}/incidents.json`)
  for (const path of [`other/${lease}/incidents.json`, `${job}/${lease}/../secret`, `${job}/${lease}/a\\b`, `${job}/no-lease/file`]) assert.throws(() => resultPath(job, path))
})
