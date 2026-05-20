import React, { useRef, useEffect, useMemo, useCallback, useState } from 'react';
import Globe from 'react-globe.gl';

const OCEAN_LABELS = {'I': 'Indian', 'P': 'Pacific', 'A': 'Atlantic', '': 'Unknown'};

const GlobeView = ({ 
    activeFloats, 
    showActive, 
    showInactive,
    floatFilter,
    oceanFilter,
    instFilter, 
    selectedFloat, 
    trajectoryPoints,
    onFloatClick,
    bounds,
    onBoundsChange
}) => {
  const globeRef = useRef();
  const [globeReady, setGlobeReady] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);

  // Bounding box drawing state
  const [isDrawingMode, setIsDrawingMode] = useState(false);
  const [firstCorner, setFirstCorner] = useState(null);
  const [previewCorner, setPreviewCorner] = useState(null);

  // Set initial view to India on load
  useEffect(() => {
    if (globeRef.current && globeReady) {
      setIsTransitioning(true);
      globeRef.current.pointOfView({ lat: 20, lng: 78, altitude: 2.5 }, 1000);
      setTimeout(() => setIsTransitioning(false), 1100);
    }
  }, [globeReady]);

  // Fly to selected float
  useEffect(() => {
    if (selectedFloat && globeRef.current) {
      setIsTransitioning(true);
      globeRef.current.pointOfView(
        { lat: selectedFloat.lat, lng: selectedFloat.lon, altitude: 1.2 },
        1200
      );
      setTimeout(() => setIsTransitioning(false), 1300);
    }
  }, [selectedFloat]);

  // Filter floats — same logic as 2D map
  const filteredFloats = useMemo(() => {
    if (!showActive || !activeFloats) return [];
    return activeFloats.filter(f => {
      if (floatFilter !== 'all' && f.type !== floatFilter) return false;
      if (!showInactive && f.status === 'inactive') return false;
      if (oceanFilter !== 'all') {
        if ((OCEAN_LABELS[f.ocean] || f.ocean) !== oceanFilter) return false;
      }
      if (instFilter !== 'all' && f.institution !== instFilter) return false;
      return true;
    });
  }, [activeFloats, showActive, showInactive, floatFilter, oceanFilter, instFilter]);

  // Point color based on type and selection
  const pointColor = useCallback((d) => {
    if (selectedFloat?.platform === d.platform) return '#f97316'; // orange for selected
    if (d.status === 'inactive') return '#94a3b8';
    return d.type === 'bgc' ? '#c084fc' : '#facc15'; // purple for BGC, yellow for core
  }, [selectedFloat]);

  const pointAltitude = useCallback((d) => {
    return selectedFloat?.platform === d.platform ? 0.04 : 0.01;
  }, [selectedFloat]);

  const pointRadius = useCallback((d) => {
    if (selectedFloat?.platform === d.platform) return 0.6;
    if (d.status === 'inactive') return 0.2;
    return 0.3;
  }, [selectedFloat]);

  // Trajectory as arcs between consecutive points — renders cleanly on the globe
  const arcData = useMemo(() => {
    if (!trajectoryPoints || trajectoryPoints.length < 2) return [];
    const arcs = [];
    for (let i = 0; i < trajectoryPoints.length - 1; i++) {
      const from = trajectoryPoints[i];
      const to = trajectoryPoints[i + 1];
      if (from.lat != null && from.lon != null && to.lat != null && to.lon != null) {
        arcs.push({
          startLat: from.lat,
          startLng: from.lon,
          endLat: to.lat,
          endLng: to.lon,
          cycle: to.cycle
        });
      }
    }
    return arcs;
  }, [trajectoryPoints]);

  // Trajectory waypoint labels (shown as HTML labels on the globe)
  const labelData = useMemo(() => {
    if (!trajectoryPoints) return [];
    return trajectoryPoints
      .filter(pt => pt.lat != null && pt.lon != null)
      .map((pt, idx) => ({
      lat: pt.lat,
      lng: pt.lon,
      cycle: pt.cycle,
      date: pt.date,
      isLast: idx === trajectoryPoints.length - 1,
      size: idx === trajectoryPoints.length - 1 ? 1.2 : 0.6,
      color: idx === trajectoryPoints.length - 1 ? '#fef08a' : '#ffffff'
    }));
  }, [trajectoryPoints]);

  // Helper: generate path coords for a bounding box
  const generateBoxPath = useCallback((north, south, east, west) => {
    if (isNaN(north) || isNaN(south) || isNaN(east) || isNaN(west)) return [];
    const path = [];
    const steps = 10;
    // Top edge (West to East)
    for (let i = 0; i <= steps; i++) {
      path.push({ lat: north, lng: west + (east - west) * (i / steps) });
    }
    // Right edge (North to South)
    for (let i = 0; i <= steps; i++) {
      path.push({ lat: north - (north - south) * (i / steps), lng: east });
    }
    // Bottom edge (East to West)
    for (let i = 0; i <= steps; i++) {
      path.push({ lat: south, lng: east - (east - west) * (i / steps) });
    }
    // Left edge (South to North)
    for (let i = 0; i <= steps; i++) {
      path.push({ lat: south + (north - south) * (i / steps), lng: west });
    }
    return path;
  }, []);

  // Bounding box path (from finalized bounds)
  const boxPathData = useMemo(() => {
    if (!bounds) return [];
    const north = parseFloat(bounds.north);
    const south = parseFloat(bounds.south);
    const east = parseFloat(bounds.east);
    const west = parseFloat(bounds.west);
    const path = generateBoxPath(north, south, east, west);
    if (path.length === 0) return [];
    return [{ coords: path, id: 'bounds' }];
  }, [bounds, generateBoxPath]);

  // Preview path (while drawing — between first corner and preview corner)
  const previewPathData = useMemo(() => {
    if (!firstCorner || !previewCorner) return [];
    const north = Math.max(firstCorner.lat, previewCorner.lat);
    const south = Math.min(firstCorner.lat, previewCorner.lat);
    const east = Math.max(firstCorner.lng, previewCorner.lng);
    const west = Math.min(firstCorner.lng, previewCorner.lng);
    if (Math.abs(north - south) < 0.1 && Math.abs(east - west) < 0.1) return [];
    const path = generateBoxPath(north, south, east, west);
    if (path.length === 0) return [];
    return [{ coords: path, id: 'preview' }];
  }, [firstCorner, previewCorner, generateBoxPath]);

  // Combined paths for rendering
  const allPathsData = useMemo(() => {
    if (firstCorner && previewPathData.length > 0) return previewPathData;
    return boxPathData;
  }, [boxPathData, previewPathData, firstCorner]);

  // Globe click handler for bounding box drawing
  const handleGlobeClick = useCallback(({ lat, lng }) => {
    if (!isDrawingMode) return;

    if (!firstCorner) {
      // First click — set the first corner
      setFirstCorner({ lat, lng });
      setPreviewCorner({ lat, lng });
    } else {
      // Second click — finalize the bounding box
      const north = Math.max(firstCorner.lat, lat);
      const south = Math.min(firstCorner.lat, lat);
      const east = Math.max(firstCorner.lng, lng);
      const west = Math.min(firstCorner.lng, lng);

      if (onBoundsChange) {
        onBoundsChange({ north, south, east, west });
      }

      // Reset drawing state
      setFirstCorner(null);
      setPreviewCorner(null);
      setIsDrawingMode(false);
    }
  }, [isDrawingMode, firstCorner, onBoundsChange]);

  // Track mouse movement on globe for preview rectangle
  useEffect(() => {
    if (!isDrawingMode || !firstCorner || !globeRef.current) return;

    const renderer = globeRef.current.renderer();
    const canvas = renderer?.domElement;
    if (!canvas) return;

    const handleMouseMove = (e) => {
      // Use the globe's internal method to convert screen coords to lat/lng
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      
      // Convert to globe coordinates using Three.js raycasting
      const globe = globeRef.current;
      if (globe && globe.toGlobeCoords) {
        const coords = globe.toGlobeCoords(x, y);
        if (coords) {
          setPreviewCorner({ lat: coords.lat, lng: coords.lng });
        }
      }
    };

    canvas.addEventListener('mousemove', handleMouseMove);
    return () => canvas.removeEventListener('mousemove', handleMouseMove);
  }, [isDrawingMode, firstCorner]);

  // Point label on hover
  const pointLabel = useCallback((d) => {
    const rawDate = d.date || '';
    const displayDate = rawDate.length >= 8
      ? `${rawDate.slice(0,4)}-${rawDate.slice(4,6)}-${rawDate.slice(6,8)}`
      : rawDate;
    return `
      <div style="
        background: rgba(15,23,42,0.92);
        color: white;
        padding: 8px 12px;
        border-radius: 8px;
        font-family: 'Inter', sans-serif;
        font-size: 12px;
        border: 1px solid rgba(255,255,255,0.15);
        backdrop-filter: blur(8px);
        min-width: 120px;
      ">
        <div style="font-weight: 700; margin-bottom: 3px;">${d.platform} ${d.status === 'inactive' ? '(Inactive)' : ''}</div>
        <div style="color: #94a3b8;">${d.type?.toUpperCase()} · ${displayDate}</div>
        <div style="color: #94a3b8;">${d.lat?.toFixed(3)}°, ${d.lon?.toFixed(3)}°</div>
        <div style="color: #60a5fa; font-size: 10px; margin-top: 3px;">Click to view trajectory</div>
      </div>
    `;
  }, []);

  const handlePointClick = useCallback((point) => {
    if (isDrawingMode) return; // Don't select floats while drawing
    if (point && onFloatClick) {
      onFloatClick(point);
    }
  }, [onFloatClick, isDrawingMode]);

  // Navigation controls
  const flyToIndia = useCallback(() => {
    if (globeRef.current) {
      setIsTransitioning(true);
      globeRef.current.pointOfView({ lat: 20, lng: 78, altitude: 2.5 }, 1500);
      setTimeout(() => setIsTransitioning(false), 1600);
    }
  }, []);

  const zoomIn = useCallback(() => {
    if (globeRef.current) {
      setIsTransitioning(true);
      const pov = globeRef.current.pointOfView();
      globeRef.current.pointOfView({ ...pov, altitude: pov.altitude * 0.6 }, 400);
      setTimeout(() => setIsTransitioning(false), 500);
    }
  }, []);

  const zoomOut = useCallback(() => {
    if (globeRef.current) {
      setIsTransitioning(true);
      const pov = globeRef.current.pointOfView();
      globeRef.current.pointOfView({ ...pov, altitude: Math.min(pov.altitude * 1.5, 5) }, 400);
      setTimeout(() => setIsTransitioning(false), 500);
    }
  }, []);

  // Toggle drawing mode
  const toggleDrawingMode = useCallback(() => {
    if (isDrawingMode) {
      // Cancel drawing
      setIsDrawingMode(false);
      setFirstCorner(null);
      setPreviewCorner(null);
    } else {
      setIsDrawingMode(true);
      setFirstCorner(null);
      setPreviewCorner(null);
    }
  }, [isDrawingMode]);

  // Clear bounds
  const clearBounds = useCallback(() => {
    if (onBoundsChange) {
      onBoundsChange(null);
    }
    setFirstCorner(null);
    setPreviewCorner(null);
    setIsDrawingMode(false);
  }, [onBoundsChange]);

  // Waypoint label element
  const labelElement = useCallback((d) => {
    const el = document.createElement('div');
    el.style.cssText = `
      width: ${d.isLast ? '12px' : '8px'};
      height: ${d.isLast ? '12px' : '8px'};
      border-radius: 50%;
      background: ${d.color};
      border: 2px solid ${d.isLast ? '#ca8a04' : '#ef4444'};
      box-shadow: 0 0 6px ${d.isLast ? '#ca8a04' : '#ef444480'};
      cursor: pointer;
    `;
    return el;
  }, []);

  // Waypoint label tooltip
  const labelLabel = useCallback((d) => {
    const rawDate = d.date || '';
    const displayDate = rawDate.length >= 8
      ? `${rawDate.slice(0,4)}-${rawDate.slice(4,6)}-${rawDate.slice(6,8)}`
      : rawDate;
    return `
      <div style="
        background: rgba(15,23,42,0.92);
        color: white;
        padding: 6px 10px;
        border-radius: 6px;
        font-size: 11px;
        border: 1px solid rgba(255,255,255,0.15);
      ">
        <strong>Cycle ${d.cycle}</strong><br/>
        ${displayDate}<br/>
        ${d.lat?.toFixed(3)}°, ${d.lng?.toFixed(3)}°
      </div>
    `;
  }, []);

  // Drawing mode styles
  const drawBtnStyle = {
    background: isDrawingMode ? '#0284c7' : 'rgba(15,23,42,0.85)',
    color: 'white',
    border: isDrawingMode ? '2px solid #38bdf8' : '1px solid rgba(255,255,255,0.15)',
    padding: '7px 12px',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '12px',
    fontWeight: 600,
    fontFamily: "'Inter', sans-serif",
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    backdropFilter: 'blur(10px)',
    transition: 'all 0.2s ease',
    boxShadow: isDrawingMode ? '0 0 12px rgba(56,189,248,0.4)' : 'none',
  };

  const clearBtnStyle = {
    background: 'rgba(239,68,68,0.15)',
    color: '#fca5a5',
    border: '1px solid rgba(239,68,68,0.3)',
    padding: '7px 12px',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '12px',
    fontWeight: 600,
    fontFamily: "'Inter', sans-serif",
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    backdropFilter: 'blur(10px)',
    transition: 'all 0.2s ease',
  };

  return (
    <div style={{ width: '100%', height: '100vh', position: 'relative', background: '#070d1b', cursor: isDrawingMode ? 'crosshair' : 'default' }}>
      <Globe
        ref={globeRef}
        onGlobeReady={() => setGlobeReady(true)}
        globeImageUrl="//unpkg.com/three-globe/example/img/earth-blue-marble.jpg"
        bumpImageUrl="//unpkg.com/three-globe/example/img/earth-topology.png"
        backgroundImageUrl="//unpkg.com/three-globe/example/img/night-sky.png"
        
        // Active float points
        pointsData={isTransitioning ? [] : filteredFloats}
        pointLat="lat"
        pointLng="lon"
        pointColor={pointColor}
        pointAltitude={pointAltitude}
        pointRadius={pointRadius}
        pointLabel={pointLabel}
        onPointClick={handlePointClick}
        pointsMerge={false}
        
        // Trajectory arcs between consecutive waypoints
        arcsData={isTransitioning ? [] : arcData}
        arcStartLat="startLat"
        arcStartLng="startLng"
        arcEndLat="endLat"
        arcEndLng="endLng"
        arcColor={() => ['#ef4444', '#f97316']}
        arcStroke={1.5}
        arcDashLength={0.4}
        arcDashGap={0.2}
        arcDashAnimateTime={2000}
        arcAltitudeAutoScale={0.15}

        // Trajectory waypoint glowing rings (WebGL, completely crash-free and performant)
        ringsData={isTransitioning ? [] : labelData}
        ringLat="lat"
        ringLng="lng"
        ringColor={d => d.isLast ? '#ca8a04' : '#ef4444'}
        ringMaxRadius={d => d.isLast ? 0.8 : 0.4}
        ringPropagationSpeed={1.5}
        ringRepeatPeriod={800}
        
        // Waypoint tooltips via labels layer
        labelsData={isTransitioning ? [] : labelData}
        labelLat="lat"
        labelLng="lng"
        labelText={d => (d.isLast && d.cycle != null) ? d.cycle.toString() : ''}
        labelSize={d => d.isLast ? 1.0 : 0.6}
        labelColor={() => 'rgba(255,255,255,0.8)'}
        labelDotRadius={0}
        labelAltitude={0.015}
        labelLabel={labelLabel}
        
        // Bounds Path (finalized or preview)
        pathsData={isTransitioning ? [] : allPathsData}
        pathPoints="coords"
        pathPointLat={d => d.lat}
        pathPointLng={d => d.lng}
        pathStroke={d => d.id === 'preview' ? '#38bdf8' : '#0284c7'}
        pathWidth={d => d.id === 'preview' ? 1.0 : 1.5}
        pathDashLength={d => d.id === 'preview' ? 0.5 : undefined}
        pathDashGap={d => d.id === 'preview' ? 0.3 : undefined}
        
        // Globe click for bounding box drawing
        onGlobeClick={handleGlobeClick}
        
        // Globe appearance
        atmosphereColor="#3a7bd5"
        atmosphereAltitude={0.18}
        
        width={window.innerWidth - 280}
        height={window.innerHeight}
      />

      {/* Drawing Mode Instructions Banner */}
      {isDrawingMode && (
        <div style={{
          position: 'absolute',
          top: 12,
          left: '50%',
          transform: 'translateX(-50%)',
          background: 'rgba(2,132,199,0.9)',
          color: 'white',
          padding: '8px 20px',
          borderRadius: '8px',
          fontSize: '13px',
          fontWeight: 600,
          fontFamily: "'Inter', sans-serif",
          backdropFilter: 'blur(10px)',
          border: '1px solid rgba(56,189,248,0.4)',
          zIndex: 1001,
          pointerEvents: 'none',
          boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
          animation: 'fadeIn 0.3s ease',
        }}>
          {!firstCorner 
            ? '📍 Click on the globe to set the first corner' 
            : '📍 Click again to set the opposite corner'}
        </div>
      )}

      {/* Bounding Box Drawing Controls */}
      <div style={{
        position: 'absolute',
        top: 72,
        right: 14,
        zIndex: 1000,
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
      }}>
        <button 
          style={drawBtnStyle}
          onClick={toggleDrawingMode}
          title={isDrawingMode ? 'Cancel Drawing' : 'Draw Bounding Box'}
          onMouseEnter={e => e.currentTarget.style.opacity = '0.9'}
          onMouseLeave={e => e.currentTarget.style.opacity = '1'}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray={isDrawingMode ? "none" : "5 3"} />
          </svg>
          {isDrawingMode ? 'Cancel' : 'Draw Box'}
        </button>
        
        {bounds && !isDrawingMode && (
          <button
            style={clearBtnStyle}
            onClick={clearBounds}
            title="Clear Bounding Box"
            onMouseEnter={e => e.currentTarget.style.opacity = '0.85'}
            onMouseLeave={e => e.currentTarget.style.opacity = '1'}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
            Clear Box
          </button>
        )}
      </div>

      {/* Custom Navigation Controls */}
      <div className="cesium-nav-controls">
        <button className="cesium-nav-btn" onClick={flyToIndia} title="Fly to India (Home)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
            <polyline points="9 22 9 12 15 12 15 22"/>
          </svg>
        </button>
        <div className="cesium-nav-divider" />
        <button className="cesium-nav-btn" onClick={zoomIn} title="Zoom In">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
        </button>
        <button className="cesium-nav-btn" onClick={zoomOut} title="Zoom Out">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
        </button>
        <div className="cesium-nav-divider" />
        <div className="cesium-nav-hint">
          <div className="hint-row">🖱️ Left drag: Rotate</div>
          <div className="hint-row">🖱️ Right drag: Zoom</div>
          <div className="hint-row">⚲ Scroll: Zoom</div>
        </div>
      </div>
    </div>
  );
};

export default GlobeView;
