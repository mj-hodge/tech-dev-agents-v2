import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PublicClientApplication } from '@azure/msal-browser';
import { MsalProvider } from '@azure/msal-react';
import { msalConfig } from './auth/msalConfig';
import { setMsalInstance } from './api/client';
import App from './App';
import './index.css';

const msalInstance = new PublicClientApplication(msalConfig);
setMsalInstance(msalInstance);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchInterval: 30_000,
      retry: 2,
    },
  },
});

// Initialize MSAL before rendering — ensures token cache is ready.
// handleRedirectPromise() MUST be called before any other MSAL API;
// even with loginPopup() it drains any stale redirect hash from the URL.
msalInstance.initialize().then(async () => {
  await msalInstance.handleRedirectPromise();

  // Check for existing logged-in accounts
  const accounts = msalInstance.getAllAccounts();
  if (accounts.length > 0) {
    msalInstance.setActiveAccount(accounts[0]);
  }

  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <MsalProvider instance={msalInstance}>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </MsalProvider>
    </React.StrictMode>,
  );
});
