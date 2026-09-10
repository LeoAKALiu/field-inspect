import * as fs from 'node:fs';
import * as path from 'path';
import { expect, test } from '@playwright/test';
import { analyzePng, waitForEngineRendered } from './helpers';

/**
 * 正式页面截图（严格 1920×1080）。
 *
 * 与验收截图的约定：
 * - 视口固定 1920×1080，viewport 截图（fullPage: false），产物必须严格 1920×1080
 *   （直接解析 PNG IHDR 断言宽高，不做 fullPage 后裁剪）；
 * - 含三维区域的页面必须等真引擎出图（非纯黑双判定），MockViewer 状态一律失败；
 * - 除综合态势外，截图前断言 document 无纵向溢出；综合态势在宽而矮的桌面窗口
 *   使用页面级自然滚动，禁止左右栏嵌套滚动，并验证末端卡片与页脚均可到达。
 *
 * 运行：`pnpm exec playwright test tests/e2e/screenshots.spec.ts`
 */

const SHOTS_DIR = path.resolve(__dirname, '../../docs/screenshots');

test.use({ viewport: { width: 1920, height: 1080 } });

const PAGES: Array<{ route: string; file: string; hasViewer: boolean; readyText: string }> = [
  { route: '/', file: '01-overall.png', hasViewer: true, readyText: '三维数字孪生全景' },
  { route: '/inspection', file: '02-inspection.png', hasViewer: true, readyText: '巡检车状态' },
  { route: '/playback', file: '03-playback.png', hasViewer: true, readyText: '三维场景同步回放' },
  { route: '/equipment', file: '04-equipment.png', hasViewer: true, readyText: '设备台账与在线状态' },
  { route: '/review', file: '05-review.png', hasViewer: true, readyText: '待复核队列' },
  { route: '/integration', file: '06-integration.png', hasViewer: false, readyText: '接入通道' },
];

test.describe('页面截图（严格 1920×1080）', () => {
  for (const { route, file, hasViewer, readyText } of PAGES) {
    test(`${route} → ${file}`, async ({ page }) => {
      await page.goto(route);
      await page.getByText(readyText).first().waitFor({ state: 'visible', timeout: 15_000 });

      if (hasViewer) {
        // 真引擎出图（canvas + 非纯黑双判定）；MockViewer 降级直接失败
        await waitForEngineRendered(page, route);
      }

      // 横向溢出在所有页面都不允许。综合态势为了避免左右栏嵌套滚动，允许整页纵向
      // 滚动；其余页面继续保持 1920×1080 一屏布局。
      const docSize = await page.evaluate(() => ({
        w: document.documentElement.scrollWidth,
        h: document.documentElement.scrollHeight,
      }));
      expect(docSize.w, `[${route}] 页面横向溢出`).toBeLessThanOrEqual(1920);
      const footer = page.getByText('数字孪生功能原型｜非生产系统');
      if (route === '/') {
        const nestedColumnOverflow = await page
          .locator('.overall-grid .left-column, .overall-grid .right-column')
          .evaluateAll((columns) => columns.map((column) => getComputedStyle(column).overflowY));
        expect(nestedColumnOverflow).toEqual(['visible', 'visible']);
        await page.locator('.overall-grid .left-column .panel-card:last-child').scrollIntoViewIfNeeded();
        await page.locator('.overall-grid .right-column .panel-card:last-child').scrollIntoViewIfNeeded();
        await footer.scrollIntoViewIfNeeded();
        await expect(footer).toBeVisible();
        await page.evaluate(() => window.scrollTo(0, 0));
      } else {
        expect(docSize.h, `[${route}] 页面纵向溢出（1080 视口内出现全局滚动条）`).toBeLessThanOrEqual(1080);
        await expect(footer).toBeVisible();
      }

      if (hasViewer) {
        // LIRIS 场景角标（合成隧道场景｜车辆轨迹与监测数据为模拟数据）
        await expect(page.locator('.scene-corner-badge').first()).toBeVisible();
      }

      // 渲染沉淀（引擎首帧 / ECharts 动画）
      await page.waitForTimeout(2500);

      const buf = await page.screenshot({ fullPage: false });
      // 严格尺寸断言：直接读 PNG IHDR
      const stats = analyzePng(buf);
      expect(
        { w: stats.width, h: stats.height },
        `[${route}] 截图尺寸必须严格 1920×1080`,
      ).toEqual({ w: 1920, h: 1080 });

      fs.writeFileSync(path.join(SHOTS_DIR, file), buf);
    });
  }
});
