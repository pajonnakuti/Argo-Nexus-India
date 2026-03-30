import React, { useEffect, useRef, useCallback } from 'react';

const CesiumView = ({ 
    activeFloats, 
    showActive, 
    floatFilter, 
    selectedFloat, 
    trajectoryPoints,
    onFloatClick 
}) => {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const primitivesRef = useRef(null);

  // Fly to India (home view)
  const flyToIndia = useCallback(() => {
    if (!viewerRef.current || !window.Cesium) return;
    const Cesium = window.Cesium;
    viewerRef.current.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(78, 15, 8000000),
      orientation: {
        heading: Cesium.Math.toRadians(0),
        pitch: Cesium.Math.toRadians(-85),
        roll: 0
      },
      duration: 1.5
    });
  }, []);

  // Zoom helpers
  const zoomIn = useCallback(() => {
    if (!viewerRef.current) return;
    const camera = viewerRef.current.camera;
    const height = camera.positionCartographic.height;
    camera.zoomIn(height * 0.4);
    viewerRef.current.scene.requestRender();
  }, []);

  const zoomOut = useCallback(() => {
    if (!viewerRef.current) return;
    const camera = viewerRef.current.camera;
    const height = camera.positionCartographic.height;
    camera.zoomOut(height * 0.4);
    viewerRef.current.scene.requestRender();
  }, []);

  // Initialize Viewer Once
  useEffect(() => {
     if (!window.Cesium) {
         console.error("Cesium is not loaded.");
         return;
     }
     
     const Cesium = window.Cesium;
     if (!viewerRef.current && containerRef.current) {
         viewerRef.current = new Cesium.Viewer(containerRef.current, {
             infoBox: true,
             selectionIndicator: true,
             navigationHelpButton: true,
             animation: false,
             timeline: false,
             baseLayerPicker: false,
             homeButton: false,
             fullscreenButton: false,
             sceneModePicker: false,
             geocoder: false,
             requestRenderMode: true,
             maximumRenderTimeChange: Infinity
         });

         // Set initial view to India
         viewerRef.current.camera.setView({
           destination: Cesium.Cartesian3.fromDegrees(78, 15, 8000000),
           orientation: {
             heading: Cesium.Math.toRadians(0),
             pitch: Cesium.Math.toRadians(-85),
             roll: 0
           }
         });

         // Enable mouse wheel zoom smoothing
         viewerRef.current.scene.screenSpaceCameraController.zoomEventTypes = [
           Cesium.CameraEventType.WHEEL,
           Cesium.CameraEventType.PINCH
         ];
         viewerRef.current.scene.screenSpaceCameraController.tiltEventTypes = [
           Cesium.CameraEventType.MIDDLE_DRAG,
           Cesium.CameraEventType.PINCH,
           { eventType: Cesium.CameraEventType.RIGHT_DRAG }
         ];

         // Float click handler
         viewerRef.current.screenSpaceEventHandler.setInputAction((movement) => {
             const pickedFeature = viewerRef.current.scene.pick(movement.position);
             if (window.Cesium.defined(pickedFeature) && pickedFeature.id) {
                 const data = pickedFeature.id._floatData || pickedFeature.id;
                 if (data && data.platform && onFloatClick) {
                     onFloatClick(data);
                 }
             }
         }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
     }
  }, [onFloatClick]);

  // Pan to selected float
  useEffect(() => {
    if (selectedFloat && viewerRef.current && window.Cesium) {
      const Cesium = window.Cesium;
      // Fly up slightly and look down at the float
      viewerRef.current.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(selectedFloat.lon, selectedFloat.lat, 1800000),
        duration: 1.5
      });
    }
  }, [selectedFloat]);

  // Update Entities
  useEffect(() => {
     if (!viewerRef.current || !window.Cesium) return;
     const Cesium = window.Cesium;
     const viewer = viewerRef.current;
     
     viewer.entities.removeAll();
     if (primitivesRef.current) {
         viewer.scene.primitives.remove(primitivesRef.current);
         primitivesRef.current = null;
     }

     // Draw active floats (using PointPrimitives for performance +1000% speedup)
     if (showActive && activeFloats) {
         const points = new Cesium.PointPrimitiveCollection();
         
         activeFloats.forEach(float => {
            if (floatFilter === 'all' || float.type === floatFilter) {
               const isSelected = selectedFloat?.platform === float.platform;
               points.add({
                  position: Cesium.Cartesian3.fromDegrees(float.lon, float.lat),
                  pixelSize: isSelected ? 16 : 8,
                  color: isSelected ? Cesium.Color.ORANGE : (float.type === 'bgc' ? Cesium.Color.fromCssColorString('#c084fc') : Cesium.Color.YELLOW),
                  outlineColor: Cesium.Color.BLACK,
                  outlineWidth: 1,
                  id: { _floatData: float } 
               });
            }
         });
         primitivesRef.current = viewer.scene.primitives.add(points);
     }

     // Draw trajectory polyline (Entities are fine here for 1 trajectory)
     if (trajectoryPoints && trajectoryPoints.length > 1) {
         const coords = [];
         trajectoryPoints.forEach(p => {
             coords.push(p.lon, p.lat);
         });
         
         viewer.entities.add({
             polyline: {
                 positions: Cesium.Cartesian3.fromDegreesArray(coords),
                 width: 4,
                 material: new Cesium.PolylineGlowMaterialProperty({
                     glowPower: 0.2,
                     color: Cesium.Color.RED
                 }),
                 clampToGround: true
             }
         });
     }

     // Draw trajectory points
     if (trajectoryPoints) {
         trajectoryPoints.forEach((pt, idx) => {
             const isLast = idx === trajectoryPoints.length - 1;
             const ent = viewer.entities.add({
                 position: Cesium.Cartesian3.fromDegrees(pt.lon, pt.lat, 1500),
                 name: `Cycle ${pt.cycle}`,
                 description: `Date: ${pt.date} <br/> Lat: ${pt.lat.toFixed(3)}&deg; <br/> Lon: ${pt.lon.toFixed(3)}&deg;`,
                 point: {
                     pixelSize: isLast ? 10 : 6,
                     color: isLast ? Cesium.Color.fromCssColorString('#ca8a04') : Cesium.Color.WHITE,
                     outlineColor: Cesium.Color.RED,
                     outlineWidth: 2
                 },
                 label: {
                     text: String(pt.cycle),
                     font: '12px sans-serif',
                     fillColor: Cesium.Color.WHITE,
                     outlineColor: Cesium.Color.BLACK,
                     outlineWidth: 2,
                     style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                     pixelOffset: new Cesium.Cartesian2(12, 0),
                     horizontalOrigin: Cesium.HorizontalOrigin.LEFT,
                     verticalOrigin: Cesium.VerticalOrigin.CENTER,
                     disableDepthTestDistance: Number.POSITIVE_INFINITY
                 }
             });
             ent._floatData = pt;
         });
     }

     viewer.scene.requestRender();

  }, [activeFloats, showActive, floatFilter, selectedFloat, trajectoryPoints]);

  return (
    <div style={{ width: '100%', height: '100vh', position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      
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
          <div className="hint-row">🖱️ Middle: Tilt</div>
          <div className="hint-row">⚲ Scroll: Zoom</div>
        </div>
      </div>
    </div>
  );
};

export default CesiumView;
