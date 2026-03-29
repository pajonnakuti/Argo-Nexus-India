import React, { useState } from 'react';
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
  const [previewData, setPreviewData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [floatCounts, setFloatCounts] = useState({ total: 0, core: 0, bgc: 0, ocean: {}, inst: {} });

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
    let csvChunks = [];      // accumulator for streamed CSV
    let chunkFilename = '';

    ws.onopen = () => {
      ws.send(JSON.stringify({ bounds, params }));
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'log') {
        setLogs(prev => [...prev, data.message]);
      } 
      else if (data.type === 'error') {
        alert('Error: ' + data.message);
        setLoading(false);
        ws.close();
      } 
      else if (data.type === 'csv_chunk') {
        // Accumulate streamed CSV chunks
        csvChunks.push(data.data);
        chunkFilename = data.filename;
        setLogs(prev => [...prev, `Receiving chunk ${data.chunk_index + 1}/${data.total_chunks}...`]);
        
        if (data.is_last) {
          const fullCsv = csvChunks.join('');
          const blob = new Blob(["\ufeff" + fullCsv], { type: 'text/csv;charset=utf-8;' });
          const url = window.URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.setAttribute('download', chunkFilename);
          document.body.appendChild(link);
          link.click();
          link.remove();
          
          setPreviewData(fullCsv);
          setLoading(false);
          csvChunks = [];
          ws.close();
        }
      }
      else if (data.type === 'complete') {
        // Small CSV sent in one message (legacy path)
        const blob = new Blob(["\ufeff" + data.csv], { type: 'text/csv;charset=utf-8;' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', data.filename);
        document.body.appendChild(link);
        link.click();
        link.remove();
        
        // Show Preview
        setPreviewData(data.csv);
        setLoading(false);
        ws.close();
      }
    };

    ws.onerror = (error) => {
      console.error('WebSocket Error:', error);
      setLoading(false);
      alert('Connection failed.');
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
        floatCounts={floatCounts}
      />
      <div className="map-container">
        <MapComponent onBoundsChange={setBounds} bounds={bounds} onFloatCountsUpdate={setFloatCounts} />
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
