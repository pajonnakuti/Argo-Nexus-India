import React, { useState, useRef } from 'react';
import MapComponent from './components/MapComponent';
import Sidebar from './components/Sidebar';
import DataPreview from './components/DataPreview';
import axios from 'axios';

function App() {
  const [bounds, setBounds] = useState(null);
  const [params, setParams] = useState({
    startDate: '2020-01-01',
    endDate: '2024-12-31',
    minDepth: 0,
    maxDepth: 2000,
    type: 'core',
    selectedVars: ['TEMP', 'PSAL', 'PRES', 'All QC Flags', 'All Available Parameters']
  });
  const [loading, setLoading] = useState(false);
  const [wsStatus, setWsStatus] = useState('idle');
  const [percent, setPercent] = useState(0);
  const [previewData, setPreviewData] = useState(null);
  const [logs, setLogs] = useState([]);
  const wsTimeoutRef = useRef(null);
  const wsRef = useRef(null);

  const cancelRequest = () => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setLoading(false);
    setWsStatus('canceled');
    setLogs(prev => [...prev, 'Request canceled by user.']);
  };

  const handleBoundsChange = (newBounds) => {
    setBounds(newBounds);
  };

  const handleSubmit = () => {
    if (!bounds) {
      alert('Please select an area on the map first');
      return;
    }

    setLoading(true);
    setPreviewData(null);
    setLogs(['Initializing connection...']);

    // WebSocket Connection
    const ws = new WebSocket('ws://localhost:8000/api/ws');
    wsRef.current = ws;

    ws.onopen = () => {
      setWsStatus('connected');
      setPercent(0);
      setLogs(prev => [...prev, 'WebSocket connected. Sending request...']);
      ws.send(JSON.stringify({ bounds, params }));
      wsTimeoutRef.current = setTimeout(() => {
        if (ws.readyState === WebSocket.OPEN) {
          setLogs(prev => [...prev, 'Warning: no response from server in 180s. Closing connection.']);
          ws.close();
          setLoading(false);
        }
      }, 180000); // 180 seconds timeout if no complete
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'log') {
        setLogs(prev => [...prev, data.message]);
      } else if (data.type === 'progress') {
        const progress = Number(data.value);
        setPercent(progress >= 0 && progress <= 100 ? progress : 0);
        setLogs(prev => [...prev, `Progress: ${progress}%`]);
      } else if (data.type === 'error') {
        setLogs(prev => [...prev, `Error: ${data.message}`]);
        alert('Error: ' + data.message);
        setLoading(false);
        setWsStatus('error');
        ws.close();
      } else if (data.type === 'complete') {
        setPercent(100);
        clearTimeout(wsTimeoutRef.current);
        setTimeout(() => { wsTimeoutRef.current = null; }, 0);

        setLogs(prev => [...prev, 'Completed. Starting download...']);

        // Download CSV with BOM
        const blob = new Blob(["\ufeff" + data.csv], { type: 'text/csv;charset=utf-8;' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', data.filename);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);

        // Show Preview
        setPreviewData(data.csv);
        setLoading(false);
        setWsStatus('finished');
        ws.close();
      }
    };

    ws.onerror = (error) => {
      console.error('WebSocket Error:', error);
      setLogs(prev => [...prev, 'WebSocket connection error']);
      setWsStatus('error');
      setLoading(false);
      alert('Connection failed.');
      if (wsTimeoutRef.current) {
        clearTimeout(wsTimeoutRef.current);
        wsTimeoutRef.current = null;
      }
    };

    ws.onclose = (event) => {
      if (loading && wsStatus !== 'finished') {
        setLogs(prev => [...prev, `Connection closed unexpectedly (code ${event.code}).`]);
      }
      if (wsTimeoutRef.current) {
        clearTimeout(wsTimeoutRef.current);
        wsTimeoutRef.current = null;
      }
      setWsStatus('closed');
      setLoading(false);
    };
  };

  return (
    <div className="app">
      <Sidebar
        bounds={bounds}
        params={params}
        setParams={setParams}
        onSubmit={handleSubmit}
        onBoundsChange={handleBoundsChange}
      />
      <div className="map-container">
        <MapComponent onBoundsChange={setBounds} bounds={bounds} />
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
              <div className="loader-text">Processing Request... (status: {wsStatus})</div>
              <div className="progress-bar">
                <div className="progress-fill" style={{ width: `${percent}%` }}></div>
              </div>
              <button className="btn-cancel" onClick={cancelRequest}>Cancel</button>
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
