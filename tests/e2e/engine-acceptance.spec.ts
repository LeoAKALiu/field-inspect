import { expect, test, type Page } from '@playwright/test';
import {
  analyzePng,
  pngByteThreshold,
  probeConsoleStrict,
  requireEngineCanvas,
  waitForEngineRendered,
} from './helpers';

/**
 * engine-only 正式验收（硬性失败，不走 MockViewer 兜底）。
 *
 * 与 integration.spec.ts 的双路径兜底不同，本 spec 判定全部为硬失败：
 * 真引擎 canvas 必须出现、OBJ/PLY 资源必须 200/206、视口必须非纯黑、
 * 显示模式必须真实可切换、focusObject 必须生效。
 *
 * 运行方式：
 * - 全量（含本 spec）：`pnpm exec playwright test`
 * - 仅 engine-only：`pnpm exec playwright test --project=engine-acceptance`
 *
 * 控制台白名单收紧：仅豁免 favicon 与 SwiftShader 性能警告
 * （automatic fallback to software / GroupMarkerNotSet），
 * three.js 报错或 WebGL context 创建失败一律判失败。
 */

/** 含三维区域的五个页面 */
const VIEWER_PAGES: Array<{ route: string; label: string; readyText: string }> = [
  { route: '/', label: '综合态势', readyText: '三维数字孪生全景' },
  { route: '/inspection', label: '三维巡检', readyText: '巡检车状态' },
  { route: '/playback', label: '任务回放', readyText: '三维场景同步回放' },
  { route: '/equipment', label: '监测设备', readyText: '设备台账与在线状态' },
  { route: '/review', label: '问题定位与复核', readyText: '待复核队列' },
];

/** 监听 LIRIS OBJ/PLY 资源请求，记录其 HTTP 状态码。 */
function trackLirisAssets(page: Page): { obj: number[]; ply: number[] } {
  const seen = { obj: [] as number[], ply: [] as number[] };
  page.on('response', (res) => {
    if (res.url().includes('/tunnel/liris/tunnel_mesh.obj')) seen.obj.push(res.status());
    if (res.url().includes('/tunnel/liris/tunnel_pointcloud.ply')) seen.ply.push(res.status());
  });
  return seen;
}

test.describe('engine-only 正式验收', () => {
  for (const { route, label, readyText } of VIEWER_PAGES) {
    test(`E1 ${label}（${route}）：真引擎 canvas + LIRIS 资源 200/206 + 视口非纯黑`, async ({
      page,
    }) => {
      const probe = probeConsoleStrict();
      probe.attach(page);
      const assets = trackLirisAssets(page);

      await page.goto(route);
      await page.getByText(readyText).first().waitFor({ state: 'visible', timeout: 15_000 });

      // 1) 真引擎 canvas 必须出现；任何 MockViewer 降级即失败
      await waitForEngineRendered(page, label);

      // 2) OBJ / PLY 资源请求成功（200 整体或 206 分段）
      await expect
        .poll(() => assets.obj.length, { timeout: 30_000, message: '未观察到 tunnel_mesh.obj 请求' })
        .toBeGreaterThan(0);
      await expect
        .poll(() => assets.ply.length, { timeout: 30_000, message: '未观察到 tunnel_pointcloud.ply 请求' })
        .toBeGreaterThan(0);
      for (const s of assets.obj) expect([200, 206]).toContain(s);
      for (const s of assets.ply) expect([200, 206]).toContain(s);

      // 3) 视口非纯黑/空画布：PNG 字节数 + 抽样像素亮度方差双判定
      //    （waitForEngineRendered 内已执行，这里再独立断言一次最终帧）
      const shot = await page.locator('.twin-adapter').first().screenshot();
      const stats = analyzePng(shot);
      expect(
        shot.length,
        `[${label}] 视口截图字节数过小（疑似纯黑）`,
      ).toBeGreaterThan(pngByteThreshold(stats.width, stats.height));
      expect(stats.lumaStd, `[${label}] 视口像素亮度方差过低（疑似空画布）`).toBeGreaterThan(5);
      expect(stats.meanLuma, `[${label}] 视口平均亮度过低（疑似纯黑）`).toBeGreaterThan(3);

      probe.assertClean();
    });
  }

  test('E2 /inspection 显示模式切换：三种模式真实可切且视口图像变化', async ({ page }) => {
    const probe = probeConsoleStrict();
    probe.attach(page);
    await page.goto('/inspection');
    await waitForEngineRendered(page, '三维巡检(模式切换)');

    const viewport = page.locator('.twin-adapter').first();
    let prevShot: Buffer | null = null;
    for (const modeLabel of ['实体网格', '点云云图', '融合叠加']) {
      const btn = page.locator('.hud-btn', { hasText: modeLabel });
      // HUD 遮挡修复回归：按钮必须在 /inspection 页真实可点击
      await expect(btn).toBeVisible();
      await btn.click();
      await expect(btn).toHaveClass(/active/);
      await page.waitForTimeout(600); // 等引擎按新模式重绘
      const shot = await viewport.screenshot();
      if (prevShot) {
        expect(
          shot.equals(prevShot),
          `切换到「${modeLabel}」后视口图像未发生变化`,
        ).toBeFalsy();
      }
      prevShot = shot;
    }

    probe.assertClean();
  });

  test('E3 /inspection focusObject：点击异常候选触发定位与选中详情', async ({ page }) => {
    const probe = probeConsoleStrict();
    probe.attach(page);
    await page.goto('/inspection');
    await waitForEngineRendered(page, '三维巡检(focusObject)');

    const viewport = page.locator('.twin-adapter').first();
    const before = await viewport.screenshot();

    // 点击「视角快速定位」中的第一个异常候选预设按钮
    const preset = page.locator('.preset-btn').first();
    await expect(preset).toBeVisible();
    await preset.click();

    // 选中态：右侧「选中目标属性」面板出现该事件编号
    const presetText = (await preset.textContent()) ?? '';
    const eventId = presetText.match(/evt-\d+/)?.[0];
    expect(eventId, '预设按钮未包含事件编号').toBeTruthy();
    await expect(page.locator('.right-panel .meta-tag')).toHaveText(eventId!);
    await expect(page.locator('.entity-detail-box')).toBeVisible();

    // 相机联动（可选断言）：focusObject 后视口图像应发生变化
    await page.waitForTimeout(1200); // 相机飞行动画
    const after = await viewport.screenshot();
    expect(after.equals(before), 'focusObject 后视口图像未变化（相机未联动）').toBeFalsy();

    probe.assertClean();
  });
});
