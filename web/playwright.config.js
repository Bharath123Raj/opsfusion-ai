import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:4173', headless: true,
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    } : {},
  },
  webServer: { command: 'npm run dev -- --port 4173', url: 'http://127.0.0.1:4173', reuseExistingServer: false,
    env: { VITE_SUPABASE_URL: 'https://test-project.supabase.co', VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_browser_test_only' },
  },
})
