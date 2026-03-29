import React, { useEffect, useRef } from 'react';

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
             navigationHelpButton: false,
             animation: false,
             timeline: false,
             baseLayerPicker: false,
             requestRenderMode: true,     // Massive CPU/GPU saver
             maximumRenderTimeChange: Infinity
         });
         
         viewerRef.current.screenSpaceEventHandler.setInputAction((movement) => {
             const pickedFeature = viewerRef.current.scene.pick(movement.position);
             if (Cesium.defined(pickedFeature) && pickedFeature.id && pickedFeature.id._floatData) {
                 if (onFloatClick) {
                     onFloatClick(pickedFeature.id._floatData);
                 }
             }
         }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
     }
  }, [onFloatClick]);

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
             coords.push(p.lon, p.lat, 1000);
         });
         
         viewer.entities.add({
             polyline: {
                 positions: Cesium.Cartesian3.fromDegreesArrayHeights(coords),
                 width: 3,
                 material: Cesium.Color.RED
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

  return <div ref={containerRef} style={{ width: '100%', height: '100vh', position: 'relative' }} />;
};

export default CesiumView;
