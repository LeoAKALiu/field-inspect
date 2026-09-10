import { inflateSync } from 'node:zlib';
import { expect, type Page } from '@playwright/test';

/**
 * e2e 共享工具：
 * - probeConsole：收集 console.error / pageerror，按白名单豁免 WebGL 等环境噪音；
 * - waitForViewer：等待三维区域就绪，返回实际渲染路径（真引擎 canvas 或 MockViewer 兜底）。
 */

/** headless chromium（SwiftShader 软渲染）下可豁免的控制台错误模式。 */
const CONSOLE_ERROR_WHITELIST: RegExp[] = [
  /webgl/i,
  /swiftshader/i,
  /automatic fallback to software/i,
  /gpu process/i,
  /three\./i,
  /favicon/i,
];

export interface ConsoleProbe {
  /** 全部原始错误（未过滤），用于失败时输出诊断。 */
  raw: string[];
  attach(page: Page): void;
  /** 断言：白名单之外不得有 console 错误。 */
  assertClean(): void;
}

export function probeConsole(): ConsoleProbe {
  const raw: string[] = [];
  return {
    raw,
    attach(page: Page) {
      page.on('console', (msg) => {
        if (msg.type() === 'error') raw.push(`[console.error] ${msg.text()}`);
      });
      page.on('pageerror', (err) => raw.push(`[pageerror] ${err.message}`));
    },
    assertClean() {
      const blocking = raw.filter((e) => !CONSOLE_ERROR_WHITELIST.some((re) => re.test(e)));
      expect(
        blocking,
        `存在非白名单控制台错误（白名单仅豁免 WebGL/favicon 环境噪音）:\n${blocking.join('\n')}`,
      ).toEqual([]);
    },
  };
}

export type ViewerPath = 'engine' | 'mock';

/** 本次运行各页面实际走的三维渲染路径（用于报告与 KNOWN_ISSUES 记录）。 */
export const viewerPathLog: Array<{ page: string; path: ViewerPath }> = [];

/**
 * 等待三维区域稳定：真引擎渲染出 canvas，或引擎初始化失败降级为
 * MockViewer「暂不可用」占位。二者必有其一，并记录实际路径。
 */
export async function waitForViewer(page: Page, label: string): Promise<ViewerPath> {
  const canvas = page.locator('.twin-adapter canvas').first();
  const mock = page.locator('.mock-viewer', { hasText: '三维引擎暂不可用' }).first();

  const engineP = canvas
    .waitFor({ state: 'visible', timeout: 60_000 })
    .then(() => 'engine' as const)
    .catch(() => null);
  const mockP = mock
    .waitFor({ state: 'visible', timeout: 60_000 })
    .then(() => 'mock' as const)
    .catch(() => null);

  const winner = await Promise.race([engineP, mockP]);
  if (!winner) {
    throw new Error(
      `[${label}] 三维区域既未出现引擎 canvas，也未出现 MockViewer 兜底（30s 超时）`,
    );
  }
  viewerPathLog.push({ page: label, path: winner });
  console.log(`[viewer-path] ${label}: ${winner === 'engine' ? '真引擎 canvas' : 'MockViewer 兜底'}`);

  if (winner === 'engine') {
    await expect(canvas).toBeVisible();
  } else {
    await expect(mock).toBeVisible();
  }
  return winner;
}

/* ============================================================================
 * engine-only 正式验收专用（engine-acceptance / screenshots 两个 spec）
 * ========================================================================== */

/**
 * 收紧版控制台白名单：engine-only 验收只允许 favicon 与明确的 SwiftShader
 * 性能类警告；three.js 错误、WebGL context 创建失败一律不豁免（真引擎报错即失败）。
 */
const CONSOLE_ERROR_WHITELIST_STRICT: RegExp[] = [
  /favicon/i,
  /automatic fallback to software/i,
  /GroupMarkerNotSet/i,
];

export function probeConsoleStrict(): ConsoleProbe {
  const raw: string[] = [];
  return {
    raw,
    attach(page: Page) {
      page.on('console', (msg) => {
        if (msg.type() === 'error') raw.push(`[console.error] ${msg.text()}`);
      });
      page.on('pageerror', (err) => raw.push(`[pageerror] ${err.message}`));
    },
    assertClean() {
      const blocking = raw.filter(
        (e) => !CONSOLE_ERROR_WHITELIST_STRICT.some((re) => re.test(e)),
      );
      expect(
        blocking,
        `engine-only 验收存在控制台错误（严格白名单仅豁免 favicon/SwiftShader 性能警告）:\n${blocking.join('\n')}`,
      ).toEqual([]);
    },
  };
}

/**
 * 硬性要求真引擎：canvas 必须出现；一旦出现 MockViewer「暂不可用」降级，
 * 或超时无 canvas，立即失败（不走双路径兜底）。
 * 注意：引擎加载期间会短暂出现「三维引擎加载中…」的 loading 占位，
 * 只有 unavailable 态的 MockViewer 才视为失败。
 */
export async function requireEngineCanvas(page: Page, label: string): Promise<void> {
  const mockDown = page.locator('.mock-viewer', { hasText: '三维引擎暂不可用' });
  const canvas = page.locator('.twin-adapter canvas').first();

  const canvasP = canvas
    .waitFor({ state: 'visible', timeout: 60_000 })
    .then(() => 'ok' as const)
    .catch(() => null);
  const mockP = mockDown
    .first()
    .waitFor({ state: 'visible', timeout: 60_000 })
    .then(() => 'mock' as const)
    .catch(() => null);

  const winner = await Promise.race([canvasP, mockP]);
  if (winner === 'mock') {
    throw new Error(`[${label}] 三维引擎降级为 MockViewer（暂不可用），engine-only 验收判定失败`);
  }
  if (!winner) {
    throw new Error(`[${label}] 60s 内未出现 .twin-adapter canvas，engine-only 验收判定失败`);
  }
  // canvas 出现后仍可能有别的视口降级为 MockViewer（如多视口页面）
  await expect(mockDown, `[${label}] 页面存在 MockViewer 降级占位`).toHaveCount(0);
  viewerPathLog.push({ page: label, path: 'engine' });
}

/**
 * 视口截图的字节数阈值（按像素面积缩放，上限 30KB）：
 * 均匀黑屏 PNG 压缩后仅百余字节；大视口出图后轻松超过 30KB，
 * 小视口（如设备页 378×221）出图约 28KB，绝对阈值会误判，故按面积缩放。
 */
export function pngByteThreshold(width: number, height: number): number {
  return Math.min(30_000, Math.round(width * height * 0.15));
}

/**
 * 等引擎真正出图（非纯黑/空画布）。双重判定的原理：
 * 1) PNG 字节数阈值（按视口面积缩放，见 pngByteThreshold）—— 黑屏 PNG 仅百余字节；
 * 2) 抽样像素亮度方差 —— 解码 PNG 后计算亮度标准差，黑屏/纯色方差≈0，
 *    真实三维场景（暗背景 + 亮色几何体/标注）方差显著大于阈值。
 */
export async function waitForEngineRendered(page: Page, label: string): Promise<void> {
  await requireEngineCanvas(page, label);
  const viewport = page.locator('.twin-adapter').first();
  let stats: PngStats | null = null;
  let byteLen = 0;
  await expect
    .poll(
      async () => {
        const buf = await viewport.screenshot();
        byteLen = buf.length;
        stats = analyzePng(buf);
        // 字节数与像素方差都达标才算出图
        return byteLen > pngByteThreshold(stats.width, stats.height) ? stats.lumaStd : 0;
      },
      {
        timeout: 60_000,
        intervals: [1_000, 2_000, 3_000, 5_000],
        message: `[${label}] 三维视口长时间为纯黑/空画布（字节数或像素方差不达标）`,
      },
    )
    .toBeGreaterThan(5);
  console.log(
    `[engine-rendered] ${label}: bytes=${byteLen} lumaStd=${stats!.lumaStd.toFixed(1)} meanLuma=${stats!.meanLuma.toFixed(1)}`,
  );
}

/* ----------------------------------------------------------------------------
 * 极简 PNG 解析（无第三方依赖）：读 IHDR 尺寸 + inflate IDAT + 反滤波，
 * 仅支持 Playwright 截图产物（8-bit、非隔行、RGB/RGBA）。
 * -------------------------------------------------------------------------- */

export interface PngStats {
  width: number;
  height: number;
  /** 抽样像素亮度（Rec.601）标准差 */
  lumaStd: number;
  /** 抽样像素平均亮度 */
  meanLuma: number;
}

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

export function analyzePng(buf: Buffer): PngStats {
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  const bitDepth = buf[24]!;
  const colorType = buf[25]!;
  const interlace = buf[28]!;
  if (bitDepth !== 8 || interlace !== 0 || (colorType !== 6 && colorType !== 2)) {
    throw new Error(`不支持的 PNG 格式: bitDepth=${bitDepth} colorType=${colorType} interlace=${interlace}`);
  }
  const channels = colorType === 6 ? 4 : 3;

  // 收集 IDAT 并解压
  const idat: Buffer[] = [];
  let offset = 8;
  while (offset + 8 <= buf.length) {
    const len = buf.readUInt32BE(offset);
    const type = buf.toString('ascii', offset + 4, offset + 8);
    if (type === 'IDAT') idat.push(buf.subarray(offset + 8, offset + 8 + len));
    offset += 12 + len;
  }
  const raw = inflateSync(Buffer.concat(idat));

  // 反滤波并抽样亮度（每 8 个像素取 1 个，足够区分黑屏与真实场景）
  const stride = width * channels;
  const prev = new Uint8Array(stride);
  const line = new Uint8Array(stride);
  let sum = 0;
  let sumSq = 0;
  let count = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]!;
    const src = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const v = src[x]!;
      const a = x >= channels ? line[x - channels]! : 0;
      const b = prev[x]!;
      const c = x >= channels ? prev[x - channels]! : 0;
      let val: number;
      switch (filter) {
        case 0: val = v; break;
        case 1: val = v + a; break;
        case 2: val = v + b; break;
        case 3: val = v + ((a + b) >> 1); break;
        default: val = v + paeth(a, b, c); break;
      }
      line[x] = val & 0xff;
    }
    if (y % 2 === 0) {
      for (let x = 0; x < width; x += 8) {
        const r = line[x * channels]!;
        const g = line[x * channels + 1]!;
        const bch = line[x * channels + 2]!;
        const luma = 0.299 * r + 0.587 * g + 0.114 * bch;
        sum += luma;
        sumSq += luma * luma;
        count++;
      }
    }
    prev.set(line);
  }
  const mean = count > 0 ? sum / count : 0;
  const variance = count > 0 ? sumSq / count - mean * mean : 0;
  return { width, height, lumaStd: Math.sqrt(Math.max(variance, 0)), meanLuma: mean };
}
