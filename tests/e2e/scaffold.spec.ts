import { expect, test } from '@playwright/test';

test.describe('scaffold', () => {
  test('web shell renders header and footer', async ({ page }) => {
    await page.goto('/');
    await expect(
      page.getByRole('heading', { name: '隧道数字孪生巡检平台' }),
    ).toBeVisible();
    await expect(page.getByText('数字孪生功能原型｜非生产系统')).toBeVisible();
  });

  test('api health endpoint responds', async ({ request }) => {
    const response = await request.get('http://127.0.0.1:8000/api/health');
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.status).toBe('ok');
  });
});
