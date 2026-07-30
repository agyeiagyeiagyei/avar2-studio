import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { selectApiMode } from './static-api';

// Probe for a live backend before first render: on a static host
// (GitHub Pages) the api object is swapped for the snapshot reader.
selectApiMode().finally(() => {
  const root = ReactDOM.createRoot(document.getElementById('root'));
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
});
