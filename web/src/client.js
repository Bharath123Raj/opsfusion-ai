import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY
// Public configuration only. A service-role/secret key is never accepted here.
if (key && !key.startsWith('sb_publishable_')) {
  throw new Error('The web application requires a Supabase publishable key.')
}
export const configured = Boolean(url && key && key.startsWith('sb_publishable_'))
export const supabase = configured ? createClient(url, key) : null
