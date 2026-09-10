import React from 'react';
import { Box, Loader2 } from 'lucide-react';

interface Props {
  state: 'loading' | 'unavailable';
  height?: number | string;
}

/**
 * MockViewer — 三维引擎加载中 / 不可用时的兜底占位视图。
 * 保证任何情况下三维区域不白屏。
 */
export const MockViewer: React.FC<Props> = ({ state, height = '100%' }) => {
  return (
    <div className="mock-viewer" style={{ height }}>
      <svg
        className="mock-viewer-graphic"
        viewBox="0 0 200 120"
        role="img"
        aria-label="隧道剖面示意"
      >
        <path
          d="M20 110 L20 60 A80 55 0 0 1 180 60 L180 110"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
        <line x1="10" y1="110" x2="190" y2="110" stroke="currentColor" strokeWidth="2" />
        <circle cx="100" cy="78" r="6" fill="currentColor" opacity="0.5" />
        <line x1="40" y1="110" x2="70" y2="88" stroke="currentColor" strokeWidth="1" opacity="0.4" />
        <line x1="160" y1="110" x2="130" y2="88" stroke="currentColor" strokeWidth="1" opacity="0.4" />
      </svg>
      {state === 'loading' ? (
        <div className="mock-viewer-text">
          <Loader2 size={18} className="animate-spin" />
          <span>三维引擎加载中…</span>
        </div>
      ) : (
        <div className="mock-viewer-text">
          <Box size={18} />
          <span>三维引擎暂不可用，已切换为占位视图</span>
        </div>
      )}
    </div>
  );
};
