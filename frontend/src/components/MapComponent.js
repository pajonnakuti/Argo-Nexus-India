import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import { MapContainer, TileLayer, FeatureGroup, CircleMarker, Polyline, Tooltip, useMap, Marker } from 'react-leaflet';
import { EditControl } from 'react-leaflet-draw';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw/dist/leaflet.draw.css';
import CesiumView from './CesiumView';

import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';

let DefaultIcon = L.icon({
  iconUrl: icon,
  shadowUrl: iconShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41]
});

L.Marker.prototype.options.icon = DefaultIcon;

// ── Active Floats Toggle Control ─────────────────────────────────────────────
const INST_LABELS = {
  'AO': 'AOML', 'IF': 'Ifremer', 'JA': 'JAMSTEC', 'CS': 'CSIRO',
  'BO': 'BODC', 'ME': 'MEDS', 'IN': 'INCOIS', 'HZ': 'Hangzhou',
  'KM': 'KORDI', 'KO': 'KO', 'NM': 'NM'
};

const ActiveFloatsControl = ({ showActive, setShowActive, floatFilter, setFloatFilter, oceanFilter, setOceanFilter, instFilter, setInstFilter, floatCounts, parameterCounts }) => {
  const map = useMap();

  useEffect(() => {
    const CustomControl = L.Control.extend({
      options: { position: 'topright' },
      onAdd: function () {
        const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control custom-control active-floats-control-wrapper');

        // Build institution options HTML
        const instEntries = Object.entries(floatCounts.inst || {});
        const instOptionsHtml = instEntries.map(([code, count]) => 
          `<div class="filter-option ${instFilter === code ? 'selected' : ''}" data-inst="${code}">
            ${INST_LABELS[code] || code} (${count})
          </div>`
        ).join('');

        // Build ocean options HTML
        const oceanEntries = Object.entries(floatCounts.ocean || {});
        const oceanOptionsHtml = oceanEntries.map(([name, count]) => 
          `<div class="filter-option ${oceanFilter === name ? 'selected' : ''}" data-ocean="${name}">
            ${name} (${count})
          </div>`
        ).join('');

        // Active filter count badge
        let activeFilterCount = 0;
        if (floatFilter !== 'all') activeFilterCount++;
        if (oceanFilter !== 'all') activeFilterCount++;
        if (instFilter !== 'all') activeFilterCount++;
        const badge = activeFilterCount > 0 ? `<span class="filter-badge">${activeFilterCount}</span>` : '';

        container.innerHTML = `
          <div class="active-floats-dropdown">
            <button class="active-floats-btn ${showActive ? 'active' : ''}" title="Toggle Active Floats">
              📡 ${showActive ? 'Hide' : 'Show'} Active Floats ${floatCounts.total > 0 ? `(${floatCounts.total})` : ''} ${badge}
              <span class="dropdown-arrow">▼</span>
            </button>
            <div class="dropdown-content ${showActive ? 'show' : ''}">
              <div class="filter-section-label">Type</div>
              <div class="filter-option ${floatFilter === 'all' ? 'selected' : ''}" data-filter="all">
                All (${floatCounts.total})
              </div>
              <div class="filter-option ${floatFilter === 'core' ? 'selected' : ''}" data-filter="core">
                Core (${floatCounts.core})
              </div>
              <div class="filter-option ${floatFilter === 'bgc' ? 'selected' : ''}" data-filter="bgc">
                BGC (${floatCounts.bgc})
              </div>
              <div class="filter-divider"></div>
              <div class="filter-section-label">Ocean</div>
              <div class="filter-option ${oceanFilter === 'all' ? 'selected' : ''}" data-ocean="all">
                All Oceans
              </div>
              ${oceanOptionsHtml}
              <div class="filter-divider"></div>
              <div class="filter-section-label">Institution</div>
              <div class="filter-option ${instFilter === 'all' ? 'selected' : ''}" data-inst="all">
                All Institutions
              </div>
              ${instOptionsHtml}
              <div class="filter-divider"></div>
              <div style="font-size: 0.75rem; text-align: center;">
                <div><strong>Total INCOIS Floats:</strong> ${floatCounts.total}</div>
                <div><strong>Core:</strong> ${floatCounts.core} | <strong>BGC:</strong> ${floatCounts.bgc}</div>
              </div>
              <div class="parameter-coverage">
                <div class="filter-section-label" style="text-align: center; margin-bottom: 6px;">PARAMETER COVERAGE</div>
                <div style="text-align: center; margin-bottom: 4px;"><strong>NO<sub>3</sub>:</strong> ${parameterCounts.NO3} <span style="color: #cbd5e1; margin: 0 4px;">|</span> <strong>DOXY:</strong> ${parameterCounts.DOXY}</div>
                <div style="text-align: center;"><strong>CHLA:</strong> ${parameterCounts.CHLA} <span style="color: #cbd5e1; margin: 0 4px;">|</span> <strong>BBP700:</strong> ${parameterCounts.BBP700}</div>
              </div>
            </div>
          </div>
        `;

        const btn = container.querySelector('.active-floats-btn');
        btn.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          setShowActive(!showActive);
        };

        // Type filter clicks
        container.querySelectorAll('[data-filter]').forEach(opt => {
          opt.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!showActive) setShowActive(true);
            setFloatFilter(opt.getAttribute('data-filter'));
          };
        });

        // Ocean filter clicks
        container.querySelectorAll('[data-ocean]').forEach(opt => {
          opt.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!showActive) setShowActive(true);
            setOceanFilter(opt.getAttribute('data-ocean'));
          };
        });

        // Institution filter clicks
        container.querySelectorAll('[data-inst]').forEach(opt => {
          opt.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!showActive) setShowActive(true);
            setInstFilter(opt.getAttribute('data-inst'));
          };
        });

        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.disableScrollPropagation(container);
        return container;
      }
    });

    const control = new CustomControl();
    map.addControl(control);
    return () => map.removeControl(control);
  }, [map, showActive, setShowActive, floatFilter, setFloatFilter, oceanFilter, setOceanFilter, instFilter, setInstFilter, floatCounts]);

  return null;
};

// ── Trajectory Info Panel ─────────────────────────────────────────────────────
const TrajectoryPanel = ({ float, trajectoryInfo, loading, onClose }) => {
  if (!float) return null;

  const formatDate = (raw) => {
    if (!raw || raw.length < 8) return 'N/A';
    return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
  };

  return (
    <div className="trajectory-panel">
      <div className="trajectory-panel-header">
        <div className="trajectory-panel-title">
          <span className="trajectory-icon">🛰️</span>
          <span>Float <strong>{float.platform}</strong></span>
          <span className={`float-type-badge ${float.type}`}>{float.type?.toUpperCase()}</span>
        </div>
        <button className="trajectory-close-btn" onClick={onClose} title="Clear trajectory">✕</button>
      </div>

      {loading ? (
        <div className="trajectory-loading">
          <div className="traj-spinner" />
          <span>Loading trajectory…</span>
        </div>
      ) : trajectoryInfo ? (
        <div className="trajectory-stats">
          <div className="trajectory-stat">
            <span className="tstat-label">Total Cycles</span>
            <span className="tstat-value">{trajectoryInfo.total_cycles}</span>
          </div>
          <div className="trajectory-stat">
            <span className="tstat-label">First Obs.</span>
            <span className="tstat-value">{formatDate(trajectoryInfo.first_date)}</span>
          </div>
          <div className="trajectory-stat">
            <span className="tstat-label">Last Obs.</span>
            <span className="tstat-value">{formatDate(trajectoryInfo.last_date)}</span>
          </div>
          <div className="trajectory-stat">
            <span className="tstat-label">Institution</span>
            <span className="tstat-value">{float.institution || 'N/A'}</span>
          </div>
        </div>
      ) : null}
    </div>
  );
};

// ── Map pan helper ────────────────────────────────────────────────────────────
const MapPanner = ({ center }) => {
  const map = useMap();
  useEffect(() => {
    if (center) map.panTo(center, { animate: true, duration: 0.6 });
  }, [center, map]);
  return null;
};

// ── Main MapComponent ─────────────────────────────────────────────────────────
const MapComponent = ({ onBoundsChange, bounds }) => {
  const mapRef = useRef();
  const featureGroupRef = useRef();

  const [is3D, setIs3D] = useState(false);
  const [activeFloats, setActiveFloats] = useState([]);
  const [showActive, setShowActive] = useState(false);
  const [floatFilter, setFloatFilter] = useState('all');
  const [oceanFilter, setOceanFilter] = useState('all');
  const [instFilter, setInstFilter] = useState('all');
  const [floatCounts, setFloatCounts] = useState({ total: 0, core: 0, bgc: 0, ocean: {}, inst: {} });

  const incoisFloats = useMemo(() => {
    const incois = activeFloats.filter(f => {
      const platform = String(f.platform || '').toUpperCase();
      const argoIndex = String(f.argo_prof_index || f.argo_prof_index_name || '').toUpperCase();
      return platform.startsWith('IN') || argoIndex.includes('IN');
    });

    const unique = [];
    const seen = new Set();
    incois.forEach(f => {
      const id = String(f.platform || '').toUpperCase();
      if (id && !seen.has(id)) {
        seen.add(id);
        unique.push(f);
      }
    });
    return unique.length > 0 ? unique : activeFloats;
  }, [activeFloats]);

  const parameterCounts = useMemo(() => {
    const counts = { NO3: 0, DOXY: 0, CHLA: 0, BBP700: 0 };
    incoisFloats.forEach((float) => {
      const params = float.parameters || float.params || float.parameter_list || [];
      const paramList = Array.isArray(params) ? params.map(p => String(p).toUpperCase()) : [];

      if (paramList.includes('NITRATE') || paramList.includes('NO3') || float.NITRATE || float.NO3) {
        counts.NO3 += 1;
      }
      if (paramList.includes('DOXY') || float.DOXY) {
        counts.DOXY += 1;
      }
      if (paramList.includes('CHLA') || float.CHLA) {
        counts.CHLA += 1;
      }
      if (paramList.includes('BBP700') || float.BBP700) {
        counts.BBP700 += 1;
      }
    });
    return counts;
  }, [incoisFloats]);

  // Trajectory state
  const [selectedFloat, setSelectedFloat] = useState(null);
  const [trajectoryPoints, setTrajectoryPoints] = useState([]);
  const [trajectoryInfo, setTrajectoryInfo] = useState(null);
  const [trajLoading, setTrajLoading] = useState(false);
  const [panCenter, setPanCenter] = useState(null);

  // Fetch active floats once
  useEffect(() => {
    fetch('http://localhost:8000/api/active_floats')
      .then(res => res.json())
      .then(data => {
        if (data && data.floats) {
          setActiveFloats(data.floats);
          setFloatCounts({
            total: data.count || 0,
            core: data.core_count || 0,
            bgc: data.bgc_count || 0,
            ocean: data.ocean_counts || {},
            inst: data.inst_counts || {}
          });
        }
      })
      .catch(err => console.error("Failed to load active floats:", err));
  }, []);

  // Sync bounding-box rectangle
  useEffect(() => {
    if (bounds && featureGroupRef.current && mapRef.current &&
      typeof bounds.north === 'number' && typeof bounds.south === 'number' &&
      typeof bounds.east === 'number' && typeof bounds.west === 'number') {
      featureGroupRef.current.clearLayers();
      if (bounds.north > bounds.south && bounds.east > bounds.west) {
        const rectangle = L.rectangle([
          [bounds.south, bounds.west],
          [bounds.north, bounds.east]
        ], {
          color: '#0284C7',
          weight: 2,
          fillOpacity: 0.2
        });
        rectangle.editing.enable();
        rectangle.on('edit', (e) => {
          const editedBounds = e.target.getBounds();
          onBoundsChange({
            north: editedBounds.getNorth(),
            south: editedBounds.getSouth(),
            east: editedBounds.getEast(),
            west: editedBounds.getWest()
          });
        });
        featureGroupRef.current.addLayer(rectangle);
      }
    }
  }, [bounds, onBoundsChange, is3D]);

  const onCreate = (e) => {
    const { layerType, layer } = e;
    if (layerType === 'rectangle') {
      const b = layer.getBounds();
      onBoundsChange({ north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() });
    }
  };

  const onEdited = (e) => {
    e.layers.eachLayer((layer) => {
      const b = layer.getBounds();
      onBoundsChange({ north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() });
    });
  };

  const handleFloatClick = useCallback(async (float) => {
    setSelectedFloat(float);
    setTrajectoryPoints([]);
    setTrajectoryInfo(null);
    setTrajLoading(true);
    setPanCenter([float.lat, float.lon]);

    try {
      const resp = await fetch(`http://localhost:8000/api/trajectory/${float.platform}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      setTrajectoryInfo(data);
      setTrajectoryPoints(data.points || []);
    } catch (err) {
      console.error('Trajectory fetch failed:', err);
      setTrajectoryInfo(null);
      setTrajectoryPoints([]);
    } finally {
      setTrajLoading(false);
    }
  }, []);

  const clearTrajectory = useCallback(() => {
    setSelectedFloat(null);
    setTrajectoryPoints([]);
    setTrajectoryInfo(null);
    setPanCenter(null);
  }, []);

  const polylinePositions = trajectoryPoints.map(p => [p.lat, p.lon]);

  return (
    <div className="map-container" style={{ position: 'relative' }}>
      {/* 3D/2D View Toggle */}
      <div style={{ position: 'absolute', top: 20, left: 60, zIndex: 1000, display: 'flex', gap: '8px', background: 'rgba(15,23,42,0.8)', padding: '5px', borderRadius: '8px', backdropFilter: 'blur(10px)', border: '1px solid rgba(255,255,255,0.1)' }}>
          <button 
             onClick={() => setIs3D(false)} 
             style={{ background: !is3D ? '#0284c7' : 'transparent', color: 'white', border: 'none', padding: '6px 12px', borderRadius: '4px', cursor: 'pointer', fontWeight: !is3D ? 'bold' : 'normal' }}
          >
             🗺️ 2D Map
          </button>
          <button 
             onClick={() => setIs3D(true)} 
             style={{ background: is3D ? '#0284c7' : 'transparent', color: 'white', border: 'none', padding: '6px 12px', borderRadius: '4px', cursor: 'pointer', fontWeight: is3D ? 'bold' : 'normal' }}
          >
             🌍 3D Globe
          </button>
      </div>

      {/* Trajectory panel — rendered outside MapContainer so it's above it */}
      <TrajectoryPanel
        float={selectedFloat}
        trajectoryInfo={trajectoryInfo}
        loading={trajLoading}
        onClose={clearTrajectory}
      />

      {is3D ? (
          <CesiumView 
             activeFloats={activeFloats}
             showActive={showActive}
             floatFilter={floatFilter}
             selectedFloat={selectedFloat}
             trajectoryPoints={trajectoryPoints}
             onFloatClick={handleFloatClick}
             bounds={bounds}
          />
      ) : (
      <MapContainer
        center={[22, 80]}
        zoom={5}
        style={{ height: '100vh', width: '100%' }}
        ref={mapRef}
        whenCreated={(mapInstance) => { mapRef.current = mapInstance; }}
      >
        <TileLayer
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}"
          attribution='Tiles &copy; Esri &mdash; Sources: GEBCO, NOAA, CHS, OSU, UNH, CSUMB, National Geographic, DeLorme, NAVTEQ, and Esri'
        />

        <FeatureGroup ref={featureGroupRef}>
          <EditControl
            position="topright"
            onCreated={onCreate}
            onEdited={onEdited}
            draw={{
              rectangle: true,
              polyline: false,
              polygon: false,
              circle: false,
              marker: false,
              circlemarker: false
            }}
            edit={{ edit: true, remove: true }}
          />
        </FeatureGroup>

        <ActiveFloatsControl
          showActive={showActive}
          setShowActive={setShowActive}
          floatFilter={floatFilter}
          setFloatFilter={setFloatFilter}
          oceanFilter={oceanFilter}
          setOceanFilter={setOceanFilter}
          instFilter={instFilter}
          setInstFilter={setInstFilter}
          floatCounts={floatCounts}
          parameterCounts={parameterCounts}
        />

        {/* ── Trajectory Polyline ── */}
        {polylinePositions.length > 1 && (
          <Polyline
            positions={polylinePositions}
            pathOptions={{ color: '#ef4444', weight: 2.5, opacity: 0.85, dashArray: null }}
          />
        )}

        {/* ── Trajectory Waypoint Markers ── */}
        {trajectoryPoints.map((pt, idx) => {
          const isLast = idx === trajectoryPoints.length - 1;
          const rawDate = pt.date || '';
          const displayDate = rawDate.length >= 8
            ? `${rawDate.slice(0,4)}-${rawDate.slice(4,6)}-${rawDate.slice(6,8)}`
            : rawDate;

          return (
            <CircleMarker
              key={`traj-${pt.cycle}-${idx}`}
              center={[pt.lat, pt.lon]}
              radius={isLast ? 7 : 5}
              pathOptions={isLast
                ? { color: '#ca8a04', weight: 2.5, fillColor: '#fef08a', fillOpacity: 1 }
                : { color: '#ef4444', weight: 1.5, fillColor: '#ffffff', fillOpacity: 0.9 }
              }
            >
              <Tooltip direction="top" offset={[0, -6]} opacity={0.95}>
                <div className="traj-tooltip">
                  <strong>Cycle {pt.cycle}</strong>
                  <div>{displayDate}</div>
                  <div>{pt.lat.toFixed(3)}°, {pt.lon.toFixed(3)}°</div>
                </div>
              </Tooltip>
            </CircleMarker>
          );
        })}

        {/* ── Trajectory Cycle Labels (Permanent) ── */}
        {trajectoryPoints.map((pt, idx) => (
           <Marker 
             key={`traj-lbl-${pt.cycle}-${idx}`}
             position={[pt.lat, pt.lon]}
             icon={L.divIcon({
               className: 'traj-cycle-label',
               html: `<div>${pt.cycle}</div>`,
               iconSize: [20, 20],
               iconAnchor: [-6, 10]
             })}
             interactive={false}
           />
        ))}

        {/* ── Active Float Markers ── */}
        {showActive && activeFloats
          .filter(f => {
            if (floatFilter !== 'all' && f.type !== floatFilter) return false;
            if (oceanFilter !== 'all') {
              const oceanLabels = {'I': 'Indian', 'P': 'Pacific', 'A': 'Atlantic', '': 'Unknown'};
              if ((oceanLabels[f.ocean] || f.ocean) !== oceanFilter) return false;
            }
            if (instFilter !== 'all' && f.institution !== instFilter) return false;
            return true;
          })
          .map(float => {
            const rawDate = float.date || '';
            let displayDate = rawDate;
            if (rawDate.length >= 8) {
              displayDate = `${rawDate.slice(0,4)}-${rawDate.slice(4,6)}-${rawDate.slice(6,8)}`;
            }
            const isSelected = selectedFloat?.platform === float.platform;

            return (
              <CircleMarker
                key={float.platform}
                center={[float.lat, float.lon]}
                radius={isSelected ? 9 : 5}
                pathOptions={{
                  color: isSelected ? '#f97316' : '#ca8a04',
                  weight: isSelected ? 2.5 : 1,
                  fillColor: isSelected ? '#fed7aa' : '#fef08a',
                  fillOpacity: 0.9
                }}
                eventHandlers={{
                  click: () => handleFloatClick(float),
                }}
              >
                <Tooltip direction="top" offset={[0, -6]} opacity={0.92}>
                  <div className="traj-tooltip">
                    <strong>{float.platform}</strong>
                    <div>{float.type?.toUpperCase()} · {displayDate}</div>
                    <div>{float.lat.toFixed(3)}°, {float.lon.toFixed(3)}°</div>
                    <div style={{ color: '#93c5fd', fontSize: '0.7rem', marginTop: 2 }}>Click to view trajectory</div>
                  </div>
                </Tooltip>
              </CircleMarker>
            );
          })
        }

        {panCenter && <MapPanner center={panCenter} />}
      </MapContainer>
      )}
    </div>
  );
};

export default MapComponent;
