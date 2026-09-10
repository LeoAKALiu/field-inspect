import type { RouteProvenance } from '@digital-twin/contracts';
import { CheckCircle2, CircleDashed, Route, Shapes } from 'lucide-react';

interface DataLineageProps {
  routeProvenance?: RouteProvenance;
  variant?: 'strip' | 'detail';
}

/** 展示公开几何、估计路线、模拟业务叠加和尚未接入的甲方数据。 */
export function DataLineage({ routeProvenance, variant = 'strip' }: DataLineageProps) {
  const routeIsEstimated = routeProvenance?.status !== 'measured';
  return (
    <div className={`data-lineage ${variant}`} aria-label="数据融合与血缘">
      <div className="lineage-title">
        <Shapes size={16} />
        <span>数据融合与血缘</span>
      </div>
      <div className="lineage-items">
        <div className="lineage-item confirmed">
          <CheckCircle2 size={15} />
          <span className="lineage-key">三维几何</span>
          <span className="lineage-value">LIRIS 公开 OBJ / PLY</span>
        </div>
        <div className="lineage-item estimated">
          <Route size={15} />
          <span className="lineage-key">巡检路线</span>
          <span className="lineage-value">
            {routeIsEstimated ? 'PLY 自动主廊道 · 算法估计' : '现场实测路线'}
          </span>
        </div>
        <div className="lineage-item simulated">
          <CircleDashed size={15} />
          <span className="lineage-key">车辆 / 设备 / 异常</span>
          <span className="lineage-value">模拟数据叠加</span>
        </div>
        <div className="lineage-item pending">
          <CircleDashed size={15} />
          <span className="lineage-key">甲方 GIS / BIM / 遥测</span>
          <span className="lineage-value">未提供 · 待接入</span>
        </div>
      </div>
      {variant === 'detail' && routeProvenance && (
        <p className="lineage-disclaimer">{routeProvenance.disclaimer}</p>
      )}
    </div>
  );
}
