/**
 * 真实 GPU 验收脚本（headed 浏览器，非 SwiftShader 路径）
 *
 * 前提：后端 (8000) 与前端 (5173) 已在运行（可用 start.command 或手动启动）。
 * 运行：node scripts/gpu-acceptance.mjs
 * 输出：控制台实时日志 + docs/GPU_REPORT.md
 *
 * 记录：浏览器/操作系统/GPU 渲染器、OBJ/PLY 首次加载时间、三种显示模式切换时间、
 * 黑屏/闪退/卡顿观察，并连续操作约 10 分钟（六页轮巡 + 交互）。
 */
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.GPU_BASE_URL ?? 'http://127.0.0.1:5173';
const SOAK_MINUTES = Number(process.env.GPU_SOAK_MINUTES ?? 10);
const report = { startedAt: new Date().toISOString(), checks: [], timings: {}, errors: [] };
const log = (msg) => { console.log(msg); report.checks.push(msg); };

async function waitForEngine(page, timeoutMs = 60_000) {
  // 真引擎出图：canvas 可见且视口截图非纯黑（字节数阈值）
  const canvas = page.locator('.twin-adapter canvas').first();
  await canvas.waitFor({ state: 'visible', timeout: timeoutMs });
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const buf = await canvas.screenshot();
    if (buf.length > 30_000) return true;
    await page.waitForTimeout(1000);
  }
  throw new Error('三维视口 60s 内未出图（疑似黑屏/空画布）');
}

async function main() {
  const browser = await chromium.launch({ headless: false, args: ['--window-size=1920,1120'] });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();

  page.on('pageerror', (e) => report.errors.push(`pageerror: ${e.message}`));
  page.on('crash', () => report.errors.push('PAGE CRASH'));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/favicon/i.test(m.text())) report.errors.push(`console: ${m.text()}`);
  });

  // 1) 环境信息
  report.env = {
    os: `${os.type()} ${os.release()} ${os.arch()}`,
    node: process.version,
    chromium: browser.version(),
  };
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  const gpu = await page.evaluate(() => {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2') ?? c.getContext('webgl');
    if (!gl) return { available: false };
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      available: true,
      vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : 'n/a',
      renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'n/a',
      version: gl.getParameter(gl.VERSION),
    };
  });
  report.gpu = gpu;
  log(`环境: ${report.env.os} / Chromium ${report.env.chromium}`);
  log(`GPU: vendor=${gpu.vendor} renderer=${gpu.renderer}`);
  if (!gpu.available || /swiftshader/i.test(gpu.renderer ?? '')) {
    log('!! 未使用真实 GPU（SwiftShader 或不可用），本次不计入真实 GPU 验收');
  } else {
    log('OK 真实 GPU 路径确认（非 SwiftShader）');
  }

  // 2) OBJ/PLY 首次加载时间
  const t0 = Date.now();
  let objAt = 0, plyAt = 0;
  page.on('response', (r) => {
    if (r.url().includes('tunnel_mesh.obj') && !objAt) objAt = Date.now() - t0;
    if (r.url().includes('tunnel_pointcloud.ply') && !plyAt) plyAt = Date.now() - t0;
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await waitForEngine(page);
  report.timings.firstRenderMs = Date.now() - t0;
  report.timings.objLoadedMs = objAt || null;
  report.timings.plyLoadedMs = plyAt || null;
  log(`OK 首页真引擎出图: ${report.timings.firstRenderMs}ms (OBJ ${objAt}ms, PLY ${plyAt}ms)`);

  // 3) /inspection 三种模式切换时间
  await page.goto(`${BASE}/inspection`, { waitUntil: 'domcontentloaded' });
  await waitForEngine(page);
  const modes = ['实体网格', '点云云图', '融合叠加'];
  report.timings.modeSwitchMs = {};
  for (const m of modes) {
    const btn = page.getByRole('button', { name: m });
    const canvas = page.locator('.twin-adapter canvas').first();
    const before = await canvas.screenshot();
    const t = Date.now();
    await btn.click();
    // 等画面变化
    const deadline = Date.now() + 15_000;
    let changed = false;
    while (Date.now() < deadline) {
      await page.waitForTimeout(400);
      const after = await canvas.screenshot();
      if (!after.equals(before)) { changed = true; break; }
    }
    report.timings.modeSwitchMs[m] = Date.now() - t;
    log(`${changed ? 'OK' : '!!'} 模式切换 ${m}: ${report.timings.modeSwitchMs[m]}ms${changed ? '' : '（画面未变化）'}`);
  }

  // 4) 连续操作 soak
  log(`开始 ${SOAK_MINUTES} 分钟连续操作 soak ...`);
  const pages = ['/', '/inspection', '/playback', '/equipment', '/review', '/integration'];
  const soakEnd = Date.now() + SOAK_MINUTES * 60_000;
  let round = 0;
  while (Date.now() < soakEnd) {
    for (const p of pages) {
      if (Date.now() > soakEnd) break;
      await page.goto(`${BASE}${p}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(4000);
      // 轻微交互：点击页面中第一个可见按钮（若有）
      const btn = page.locator('button:visible').first();
      if (await btn.count()) await btn.click().catch(() => {});
      await page.waitForTimeout(2000);
    }
    round += 1;
    log(`soak 第 ${round} 轮完成，console/page 错误累计 ${report.errors.length}`);
  }

  report.finishedAt = new Date().toISOString();
  report.verdict = report.errors.length === 0 && !/swiftshader/i.test(gpu.renderer ?? '')
    ? '真实 GPU 验收通过'
    : '见下方问题清单';
  log(`结论: ${report.verdict}`);

  const md = [
    '# 真实 GPU 验收报告', '',
    `- 时间：${report.startedAt} → ${report.finishedAt}`,
    `- 环境：${report.env.os}，Chromium ${report.env.chromium}（headed）`,
    `- GPU vendor：${gpu.vendor}`,
    `- GPU renderer：${gpu.renderer}`,
    `- WebGL：${gpu.version ?? '不可用'}`, '',
    '## 性能记录', '',
    `- OBJ 首次加载：${report.timings.objLoadedMs} ms`,
    `- PLY 首次加载：${report.timings.plyLoadedMs} ms`,
    `- 首页真引擎出图：${report.timings.firstRenderMs} ms`,
    ...Object.entries(report.timings.modeSwitchMs).map(([k, v]) => `- 模式切换 ${k}：${v} ms`), '',
    `## 连续操作 ${SOAK_MINUTES} 分钟`, '',
    ...report.checks.filter((l) => l.startsWith('soak')), '',
    '## 错误清单', '',
    ...(report.errors.length ? report.errors : ['（无）']), '',
    `## 结论：${report.verdict}`, '',
  ].join('\n');
  writeFileSync(resolve(ROOT, 'docs/GPU_REPORT.md'), md);
  await browser.close();
  process.exit(report.errors.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(2); });
