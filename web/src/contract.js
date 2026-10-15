export const slots = [
  { id: 'video', label: 'Camera recording', accept: '.mp4,.avi,.mov,.mkv', limit: 50, group: 'Video' },
  { id: 'sensor', label: 'Sensor measurements', accept: '.csv', limit: 5, group: 'Sensors' },
  { id: 'sensor_train', label: 'Normal training CSV', accept: '.csv', limit: 5, group: 'Sensors' },
  { id: 'sensor_validation', label: 'Normal calibration CSV', accept: '.csv', limit: 5, group: 'Sensors' },
  { id: 'audio', label: 'Machine audio', accept: '.wav', limit: 20, group: 'Audio' },
  { id: 'audio_train', label: 'Normal training WAV', accept: '.wav', limit: 20, group: 'Audio' },
  { id: 'audio_validation', label: 'Normal calibration WAV', accept: '.wav', limit: 20, group: 'Audio' },
  { id: 'logs', label: 'Controller logs', accept: '.csv', limit: 5, group: 'Logs' },
]

export function canonicalName(slot, file) {
  const spec = slots.find(s => s.id === slot)
  const suffix = '.' + file.name.split('.').pop().toLowerCase()
  if (!spec || !spec.accept.split(',').includes(suffix)) throw new Error(`Unsupported file format for ${slot}.`)
  if (!file.size || file.size > spec.limit * 1024 * 1024) throw new Error(`${spec.label}: choose a file up to ${spec.limit} MiB.`)
  return slot + suffix
}

function timestamp(value, name) {
  if (!value || !/(Z|[+-]\d\d:\d\d)$/.test(value) || Number.isNaN(Date.parse(value))) {
    throw new Error(`${name} needs an ISO timestamp with a timezone, for example 2026-10-15T10:00:00Z.`)
  }
}

export function recordingSettings(values, files) {
  if (!['video', 'sensor', 'audio', 'logs'].some(key => files[key])) throw new Error('Choose at least one recording source.')
  if (!values.asset_id?.trim() || values.asset_id.length > 256) throw new Error('Enter an asset ID of up to 256 characters.')
  if (!values.camera_id?.trim() || values.camera_id.length > 256) throw new Error('Enter a camera ID of up to 256 characters.')
  let total = 0
  for (const [slot, file] of Object.entries(files)) { canonicalName(slot, file); total += file.size }
  if (total > 150 * 1024 * 1024) throw new Error('A job can contain up to 150 MiB of input files.')
  for (const modality of ['sensor', 'audio']) {
    if (files[modality] && (!files[`${modality}_train`] || !files[`${modality}_validation`])) {
      throw new Error(`${modality}: provide normal training and calibration recordings.`)
    }
    if (!files[modality] && (files[`${modality}_train`] || files[`${modality}_validation`])) {
      throw new Error(`${modality}: add the capture file as well as its normal periods.`)
    }
  }
  if ((files.sensor || files.audio) && !values.normal_periods_reviewed) throw new Error('Confirm that the baseline periods are known normal.')
  const settings = {
    normal_periods_reviewed: Boolean(values.normal_periods_reviewed),
    video_synthetic: Boolean(values.video_synthetic), audio_synthetic: Boolean(values.audio_synthetic),
    audio_baseline_synthetic: Boolean(values.audio_baseline_synthetic),
  }
  if (files.video) {
    timestamp(values.video_start, 'Video start')
    let polygon
    try { polygon = JSON.parse(values.polygon) } catch { throw new Error('Enter the zone polygon as a JSON list of normalized [x,y] points.') }
    if (!Array.isArray(polygon) || polygon.length < 3 || polygon.length > 30 || polygon.some(p =>
      !Array.isArray(p) || p.length !== 2 || p.some(x => typeof x !== 'number' || !Number.isFinite(x) || x < 0 || x > 1))) {
      throw new Error('Use 3–30 normalized [x,y] points between 0 and 1.')
    }
    const seconds = Number(values.max_video_seconds), fps = Number(values.sample_fps)
    if (!(seconds > 0 && seconds <= 60 && fps >= 1 && fps <= 10)) throw new Error('Video duration must be 1–60 seconds; sampling must be 1–10 FPS.')
    Object.assign(settings, { polygon, video_start: values.video_start, max_video_seconds: seconds, sample_fps: fps })
  }
  if (files.audio) {
    for (const field of ['audio_start', 'audio_train_start', 'audio_validation_start']) {
      timestamp(values[field], field.replaceAll('_', ' ')); settings[field] = values[field]
    }
  }
  return settings
}

export function workerOnline(workers, now = Date.now()) {
  return workers.some(worker => { const age = now - Date.parse(worker.last_seen); return age >= -5000 && age < 90000 })
}

export function resultPath(jobId, path) {
  const parts = path.split('/')
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
  if (parts[0] !== jobId || !uuid.test(parts[1]) || parts.length < 3 || parts.some(x => !x || x === '..' || x === '.') || path.includes('\\')) {
    throw new Error('Invalid artifact reference.')
  }
  return path
}
