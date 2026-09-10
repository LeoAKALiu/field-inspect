import { expect, test } from '@playwright/test';

test.describe('1112px Safari-like responsive layout', () => {
  test.use({ viewport: { width: 1112, height: 768 } });

  test('overall scene gets a full row and side columns remain reachable', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: '综合态势' })).toBeVisible();

    const [center, left, right] = await Promise.all([
      page.locator('.overall-grid .center-column').boundingBox(),
      page.locator('.overall-grid .left-column').boundingBox(),
      page.locator('.overall-grid .right-column').boundingBox(),
    ]);
    expect(center).not.toBeNull();
    expect(left).not.toBeNull();
    expect(right).not.toBeNull();

    // 此宽度下三维视口必须独占首行，不能再被两侧态势栏挤成窄条。
    expect(center!.y + center!.height).toBeLessThanOrEqual(
      Math.min(left!.y, right!.y) + 1,
    );
    // 下方两栏不得相互覆盖，且页面不得产生横向滚动。
    expect(left!.x + left!.width).toBeLessThanOrEqual(right!.x + 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      await page.evaluate(() => document.documentElement.clientWidth),
    );

    const lastAlert = page.locator(
      '.overall-grid .right-column .panel-card:last-child .alert-feed-item:last-child',
    );
    await lastAlert.scrollIntoViewIfNeeded();
    await expect(lastAlert).toBeVisible();
  });

  test('inspection canvas and detail panels do not overlap', async ({ page }) => {
    await page.goto('/inspection');
    await expect(page.getByRole('heading', { name: '三维巡检' })).toBeVisible();

    const [canvas, left, right] = await Promise.all([
      page.locator('.inspection-canvas-wrapper').boundingBox(),
      page.locator('.floating-panel.left-panel').boundingBox(),
      page.locator('.floating-panel.right-panel').boundingBox(),
    ]);
    expect(canvas).not.toBeNull();
    expect(left).not.toBeNull();
    expect(right).not.toBeNull();

    expect(canvas!.y + canvas!.height).toBeLessThanOrEqual(Math.min(left!.y, right!.y) + 1);
    expect(left!.x + left!.width).toBeLessThanOrEqual(right!.x + 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      await page.evaluate(() => document.documentElement.clientWidth),
    );

    const bottomDisclaimer = page.locator('.floating-panel.right-panel .lineage-disclaimer');
    await bottomDisclaimer.scrollIntoViewIfNeeded();
    await expect(bottomDisclaimer).toBeVisible();
  });
});

test.describe('2048×1212 short-wide dashboard layout', () => {
  test.use({ viewport: { width: 2048, height: 1212 } });

  test('overall avoids nested side-column scrolling and covered cards', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: '综合态势' })).toBeVisible();

    const [center, left, right] = await Promise.all([
      page.locator('.overall-grid .center-column').boundingBox(),
      page.locator('.overall-grid .left-column').boundingBox(),
      page.locator('.overall-grid .right-column').boundingBox(),
    ]);
    expect(center).not.toBeNull();
    expect(left).not.toBeNull();
    expect(right).not.toBeNull();

    // 宽屏但高度不足时同样要退出固定三栏：主视口独占首行，左右态势栏在下方并排。
    expect(center!.y + center!.height).toBeLessThanOrEqual(
      Math.min(left!.y, right!.y) + 1,
    );
    expect(left!.x + left!.width).toBeLessThanOrEqual(right!.x + 1);

    const columnOverflow = await page
      .locator('.overall-grid .left-column, .overall-grid .right-column')
      .evaluateAll((columns) =>
        columns.map((column) => ({
          overflowY: getComputedStyle(column).overflowY,
          clientHeight: column.clientHeight,
          scrollHeight: column.scrollHeight,
        })),
      );
    for (const column of columnOverflow) {
      expect(column.overflowY).not.toMatch(/auto|scroll/);
      expect(column.scrollHeight).toBeLessThanOrEqual(column.clientHeight + 1);
    }

    const lastLeftCard = page.locator('.overall-grid .left-column .panel-card:last-child');
    const lastRightCard = page.locator('.overall-grid .right-column .panel-card:last-child');
    await lastLeftCard.scrollIntoViewIfNeeded();
    await lastRightCard.scrollIntoViewIfNeeded();
    await expect(lastLeftCard).toBeVisible();
    await expect(lastRightCard).toBeVisible();
    const footer = page.getByText('数字孪生功能原型｜非生产系统');
    await footer.scrollIntoViewIfNeeded();
    await expect(footer).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      await page.evaluate(() => document.documentElement.clientWidth),
    );
  });
});
