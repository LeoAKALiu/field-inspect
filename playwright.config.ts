import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const playwrightDb = path.join(__dirname, 'data/var/playwright/twin.db');
const playwrightImports = path.join(__dirname, 'data/var/playwright/imports');

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'list',
  // 三维引擎（22MB OBJ + 16MB PLY）在 SwiftShader 软渲染下解析需数秒，
  // 多 worker 并行时首个字节到出图可能超过默认 30s。
  timeout: 90_000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      // engine-only 验收在独立 project 中跑（判定口径不同：硬性失败、严格白名单）
      testIgnore: '**/engine-acceptance.spec.ts',
    },
    {
      // 单跑：pnpm exec playwright test --project=engine-acceptance
      name: 'engine-acceptance',
      testMatch: '**/engine-acceptance.spec.ts',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // Safari 响应式回归：WebKit 内核，仅跑布局专项，避免重复整套重型三维验收。
      name: 'webkit-responsive',
      testMatch: '**/responsive.spec.ts',
      use: { ...devices['Desktop Safari'] },
    },
    {
      // Safari/WebKit 三维硬门槛：必须真 canvas 出图，不能只验证 DOM 与响应式布局。
      name: 'webkit-engine-acceptance',
      testMatch: '**/engine-acceptance.spec.ts',
      use: { ...devices['Desktop Safari'] },
    },
  ],
  webServer: [
    {
      command:
        'PYTHONPATH=. uv run python scripts/seed_recorded_playwright.py && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000',
      cwd: 'services/api',
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ...process.env,
        TWIN_DB_PATH: playwrightDb,
        TWIN_IMPORT_ROOT: playwrightImports,
      },
    },
    {
      command: 'pnpm dev --host 127.0.0.1',
      cwd: 'apps/web',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
