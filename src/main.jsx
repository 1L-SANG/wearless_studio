import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from '@/App.jsx';
import { ToastProvider } from '@/components/ui.jsx';
import '@/styles/tokens.css';
import '@/styles/app.css';
import '@/styles/features.css';
import '@/styles/moveable.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <App />
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>
);
