import React, { useState } from 'react';

const coreVars = ['TEMP', 'PSAL', 'PRES', 'All QC Flags', 'All Available Parameters'];
const bioVars = ['CHLA', 'DOXY', 'NITRATE', 'PH', 'BBP700', 'IRRADIANCE', 'PRES', 'All QC Flags', 'All Available Parameters'];

const gridVars = ['TEMP', 'PSAL', 'DOXY', 'CHLA', 'NITRATE', 'PH', 'BBP700'];
const resolutionOptions = [0.25, 0.5, 1.0];
const methodOptions = [
  { value: 'oi', label: 'Optimal Interpolation' },
  { value: 'linear', label: 'Linear' },
  { value: 'nearest', label: 'Nearest Neighbor' },
];

const Sidebar = ({
  bounds, params, setParams,
  onSubmit, onBoundsChange, floatCounts,
  downloadFormat, setDownloadFormat,
  gridConfig, setGridConfig,
  onGridSubmit, gridLoading,
  onExport
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const [gridPanelOpen, setGridPanelOpen] = useState(false);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setParams({ ...params, [name]: value });
  };

  const handleTypeChange = (type) => {
    setParams({ ...params, type, selectedVars: type === 'core' ? coreVars : bioVars });
  };

  const currentVars = params.type === 'core' ? coreVars : bioVars;
  const selectedCount = params.selectedVars?.length || 0;

  const handleVarToggle = (v) => {
    const currentSelected = params.selectedVars || [];
    const isSelected = currentSelected.includes(v);
    if (isSelected) {
      setParams({ ...params, selectedVars: currentSelected.filter(item => item !== v) });
    } else {
      setParams({ ...params, selectedVars: [...currentSelected, v] });
    }
  };

  const handleSelectAll = (e) => {
    e.preventDefault();
    setParams({ ...params, selectedVars: currentVars });
  };

  const handleClearAll = (e) => {
    e.preventDefault();
    setParams({ ...params, selectedVars: [] });
  };

  const handleBoundsChange = (e) => {
    const { name, value } = e.target;
    if (value === '') {
      onBoundsChange({ ...bounds, [name]: '' });
      return;
    }
    const numValue = parseFloat(value);
    if (!isNaN(numValue)) {
      onBoundsChange({ ...bounds, [name]: numValue });
    }
  };

  return (
    <div className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <button
        className="sidebar-toggle"
        onClick={() => setCollapsed(prev => !prev)}
        title="Toggle Sidebar"
      >
        <span />
        <span />
        <span />
      </button>

      <div className="sidebar-content">
        <div className="header">
          <img 
            src={`${process.env.PUBLIC_URL}/logo-incois-argo.png`} 
            alt="INCOIS Argo Nexus" 
            className="nav-logo"
          />
          <div className="app-title-display">
            <span className="app-title-argo">ARGO</span>
            <span className="app-title-nexus">NEXUS</span>
          </div>
          <div className="status-badge">
            <div className="status-dot"></div>
            Live Connection
          </div>
        </div>

        {/* Mini Status Block */}
        <div className="mini-status">
          <div className="stat-block">
            <div className="stat-label">ACTIVE<br/>FLOATS</div>
            <div className="stat-value">{floatCounts?.total?.toLocaleString() || '—'}</div>
          </div>
          <div className="stat-block">
            <div className="stat-label">CORE</div>
            <div className="stat-value core">{floatCounts?.core?.toLocaleString() || '—'}</div>
          </div>
          <div className="stat-block">
            <div className="stat-label">BGC</div>
            <div className="stat-value bgc">{floatCounts?.bgc?.toLocaleString() || '—'}</div>
          </div>
        </div>

        <div className="section">
        <div className="section-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
            <circle cx="12" cy="10" r="3" />
          </svg>
          Target Zone
        </div>
        <div className="grid-2">
          <div className="input-group">
            <span className="input-label">North</span>
            <input
              type="number"
              step="0.0001"
              name="north"
              className="input-field"
              value={bounds?.north || ''}
              onChange={handleBoundsChange}
              placeholder="0.0000"
            />
          </div>
          <div className="input-group">
            <span className="input-label">South</span>
            <input
              type="number"
              step="0.0001"
              name="south"
              className="input-field"
              value={bounds?.south || ''}
              onChange={handleBoundsChange}
              placeholder="0.0000"
            />
          </div>
        </div>
        <div className="grid-2">
          <div className="input-group">
            <span className="input-label">West</span>
            <input
              type="number"
              step="0.0001"
              name="west"
              className="input-field"
              value={bounds?.west || ''}
              onChange={handleBoundsChange}
              placeholder="0.0000"
            />
          </div>
          <div className="input-group">
            <span className="input-label">East</span>
            <input
              type="number"
              step="0.0001"
              name="east"
              className="input-field"
              value={bounds?.east || ''}
              onChange={handleBoundsChange}
              placeholder="0.0000"
            />
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
            <line x1="16" y1="2" x2="16" y2="6" />
            <line x1="8" y1="2" x2="8" y2="6" />
            <line x1="3" y1="10" x2="21" y2="10" />
          </svg>
          Temporal Range
        </div>
        <div className="grid-2">
          <div className="input-group">
            <span className="input-label">Start Date</span>
            <input
              type="date"
              name="startDate"
              className="input-field"
              value={params.startDate}
              onChange={handleChange}
            />
          </div>
          <div className="input-group">
            <span className="input-label">End Date</span>
            <input
              type="date"
              name="endDate"
              className="input-field"
              value={params.endDate}
              onChange={handleChange}
            />
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-title">
          Depth Range (Meters)
        </div>
        <div className="grid-2">
          <div className="input-group">
            <span className="input-label">Min</span>
            <input
              type="number"
              name="minDepth"
              className="input-field"
              value={params.minDepth}
              onChange={handleChange}
              placeholder="0"
            />
          </div>
          <div className="input-group">
            <span className="input-label">Max</span>
            <input
              type="number"
              name="maxDepth"
              className="input-field"
              value={params.maxDepth}
              onChange={handleChange}
              placeholder="2000"
            />
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-title">
          Search Type
        </div>
        <div className="type-selector">
          <button
            className={`type-btn ${params.type === 'core' ? 'active' : ''}`}
            onClick={() => handleTypeChange('core')}
          >
            Core Argo
          </button>
          <button
            className={`type-btn ${params.type === 'bio' ? 'active' : ''}`}
            onClick={() => handleTypeChange('bio')}
          >
            BGC Argo
          </button>
        </div>
      </div>

      <div className="section">
        <div className="section-header-row">
          <div className="section-title-inline">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
            </svg>
            Parameters
            <span className="count-badge">{selectedCount}/{currentVars.length}</span>
          </div>
          <div className="section-actions">
            <a href="#" onClick={handleSelectAll}>All</a> / <a href="#" onClick={handleClearAll}>Clear</a>
          </div>
        </div>
        <div className="variables">
          {currentVars.map(v => {
            const isActive = params.selectedVars?.includes(v) || false;
            return (
              <div 
                key={v} 
                className={`var-chip clickable ${isActive ? 'active' : ''}`}
                onClick={() => handleVarToggle(v)}
              >
                <div className={`checkbox ${isActive ? 'checked' : ''}`}>
                  {isActive && (
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                </div>
                {v}
              </div>
            );
          })}
        </div>
        <div className="info-note">
          ℹ️ All available parameters from NetCDF files will be exported
        </div>
      </div>

      {/* ── Download Format Selector ── */}
      <div className="section">
        <div className="section-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
          Export Format
        </div>
        <div className="format-selector">
          {['csv', 'json', 'netcdf'].map(fmt => (
            <button
              key={fmt}
              className={`format-btn ${downloadFormat === fmt ? 'active' : ''}`}
              onClick={() => setDownloadFormat(fmt)}
            >
              {fmt === 'netcdf' ? 'NetCDF' : fmt.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <button className="btn-primary" onClick={onSubmit}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
        {'Export as ' + (downloadFormat === 'netcdf' ? 'NetCDF' : downloadFormat.toUpperCase())}
      </button>

      {/* ── Section Divider ── */}
      <div className="sidebar-divider" />

      {/* ── Gridded Data Product ── */}
      <div className="section">
        <div
          className="section-title collapsible"
          onClick={() => setGridPanelOpen(prev => !prev)}
          style={{ cursor: 'pointer', userSelect: 'none' }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="7" height="7" />
            <rect x="14" y="3" width="7" height="7" />
            <rect x="14" y="14" width="7" height="7" />
            <rect x="3" y="14" width="7" height="7" />
          </svg>
          Gridded Product (OI)
          <span className={`collapse-arrow ${gridPanelOpen ? 'open' : ''}`}>&#9656;</span>
        </div>

        {gridPanelOpen && (
          <div className="grid-panel-body">
            {/* Variable */}
            <div className="input-group">
              <span className="input-label">Variable</span>
              <select
                className="input-field"
                value={gridConfig.variable}
                onChange={e => setGridConfig({ ...gridConfig, variable: e.target.value })}
              >
                {gridVars.map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>

            {/* Depth + Tolerance */}
            <div className="grid-2">
              <div className="input-group">
                <span className="input-label">Depth (m)</span>
                <input
                  type="number"
                  className="input-field"
                  value={gridConfig.depth_level}
                  onChange={e => setGridConfig({ ...gridConfig, depth_level: parseFloat(e.target.value) || 0 })}
                />
              </div>
              <div className="input-group">
                <span className="input-label">Tolerance (±m)</span>
                <input
                  type="number"
                  className="input-field"
                  value={gridConfig.depth_tolerance}
                  onChange={e => setGridConfig({ ...gridConfig, depth_tolerance: parseFloat(e.target.value) || 50 })}
                />
              </div>
            </div>

            {/* Resolution */}
            <div className="input-group">
              <span className="input-label">Grid Resolution</span>
              <div className="format-selector">
                {resolutionOptions.map(r => (
                  <button
                    key={r}
                    className={`format-btn ${gridConfig.resolution === r ? 'active' : ''}`}
                    onClick={() => setGridConfig({ ...gridConfig, resolution: r })}
                  >
                    {r}°
                  </button>
                ))}
              </div>
            </div>

            {/* Method */}
            <div className="input-group">
              <span className="input-label">Interpolation Method</span>
              <select
                className="input-field"
                value={gridConfig.method}
                onChange={e => setGridConfig({ ...gridConfig, method: e.target.value })}
              >
                {methodOptions.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </div>

            {/* OI-only params */}
            {gridConfig.method === 'oi' && (
              <div className="grid-2">
                <div className="input-group">
                  <span className="input-label">Corr. Length (°)</span>
                  <input
                    type="number"
                    step="0.5"
                    className="input-field"
                    value={gridConfig.corr_length}
                    onChange={e => setGridConfig({ ...gridConfig, corr_length: parseFloat(e.target.value) || 2.0 })}
                  />
                </div>
                <div className="input-group">
                  <span className="input-label">SNR</span>
                  <input
                    type="number"
                    step="0.1"
                    className="input-field"
                    value={gridConfig.snr}
                    onChange={e => setGridConfig({ ...gridConfig, snr: parseFloat(e.target.value) || 1.0 })}
                  />
                </div>
              </div>
            )}

            <button
              className="btn-grid-generate"
              onClick={onGridSubmit}
              disabled={gridLoading}
            >
              {gridLoading ? (
                <>
                  <span className="btn-spinner" />
                  Generating...
                </>
              ) : (
                <>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="7" height="7" />
                    <rect x="14" y="3" width="7" height="7" />
                    <rect x="14" y="14" width="7" height="7" />
                    <rect x="3" y="14" width="7" height="7" />
                  </svg>
                  Generate Gridded Product
                </>
              )}
            </button>
          </div>
        )}
      </div>

      <div className="info-text">
        System 100% Optimized • Cost: ₹0 • Automated Retrieval
      </div>
      </div>
    </div>
  );
};

export default Sidebar;
