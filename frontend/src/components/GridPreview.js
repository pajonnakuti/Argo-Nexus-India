import React, { useMemo } from 'react';

/**
 * GridPreview — Displays gridded data product as an overlay panel
 * with a color-mapped heatmap table, statistics, and download controls.
 */
const GridPreview = ({ gridData, onClose, onDownload }) => {
  // Build a color palette for the heatmap (must be before early return — React hook rules)
  const colorScale = useMemo(() => {
    const s = gridData?.stats;
    if (!s || s.min == null || s.max == null) return { getColor: () => 'rgba(200,200,200,0.3)', stops: null };
    const min = s.min;
    const max = s.max;
    const range = max - min || 1;

    // Professional ocean science color ramp (blue → cyan → yellow → red)
    const stops = [
      [0.0,  '#08306b'],
      [0.15, '#2171b5'],
      [0.30, '#4292c6'],
      [0.45, '#6baed6'],
      [0.55, '#74c476'],
      [0.65, '#fcbf49'],
      [0.80, '#f46d43'],
      [0.90, '#d73027'],
      [1.0,  '#a50026'],
    ];

    const lerp = (a, b, t) => a + (b - a) * t;
    const hexToRgb = (hex) => {
      const m = hex.match(/^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
      return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [128, 128, 128];
    };

    const getColor = (val) => {
      if (val == null) return 'rgba(200,200,200,0.06)';
      const t = Math.max(0, Math.min(1, (val - min) / range));
      let lower = stops[0], upper = stops[stops.length - 1];
      for (let i = 0; i < stops.length - 1; i++) {
        if (t >= stops[i][0] && t <= stops[i + 1][0]) {
          lower = stops[i];
          upper = stops[i + 1];
          break;
        }
      }
      const segT = (t - lower[0]) / (upper[0] - lower[0] || 1);
      const c1 = hexToRgb(lower[1]);
      const c2 = hexToRgb(upper[1]);
      const r = Math.round(lerp(c1[0], c2[0], segT));
      const g = Math.round(lerp(c1[1], c2[1], segT));
      const b = Math.round(lerp(c1[2], c2[2], segT));
      return `rgb(${r},${g},${b})`;
    };

    return { getColor, min, max, stops };
  }, [gridData]);

  if (!gridData) return null;

  const { variable, depth_level, resolution, method, n_observations, n_profiles, lats, lons, grid, bounds, stats } = gridData;

  const methodLabels = { oi: 'Optimal Interpolation', linear: 'Linear', nearest: 'Nearest Neighbor' };
  const unitMap = { TEMP: '°C', PSAL: 'PSU', DOXY: 'µmol/kg', CHLA: 'mg/m³', NITRATE: 'µmol/kg', PH: '', BBP700: '1/m', PRES: 'dbar' };
  const unit = unitMap[variable] || '';

  // Determine display grid size (truncate for performance)
  const maxCols = Math.min(lons.length, 60);
  const maxRows = Math.min(lats.length, 40);
  const colStep = Math.max(1, Math.floor(lons.length / maxCols));
  const rowStep = Math.max(1, Math.floor(lats.length / maxRows));

  return (
    <div className="grid-preview-overlay">
      <div className="grid-preview-panel">

        {/* Header */}
        <div className="grid-preview-header">
          <div className="grid-preview-title-area">
            <div className="grid-preview-icon">🗺️</div>
            <div>
              <h3 className="grid-preview-title">Gridded {variable}</h3>
              <span className="grid-preview-subtitle">
                {depth_level}m depth · {resolution}° · {methodLabels[method] || method}
              </span>
            </div>
          </div>
          <button className="grid-preview-close" onClick={onClose} title="Close">✕</button>
        </div>

        {/* Stats row */}
        <div className="grid-preview-stats">
          <div className="gp-stat">
            <span className="gp-stat-label">Observations</span>
            <span className="gp-stat-value">{n_observations?.toLocaleString()}</span>
          </div>
          <div className="gp-stat">
            <span className="gp-stat-label">Grid Size</span>
            <span className="gp-stat-value">{lats.length} × {lons.length}</span>
          </div>
          <div className="gp-stat">
            <span className="gp-stat-label">Min</span>
            <span className="gp-stat-value">{stats?.min != null ? stats.min.toFixed(2) : '—'}{unit && ` ${unit}`}</span>
          </div>
          <div className="gp-stat">
            <span className="gp-stat-label">Max</span>
            <span className="gp-stat-value">{stats?.max != null ? stats.max.toFixed(2) : '—'}{unit && ` ${unit}`}</span>
          </div>
          <div className="gp-stat">
            <span className="gp-stat-label">Mean</span>
            <span className="gp-stat-value">{stats?.mean != null ? stats.mean.toFixed(2) : '—'}{unit && ` ${unit}`}</span>
          </div>
        </div>

        {/* Heatmap */}
        <div className="grid-heatmap-container">
          <div className="grid-heatmap-scroll">
            <table className="grid-heatmap-table">
              <tbody>
                {Array.from({ length: Math.ceil(lats.length / rowStep) }).map((_, ri) => {
                  const i = Math.min(ri * rowStep, lats.length - 1);
                  return (
                    <tr key={i}>
                      <td className="heatmap-label-y">{lats[lats.length - 1 - i]?.toFixed(1)}°</td>
                      {Array.from({ length: Math.ceil(lons.length / colStep) }).map((_, ci) => {
                        const j = Math.min(ci * colStep, lons.length - 1);
                        const val = grid[lats.length - 1 - i]?.[j];
                        return (
                          <td
                            key={j}
                            className="heatmap-cell"
                            style={{ backgroundColor: colorScale.getColor(val) }}
                            title={val != null ? `${lats[lats.length - 1 - i]?.toFixed(2)}°N, ${lons[j]?.toFixed(2)}°E: ${val.toFixed(3)} ${unit}` : 'No data'}
                          />
                        );
                      })}
                    </tr>
                  );
                })}
                {/* X-axis labels */}
                <tr className="heatmap-x-labels">
                  <td />
                  {Array.from({ length: Math.ceil(lons.length / colStep) }).map((_, ci) => {
                    const j = Math.min(ci * colStep, lons.length - 1);
                    return ci % 3 === 0 ? (
                      <td key={j} className="heatmap-label-x">{lons[j]?.toFixed(1)}°</td>
                    ) : (
                      <td key={j} />
                    );
                  })}
                </tr>
              </tbody>
            </table>
          </div>

          {/* Color legend */}
          <div className="grid-color-legend">
            <div className="legend-bar">
              {colorScale.stops && colorScale.stops.map((s, i) => (
                <div key={i} className="legend-segment" style={{ backgroundColor: s[1], flex: 1 }} />
              ))}
            </div>
            <div className="legend-labels">
              <span>{stats?.min != null ? stats.min.toFixed(1) : ''}</span>
              <span>{stats?.mean != null ? stats.mean.toFixed(1) : ''}</span>
              <span>{stats?.max != null ? stats.max.toFixed(1) : ''}</span>
            </div>
            <div className="legend-unit">{variable} {unit && `(${unit})`}</div>
          </div>
        </div>

        {/* Download */}
        <button className="grid-download-btn" onClick={onDownload}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          Download as NetCDF
        </button>
      </div>
    </div>
  );
};

export default GridPreview;
