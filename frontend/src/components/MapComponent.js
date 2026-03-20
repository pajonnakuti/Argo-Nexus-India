import React, { useRef, useEffect, useState } from 'react';
import { MapContainer, TileLayer, FeatureGroup, CircleMarker, Popup, useMap } from 'react-leaflet';
import { EditControl } from 'react-leaflet-draw';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw/dist/leaflet.draw.css';

import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';

let DefaultIcon = L.icon({
  iconUrl: icon,
  shadowUrl: iconShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41]
});

L.Marker.prototype.options.icon = DefaultIcon;

const ActiveFloatsControl = ({ activeFloats, showActive, setShowActive, floatFilter, setFloatFilter, floatCounts }) => {
  const map = useMap();

  useEffect(() => {
    const CustomControl = L.Control.extend({
      options: { position: 'topright' },
      onAdd: function () {
        const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control custom-control active-floats-control-wrapper');
        
        // Build the inner HTML for a dropdown
        container.innerHTML = `
          <div class="active-floats-dropdown">
            <button class="active-floats-btn ${showActive ? 'active' : ''}" title="Toggle Active Floats">
              📡 ${showActive ? 'Hide' : 'Show'} Active Floats ${floatCounts.total > 0 ? `(${floatCounts.total})` : ''}
              <span class="dropdown-arrow">▼</span>
            </button>
            <div class="dropdown-content ${showActive ? 'show' : ''}">
              <div class="filter-option ${floatFilter === 'all' ? 'selected' : ''}" data-filter="all">
                All (${floatCounts.total})
              </div>
              <div class="filter-option ${floatFilter === 'core' ? 'selected' : ''}" data-filter="core">
                Core (${floatCounts.core})
              </div>
              <div class="filter-option ${floatFilter === 'bgc' ? 'selected' : ''}" data-filter="bgc">
                BGC (${floatCounts.bgc})
              </div>
            </div>
          </div>
        `;
        
        // Toggle main button
        const btn = container.querySelector('.active-floats-btn');
        btn.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          setShowActive(!showActive);
        };
        
        // Filter options
        const options = container.querySelectorAll('.filter-option');
        options.forEach(opt => {
          opt.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            if(!showActive) setShowActive(true); // make sure it's shown if they click a filter
            setFloatFilter(opt.getAttribute('data-filter'));
          };
        });
        
        // Prevent map clicks from firing when clicking the control
        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.disableScrollPropagation(container);
        return container;
      }
    });

    const control = new CustomControl();
    map.addControl(control);

    return () => map.removeControl(control);
  }, [map, showActive, setShowActive, floatFilter, setFloatFilter, floatCounts]);

  return null;
};

const MapComponent = ({ onBoundsChange, bounds }) => {
  const mapRef = useRef();
  const featureGroupRef = useRef();
  const [activeFloats, setActiveFloats] = useState([]);
  const [showActive, setShowActive] = useState(false);
  const [floatFilter, setFloatFilter] = useState('all');
  const [floatCounts, setFloatCounts] = useState({ total: 0, core: 0, bgc: 0 });

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
            bgc: data.bgc_count || 0
          });
        }
      })
      .catch(err => console.error("Failed to load active floats:", err));
  }, []);

  useEffect(() => {
    if (bounds && featureGroupRef.current && mapRef.current && 
        typeof bounds.north === 'number' && typeof bounds.south === 'number' &&
        typeof bounds.east === 'number' && typeof bounds.west === 'number') {
      // Clear existing layers
      featureGroupRef.current.clearLayers();
      
      // Validate bounds before creating rectangle
      if (bounds.north > bounds.south && bounds.east > bounds.west) {
        // Create editable rectangle from bounds
        const rectangle = L.rectangle([
          [bounds.south, bounds.west],
          [bounds.north, bounds.east]
        ], {
          color: '#0284C7',
          weight: 2,
          fillOpacity: 0.2
        });
        
        // Make rectangle editable
        rectangle.editing.enable();
        
        // Add event listener for when rectangle is edited
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
  }, [bounds, onBoundsChange]);

  const onCreate = (e) => {
    const { layerType, layer } = e;
    if (layerType === 'rectangle') {
      const bounds = layer.getBounds();
      onBoundsChange({
        north: bounds.getNorth(),
        south: bounds.getSouth(),
        east: bounds.getEast(),
        west: bounds.getWest()
      });
    }
  };

  const onEdited = (e) => {
    const { layers } = e;
    layers.eachLayer((layer) => {
      const bounds = layer.getBounds();
      onBoundsChange({
        north: bounds.getNorth(),
        south: bounds.getSouth(),
        east: bounds.getEast(),
        west: bounds.getWest()
      });
    });
  };

  return (
    <div className="map-container">
      <MapContainer
        center={[20, 0]}
        zoom={2}
        style={{ height: '100vh', width: '100%' }}
        ref={mapRef}
        whenCreated={(mapInstance) => {
          mapRef.current = mapInstance;
        }}
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
            edit={{
              edit: true,
              remove: true
            }}
          />
        </FeatureGroup>

        <ActiveFloatsControl 
          activeFloats={activeFloats} 
          showActive={showActive} 
          setShowActive={setShowActive} 
          floatFilter={floatFilter}
          setFloatFilter={setFloatFilter}
          floatCounts={floatCounts}
        />

        {showActive && activeFloats.filter(f => floatFilter === 'all' || f.type === floatFilter).map(float => {
          // Parse date YYYYMMDDHHMMSS for better display
          const rawDate = float.date || '';
          let displayDate = rawDate;
          if (rawDate.length >= 8) {
             displayDate = `${rawDate.slice(0,4)}-${rawDate.slice(4,6)}-${rawDate.slice(6,8)}`;
          }

          return (
            <CircleMarker
              key={float.platform}
              center={[float.lat, float.lon]}
              radius={5}
              pathOptions={{
                color: '#ca8a04', // Darker yellow outline
                weight: 1,
                fillColor: '#fef08a', // vibrant yellow fill
                fillOpacity: 0.8
              }}
            >
              <Popup className="active-float-popup">
                <div className="popup-content">
                  <div className="popup-header">Active Float ({float.type ? float.type.toUpperCase() : 'UNKNOWN'})</div>
                  <div className="popup-row">
                    <span className="popup-label">Platform:</span>
                    <span className="popup-value fw-bold">{float.platform}</span>
                  </div>
                  <div className="popup-row">
                    <span className="popup-label">Last Cycle:</span>
                    <span className="popup-value">{float.cycle || 'N/A'}</span>
                  </div>
                  <div className="popup-row">
                    <span className="popup-label">Last Update:</span>
                    <span className="popup-value">{displayDate}</span>
                  </div>
                  {float.institution && (
                    <div className="popup-row">
                      <span className="popup-label">Institution:</span>
                      <span className="popup-value">{float.institution}</span>
                    </div>
                  )}
                  <div className="popup-row">
                    <span className="popup-label">Location:</span>
                    <span className="popup-value">
                      {float.lat.toFixed(3)}&deg;, {float.lon.toFixed(3)}&deg;
                    </span>
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
};

export default MapComponent;
