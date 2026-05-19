import React, { useState } from 'react';
import MapComponent from './components/MapComponent';
import Sidebar from './components/Sidebar';
import DataPreview from './components/DataPreview';
import GridPreview from './components/GridPreview';
import axios from 'axios';

function App() {
  const [bounds, setBounds] = useState(null);
  const today = new Date();
  const thirtyDaysAgo = new Date(today);
  thirtyDaysAgo.setDate(today.getDate() - 30);
  
  const formatDate = (d) => d.toISOString().split('T')[0];

  const [params, setParams] = useState({
    startDate: formatDate(thirtyDaysAgo),
    endDate: formatDate(today),
    minDepth: 0,
    maxDepth: 2000,
    type: 'core',
    selectedVars: ['TEMP', 'PSAL', 'PRES', 'All QC Flags', 'All Available Parameters']
  });
  const [loading, setLoading] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [floatCounts, setFloatCounts] = useState({ total: 0, core: 0, bgc: 0, ocean: {}, inst: {} });

  // ── New state for download format & gridding ──
  const [downloadFormat, setDownloadFormat] = useState('csv');
  const [gridConfig, setGridConfig] = useState({
    variable: 'TEMP',
    depth_level: 10,
    depth_tolerance: 50,
    resolution: 0.5,
    method: 'oi',
    corr_length: 2.0,
    snr: 1.0
  });
  const [gridData, setGridData] = useState(null);
  const [gridLoading, setGridLoading] = useState(false);

  const handleBoundsChange = (newBounds) => {
    setBounds(newBounds);
  };

  // ── Multi-format export handler ──
  const handleExport = async (format) => {
    if (!bounds) {
      alert('Please select an area on the map first');
      return;
    }

    const fmt = format || downloadFormat;
    setLoading(true);
    setLogs([`Exporting data as ${fmt === 'diva' ? 'DIVA Gridded NetCDF' : fmt.toUpperCase()}...`]);

    try {
      // Build request body based on format
      let url, body;

      if (fmt === 'diva') {
        url = 'http://localhost:8000/api/export/diva';
        body = {
          bounds,
          params: {
            startDate: params.startDate,
            endDate: params.endDate,
            minDepth: params.minDepth,
            maxDepth: params.maxDepth,
            type: params.type,
          },
          variable: gridConfig.variable,
          depth_level: gridConfig.depth_level,
          depth_tolerance: gridConfig.depth_tolerance,
          resolution: gridConfig.resolution,
          method: gridConfig.method,
          corr_length: gridConfig.corr_length,
          snr: gridConfig.snr,
        };
      } else {
        url = `http://localhost:8000/api/export/${fmt}`;
        body = {
          bounds,
          params: {
            startDate: params.startDate,
            endDate: params.endDate,
            minDepth: params.minDepth,
            maxDepth: params.maxDepth,
            type: params.type,
          },
          selectedVars: params.selectedVars
        };
      }

      // Step 1: Submit job (returns instantly)
      const submitResp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });

      if (!submitResp.ok) {
        const err = await submitResp.json();
        throw new Error(err.detail || `Export failed with status ${submitResp.status}`);
      }

      const { job_id } = await submitResp.json();
      setLogs(prev => [...prev, `📋 Export job submitted (${job_id.slice(0,8)}...)`]);

      // Step 2: Poll for progress
      let status = 'queued';
      while (status !== 'done' && status !== 'error') {
        await new Promise(r => setTimeout(r, 2000)); // Poll every 2s
        const pollResp = await fetch(`http://localhost:8000/api/export/status/${job_id}`);
        
        if (!pollResp.ok) {
          throw new Error(`Export job lost (server returned ${pollResp.status}). Please retry.`);
        }
        
        const pollData = await pollResp.json();
        status = pollData.status;

        if (status === 'running') {
          if (pollData.total === 0) {
            // ERDDAP stream mode: total is 0, progress is bytes downloaded
            const mbDownloaded = (pollData.progress / (1024 * 1024)).toFixed(1);
            setLogs(prev => {
              const filtered = prev.filter(l => !l.startsWith('⏳'));
              return [...filtered, `⏳ Downloading from ERDDAP: ${mbDownloaded} MB...`];
            });
          } else {
            // Future known totals
            const pct = pollData.total > 0 ? Math.round((pollData.progress / pollData.total) * 100) : 0;
            setLogs(prev => {
              const filtered = prev.filter(l => !l.startsWith('⏳'));
              return [...filtered, `⏳ Processing: ${pollData.progress}/${pollData.total} items (${pct}%)`];
            });
          }
        } else if (status === 'formatting') {
          setLogs(prev => {
            const filtered = prev.filter(l => !l.startsWith('⏳'));
            return [...filtered, `⏳ Formatting ${fmt.toUpperCase()} output...`];
          });
        } else if (status === 'error') {
          throw new Error(pollData.error || 'Export failed');
        }
      }

      // Step 3: Download the file
      setLogs(prev => [...prev, `⬇️ Downloading file...`]);
      const downloadResp = await fetch(`http://localhost:8000/api/export/download/${job_id}`);
      if (!downloadResp.ok) {
        const err = await downloadResp.json();
        throw new Error(err.detail || 'Download failed');
      }

      // Determine filename from Content-Disposition header
      const disposition = downloadResp.headers.get('Content-Disposition');
      let filename = fmt === 'diva' ? `argo_diva_${gridConfig.variable}.nc` : `argo_export.${fmt}`;
      if (disposition) {
        const match = disposition.match(/filename="?([^"]+)"?/);
        if (match) filename = match[1];
      }

      const blob = await downloadResp.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.setAttribute('download', filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(blobUrl);

      const label = fmt === 'diva' ? 'DIVA Gridded NetCDF' : fmt.toUpperCase();
      setLogs(prev => [...prev, `✅ ${label} download complete: ${filename}`]);
      
      // Show preview for CSV format
      if (fmt === 'csv') {
        const text = await blob.text();
        setPreviewData(text);
      }
    } catch (err) {
      alert('Export error: ' + err.message);
      setLogs(prev => [...prev, `❌ Export failed: ${err.message}`]);
    } finally {
      setLoading(false);
    }
  };

  // ── Gridded product handler ──
  const handleGridSubmit = async () => {
    if (!bounds) {
      alert('Please select an area on the map first');
      return;
    }

    setGridLoading(true);
    setGridData(null);
    setLogs(['Generating gridded product...']);

    try {
      const response = await fetch('http://localhost:8000/api/grid', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bounds,
          params: {
            startDate: params.startDate,
            endDate: params.endDate,
            minDepth: params.minDepth,
            maxDepth: params.maxDepth,
            type: params.type,
          },
          ...gridConfig
        })
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || `Grid generation failed with status ${response.status}`);
      }

      const data = await response.json();
      setGridData(data);
      setLogs(prev => [...prev, `✅ Gridded ${data.variable} at ${data.depth_level}m — ${data.n_observations} obs → ${data.lats.length}×${data.lons.length} grid`]);
    } catch (err) {
      alert('Grid generation error: ' + err.message);
      setLogs(prev => [...prev, `❌ Grid failed: ${err.message}`]);
    } finally {
      setGridLoading(false);
    }
  };

  // ── Download gridded product as NetCDF ──
  const handleGridDownload = async () => {
    if (!bounds) return;

    setGridLoading(true);
    try {
      const response = await fetch('http://localhost:8000/api/grid/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bounds,
          params: {
            startDate: params.startDate,
            endDate: params.endDate,
            minDepth: params.minDepth,
            maxDepth: params.maxDepth,
            type: params.type,
          },
          ...gridConfig
        })
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Download failed');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `argo_gridded_${gridConfig.variable}_${gridConfig.depth_level}m.nc`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      alert('Download error: ' + err.message);
    } finally {
      setGridLoading(false);
    }
  };

  // ── Legacy WebSocket search (kept for backward compat) ──
  const handleSubmit = () => {
    if (!bounds) {
      alert('Please select an area on the map first');
      return;
    }

    // Use the new multi-format export
    handleExport(downloadFormat);
  };

  return (
    <div className="app">
      <Sidebar
        bounds={bounds}
        params={params}
        setParams={setParams}
        onSubmit={handleSubmit}
        onBoundsChange={handleBoundsChange}
        floatCounts={floatCounts}
        downloadFormat={downloadFormat}
        setDownloadFormat={setDownloadFormat}
        gridConfig={gridConfig}
        setGridConfig={setGridConfig}
        onGridSubmit={handleGridSubmit}
        gridLoading={gridLoading}
        onExport={handleExport}
      />
      <div className="map-container">
        <MapComponent 
          onBoundsChange={setBounds} 
          bounds={bounds} 
          onFloatCountsUpdate={setFloatCounts}
          startDate={params.startDate}
          endDate={params.endDate}
        />
        {/* Grid visualization overlay */}
        {gridData && (
          <GridPreview
            gridData={gridData}
            onClose={() => setGridData(null)}
            onDownload={handleGridDownload}
          />
        )}

        {previewData && (
          <DataPreview 
            csvData={previewData} 
            onClose={() => setPreviewData(null)} 
          />
        )}
        {loading && (
          <div className="loader-overlay">
            <div className="loader-content">
              <div className="loader-spinner"></div>
              <div className="loader-text">Processing Request...</div>
              <div className="log-terminal">
                {logs.map((log, i) => (
                  <div key={i} className="log-line">{log}</div>
                ))}
                <div ref={(el) => el?.scrollIntoView({ behavior: 'smooth' })} />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
