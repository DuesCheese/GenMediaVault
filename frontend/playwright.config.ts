import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e', fullyParallel: false, workers: 1, timeout: 45000,
  use: { baseURL: process.env.GMV_E2E_URL || 'http://127.0.0.1:8080', viewport: { width: 1440, height: 1000 }, trace: 'retain-on-failure', screenshot: 'only-on-failure', channel: process.env.GMV_BROWSER_CHANNEL || 'chrome' },
  reporter: [['list'], ['html', { open: 'never' }]],
})
