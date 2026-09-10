import { expect, test } from '@playwright/test';
import { probeConsole } from './helpers';

test.describe('Recorded Run 回放', () => {
  test('有实录、例外状态与本包未提供', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    await page.goto('/playback');
    await expect(page.getByRole('heading', { name: '任务回放' })).toBeVisible();
    await expect(page.getByTestId('recorded-run-empty')).toHaveCount(0);
    await expect(page.getByText('三维场景同步回放')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('run-kind-label')).toContainText('现场实录，待验收');
    await expect(page.getByTestId('run-identity')).toContainText('运行身份');
    await expect(page.getByTestId('scene-version-label')).toContainText('scene-001-v1');
    await expect(page.getByTestId('waveform-not-provided')).toContainText('本包未提供');
    await expect(page.getByTestId('events-not-provided')).toContainText('本包未提供');
    await expect(page.getByTestId('display-trajectory-disclaimer')).toBeVisible();
    await expect(page.getByTestId('bound-scene-missing')).toHaveCount(0);
    await expect(page.getByTestId('scene-version-binding')).toBeVisible();

    const taskSelect = page.locator('.task-select');
    const optionCount = await taskSelect.locator('option').count();
    expect(optionCount).toBeGreaterThanOrEqual(2);
    await taskSelect.selectOption('golden-run-v2-completed-with-exceptions');
    await expect(page.getByTestId('package-status-label')).toContainText('完成但有例外');

    probe.assertClean();
  });

  test('完整 Replay Trajectory 可分页超过默认页', async ({ request }) => {
    const first = await request.get(
      '/api/tasks/golden-run-v2-completed/trajectory?from_seq=0&limit=10000',
    );
    expect(first.ok()).toBeTruthy();
    const page = (await first.json()) as { seq: number }[];
    expect(page.length).toBeGreaterThan(0);
    const oversize = await request.get(
      '/api/tasks/golden-run-v2-completed/trajectory?limit=1000000',
    );
    expect(oversize.ok()).toBeTruthy();
    expect(((await oversize.json()) as unknown[]).length).toBe(page.length);
    const rejected = await request.get(
      '/api/tasks/golden-run-v2-completed/trajectory?limit=1000001',
    );
    expect(rejected.status()).toBe(422);
  });

  test('历史运行绑定原始 Scene Version', async ({ request }) => {
    const task = await request.get('/api/tasks/golden-run-v2-completed');
    const body = (await task.json()) as {
      scene_version_id: string;
      scene_id: string;
    };
    expect(body.scene_version_id).toBe('scene-001-v1');
    const meta = await request.get(
      `/api/scenes/${body.scene_id}/metadata?scene_version_id=${body.scene_version_id}`,
    );
    expect(meta.ok()).toBeTruthy();
    const metadata = (await meta.json()) as { scene_version_id: string };
    expect(metadata.scene_version_id).toBe('scene-001-v1');
    const version = await request.get('/api/scene-versions/scene-001-v1');
    expect(version.ok()).toBeTruthy();
  });
});
