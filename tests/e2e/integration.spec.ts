import { expect, test } from '@playwright/test';
import { probeConsole, waitForViewer } from './helpers';

/**
 * 验收闭环（对应验收 12 步关键路径）：
 * 首页加载 → 三维渲染（引擎或 MockViewer 兜底）→ 显示模式切换 →
 * 任务回放 → 监测设备（含模拟曲线）→ 问题定位与复核（PATCH 状态流转）→
 * 数据接入 → 事件导出。全程断言无非白名单 console 错误。
 */

test.describe('验收闭环', () => {
  test('01 首页加载：Header、导航与页脚可见', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    await page.goto('/');

    await expect(
      page.getByRole('heading', { name: '隧道数字孪生巡检平台' }),
    ).toBeVisible();
    // 六路由导航
    for (const label of ['综合态势', '三维巡检', '任务回放', '监测设备', '问题定位与复核', '数据接入']) {
      await expect(page.locator('.header-nav').getByText(label, { exact: true })).toBeVisible();
    }
    // 页脚免责条
    await expect(page.getByText('数字孪生功能原型｜非生产系统')).toBeVisible();
    // 首页 KPI 条与三维全景面板
    await expect(page.getByRole('heading', { name: '综合态势' })).toBeVisible();
    await expect(page.getByText('三维数字孪生全景')).toBeVisible({ timeout: 15_000 });

    probe.assertClean();
  });

  test('02 三维视图渲染：引擎 canvas 或 MockViewer 兜底必有其一', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    await page.goto('/');
    await expect(page.getByText('三维数字孪生全景')).toBeVisible({ timeout: 15_000 });

    const path = await waitForViewer(page, '综合态势');
    expect(['engine', 'mock']).toContain(path);

    probe.assertClean();
  });

  test('03 显示模式切换：实体 / 点云 / 叠加按钮可点且不报错', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    // 本用例为双路径兜底冒烟（MockViewer 也算通过）。/inspection 页的 HUD 遮挡
    // 已修复（App.css 偏移规则），显示模式的正式硬性验收在
    // engine-acceptance.spec.ts E2 用例（/inspection 页，含图像变化断言）。
    await page.goto('/');
    await expect(page.getByText('三维数字孪生全景')).toBeVisible({ timeout: 15_000 });

    const path = await waitForViewer(page, '综合态势(显示模式)');
    if (path === 'engine') {
      // HUD 显示模式按钮（仅真引擎渲染时存在）
      for (const label of ['实体网格', '点云云图', '融合叠加']) {
        const btn = page.locator('.hud-btn', { hasText: label });
        await expect(btn).toBeVisible();
        await btn.click();
        await expect(btn).toHaveClass(/active/);
      }
    } else {
      console.log('[display-mode] MockViewer 兜底路径，HUD 按钮不存在，跳过切换交互（已记录）');
      await expect(page.locator('.mock-viewer').first()).toBeVisible();
    }

    probe.assertClean();
  });

  test('04 任务回放：选任务、播放/暂停、时间轴与倍速可操作', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    await page.goto('/playback');
    await expect(page.getByRole('heading', { name: '任务回放' })).toBeVisible();

    const demoBtn = page.getByTestId('show-demonstration-runs');
    await expect(demoBtn).toBeVisible();
    const recordedEmpty = page.getByTestId('recorded-run-empty');
    if (await recordedEmpty.isVisible()) {
      await expect(recordedEmpty).toContainText('暂无现场实录运行');
      await expect(page.getByText('三维场景同步回放')).toHaveCount(0);
    }
    await demoBtn.click();
    await expect(page.getByText('三维场景同步回放')).toBeVisible({ timeout: 15_000 });

    // 任务选择
    const taskSelect = page.locator('.task-select');
    await expect(taskSelect).toBeVisible();
    await expect(page.getByTestId('run-kind-label')).toContainText('演示运行');
    const optionCount = await taskSelect.locator('option').count();
    expect(optionCount).toBeGreaterThan(0);
    if (optionCount > 1) {
      await taskSelect.selectOption({ index: 1 });
      await expect(page.getByTestId('run-identity')).toBeVisible();
    }

    // 播放中：当前时刻持续推进（mm:ss 每秒跳动，轮询等待避免并行时序抖动）
    const currentTime = page.locator('.dock-center-scrubber .time-text').first();
    await expect(currentTime).toBeVisible();
    const t1 = await currentTime.textContent();
    await expect
      .poll(async () => currentTime.textContent(), { timeout: 8_000 })
      .not.toBe(t1);

    // 暂停后时刻冻结
    const playBtn = page.locator('.dock-btn.play-btn');
    await playBtn.click();
    await expect(playBtn).toContainText('开始回放');
    const t3 = await currentTime.textContent();
    await page.waitForTimeout(1500);
    expect(await currentTime.textContent()).toBe(t3);

    // 倍速控件
    const speed4 = page.locator('.speed-btn', { hasText: '4x' });
    await speed4.click();
    await expect(speed4).toHaveClass(/active/);

    // 时间轴滑块：拖动到 30s
    const scrubber = page.locator('.scrubber-input');
    await expect(scrubber).toBeVisible();
    await scrubber.fill('30');
    await expect(currentTime).toHaveText('00:30');

    // 点击关键异常事件 → 跳转到事件时刻
    const firstEvent = page.locator('.timeline-event-item').first();
    await expect(firstEvent).toBeVisible();
    const eventTime = (await firstEvent.locator('.item-time span').textContent())?.trim();
    await firstEvent.click();
    await expect(currentTime).toHaveText(eventTime!);

    probe.assertClean();
  });

  test('05 监测设备：列表、说明条与模拟曲线（ECharts）', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    await page.goto('/equipment');
    await expect(page.getByRole('heading', { name: '监测设备' })).toBeVisible();

    // 接入边界说明条
    await expect(
      page.getByText('系统侧已准备好接入离层仪、位移计等外部数据；甲方设备协议和数据字典待配置'),
    ).toBeVisible();

    // 设备台账：含离层仪 / 位移计中文类型标签
    const rows = page.locator('.industrial-table tbody tr');
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    expect(await rows.count()).toBeGreaterThan(0);
    await expect(page.locator('.kind-tag', { hasText: '离层仪' }).first()).toBeVisible();
    await expect(page.locator('.kind-tag', { hasText: '位移计' }).first()).toBeVisible();

    // 点击设备行 → 选中态 + 右侧详情
    const targetRow = rows.first();
    const deviceName = await targetRow.locator('.dev-label').textContent();
    await targetRow.click();
    await expect(targetRow).toHaveClass(/selected/);
    await expect(page.locator('.dev-info-header h3')).toHaveText(deviceName!);

    // 模拟曲线（后端读数 → ECharts canvas）
    const chart = page.locator('.chart-box canvas').first();
    await expect(chart).toBeVisible({ timeout: 15_000 });
    expect(await page.locator('.chart-box canvas').count()).toBeGreaterThanOrEqual(1);

    probe.assertClean();
  });

  test('06 问题定位与复核：候选定位 + 复核状态 PATCH 流转', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    await page.goto('/review');
    await expect(page.getByRole('heading', { name: '问题定位与复核' })).toBeVisible();

    // 异常候选列表（GET /api/events）
    const queue = page.locator('.anomaly-queue-item');
    await expect(queue.first()).toBeVisible({ timeout: 15_000 });
    expect(await queue.count()).toBeGreaterThan(0);

    // 点击候选 → 选中态 + 复核详情面板（不断言 3D 相机）
    const candidate = (await queue.count()) > 1 ? queue.nth(1) : queue.first();
    const candidateId = await candidate.locator('.ano-code').textContent();
    await candidate.click();
    await expect(candidate).toHaveClass(/selected/);
    await expect(page.getByText('复核与派单', { exact: true })).toBeVisible();
    await expect(page.locator('.form-readonly-val')).toContainText(candidateId!);

    // 状态流转：优先 open → acknowledged（确认缺陷并派单），
    // 否则 acknowledged → false_positive（判定为误报），保证测试可重复运行。
    const pendingItem = page.locator('.anomaly-queue-item', { hasText: '待专家复核' }).first();
    const verifiedItem = page.locator('.anomaly-queue-item', { hasText: '已复核确认' }).first();

    if ((await pendingItem.count()) > 0) {
      // 状态流转后「待专家复核」文案会消失，先按事件编号固定列表项定位
      const itemId = (await pendingItem.locator('.ano-code').textContent())!;
      const item = page.locator('.anomaly-queue-item', { hasText: itemId });
      await item.click();
      const patchResp = page.waitForResponse(
        (r) => /\/api\/events\/[^/?]+$/.test(r.url()) && r.request().method() === 'PATCH',
      );
      await page.getByRole('button', { name: /确认缺陷并派单/ }).click();
      expect((await patchResp).ok()).toBeTruthy();
      await expect(page.locator('.toast-notification')).toContainText('已更新为：已复核确认');
      await expect(item.locator('.status-pill')).toHaveText('已复核确认');
    } else if ((await verifiedItem.count()) > 0) {
      const itemId = (await verifiedItem.locator('.ano-code').textContent())!;
      const item = page.locator('.anomaly-queue-item', { hasText: itemId });
      await item.click();
      const patchResp = page.waitForResponse(
        (r) => /\/api\/events\/[^/?]+$/.test(r.url()) && r.request().method() === 'PATCH',
      );
      await page.getByRole('button', { name: /判定为误报/ }).click();
      expect((await patchResp).ok()).toBeTruthy();
      await expect(page.locator('.toast-notification')).toContainText('已更新为：已判定误报');
      await expect(item.locator('.status-pill')).toHaveText('已判定误报');
    } else {
      throw new Error('复核队列中无可流转状态的事件（待专家复核 / 已复核确认均不存在）');
    }

    probe.assertClean();
  });

  test('07 数据接入：四类通道、RS-485/边缘网关示意与待配置声明', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    await page.goto('/integration');
    await expect(page.getByRole('heading', { name: '数据接入' })).toBeVisible();

    // 待配置声明
    await expect(page.getByText('甲方协议和数据字典待配置')).toBeVisible();

    // 四类接入通道
    await expect(page.locator('.channel-card')).toHaveCount(4);
    for (const name of ['HTTP REST 轮询', 'MQTT 订阅', 'WebSocket 推送', '文件导入']) {
      await expect(page.locator('.channel-card', { hasText: name })).toBeVisible();
    }

    // RS-485 / 边缘网关链路示意
    await expect(page.getByText('现场总线接入链路示意（RS-485 / Modbus）')).toBeVisible();
    await expect(page.getByText('RS-485 · Modbus RTU')).toBeVisible();
    await expect(page.getByText('边缘网关')).toBeVisible();

    probe.assertClean();
  });

  test('08 事件导出：POST /api/events/export 返回 CSV 下载', async ({ page }) => {
    const probe = probeConsole();
    probe.attach(page);
    await page.goto('/review');
    await expect(page.locator('.anomaly-queue-item').first()).toBeVisible({ timeout: 15_000 });

    const exportResp = page.waitForResponse(
      (r) => r.url().includes('/api/events/export') && r.request().method() === 'POST',
    );
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /导出记录/ }).click();

    const [response, download] = await Promise.all([exportResp, downloadPromise]);
    expect(response.ok()).toBeTruthy();
    expect(response.headers()['content-type'] ?? '').toContain('text/csv');
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
    expect(await download.path()).toBeTruthy();

    probe.assertClean();
  });
});
