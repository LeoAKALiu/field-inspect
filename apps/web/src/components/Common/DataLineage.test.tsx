import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { DataLineage } from './DataLineage';

describe('DataLineage', () => {
  it('separates public geometry, estimated route, simulated overlays and pending client data', () => {
    const html = renderToStaticMarkup(
      <DataLineage
        variant="detail"
        routeProvenance={{
          method: 'pointcloud_auto_corridor_centerline',
          algorithm_version: '2.0.0',
          source_asset: 'tunnel_pointcloud.ply',
          source_sha256: 'hash',
          source_point_count: 162594,
          generated_point_count: 25,
          status: 'estimated',
          disclaimer: '算法估计，非实测路线',
        }}
      />,
    );

    expect(html).toContain('LIRIS 公开 OBJ / PLY');
    expect(html).toContain('PLY 自动主廊道 · 算法估计');
    expect(html).toContain('模拟数据叠加');
    expect(html).toContain('未提供 · 待接入');
    expect(html).toContain('算法估计，非实测路线');
  });
});
