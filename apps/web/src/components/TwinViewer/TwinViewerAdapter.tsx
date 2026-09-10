import React, { Component, Suspense, lazy, useCallback, useState, type ReactNode } from 'react';
import type { TwinViewerProps } from '@digital-twin/contracts';
import { MockViewer } from './MockViewer';

/**
 * TwinViewerAdapter — 契约 v2 TwinViewerProps 的唯一入口组件。
 *
 * - 通过动态 import 懒加载三维引擎（@digital-twin/twin-viewer），
 *   引擎代码独立分包，不拖慢首屏；
 * - ErrorBoundary + onViewerError 双重兜底：引擎 import 失败或运行时
 *   报错时降级为 MockViewer 占位面板，保证三维区域不白屏；
 * - 坐标转换（scene_local_yup → 引擎坐标）集中在 EngineScene 一处，
 *   见 EngineScene.tsx 顶部注释。
 */
const LazyEngineScene = lazy(() => import('./EngineScene'));

interface BoundaryProps {
  fallback: ReactNode;
  children: ReactNode;
}

interface BoundaryState {
  hasError: boolean;
}

class EngineErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { hasError: false };

  static getDerivedStateFromError(): BoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown): void {
    console.warn('[TwinViewerAdapter] 三维引擎运行异常，已降级为占位视图', error);
  }

  render(): ReactNode {
    return this.state.hasError ? this.props.fallback : this.props.children;
  }
}

export const TwinViewerAdapter: React.FC<TwinViewerProps> = (props) => {
  const [engineFailed, setEngineFailed] = useState(false);

  const handleViewerError = useCallback(
    (error: { code: string; message: string }) => {
      setEngineFailed(true);
      props.onViewerError?.(error);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.onViewerError],
  );

  if (engineFailed) {
    return <MockViewer state="unavailable" height={props.height} />;
  }

  return (
    <EngineErrorBoundary fallback={<MockViewer state="unavailable" height={props.height} />}>
      <Suspense fallback={<MockViewer state="loading" height={props.height} />}>
        <LazyEngineScene {...props} onViewerError={handleViewerError} />
      </Suspense>
    </EngineErrorBoundary>
  );
};
