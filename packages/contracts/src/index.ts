/** 契约包入口：领域对象 + TwinViewer 三维模块契约。 */

export * from './domain.js';
export * from './run-bundle.js';
export * from './inspection-package.js';
export * from './twin-viewer.js';

/**
 * 契约版本：
 * - CONTRACTS_VERSION：共享契约包整体版本。0.3.0 起包含 v0.3 领域扩展
 *   （仪器观测 / 定位 / 资产匹配 / 命名指标读数 / 巡检站与尝试结果 / 运行结果溯源），
 *   v2 领域对象保持冻结、纯增量扩展。
 * - DOMAIN_CONTRACT_VERSION：v0.3 领域对象载荷内的 contract_version 常量。
 */
export const CONTRACTS_VERSION = '0.3.0';
export const DOMAIN_CONTRACT_VERSION = '0.3';
export const RUN_BUNDLE_CONTRACT_VERSION = '1.0';
export const INSPECTION_PACKAGE_CONTRACT_VERSION = '2.0';
