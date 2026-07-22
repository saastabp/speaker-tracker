import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import '@mantine/core/styles.css';

import { AuthProvider } from './auth/AuthProvider';
import { DeepLinkRestorer } from './auth/DeepLinkRestorer';
import { loadRuntimeConfig, type RuntimeConfig } from './auth/runtimeConfig';
import { AppShell } from './components/AppShell';
import { Dashboard } from './pages/Dashboard';
import { Placeholder } from './pages/Placeholder';
import { theme } from './theme';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function App({ runtimeConfig }: { runtimeConfig: RuntimeConfig | null }) {
  return (
    <StrictMode>
      <MantineProvider theme={theme} defaultColorScheme="light">
        <QueryClientProvider client={queryClient}>
          <AuthProvider runtimeConfig={runtimeConfig}>
            <BrowserRouter>
              <DeepLinkRestorer />
              <Routes>
                <Route element={<AppShell />}>
                  <Route index element={<Dashboard />} />
                  <Route path="*" element={<Placeholder />} />
                </Route>
              </Routes>
            </BrowserRouter>
          </AuthProvider>
        </QueryClientProvider>
      </MantineProvider>
    </StrictMode>
  );
}

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('#root element not found');
}
const root = createRoot(rootElement);

// Prod loads /config.json (Cognito values from the CDK Frontend stack) before mounting; sandbox
// resolves to null immediately. A config failure in prod is fatal — auth can't initialize.
loadRuntimeConfig()
  .then((runtimeConfig) => root.render(<App runtimeConfig={runtimeConfig} />))
  .catch((err) => {
    console.error('Failed to load runtime configuration', err);
    root.render(
      <div style={{ fontFamily: 'sans-serif', padding: '2rem', color: '#1F3B4D' }}>
        Speaker Tracker could not load its configuration. Please retry shortly.
      </div>,
    );
  });