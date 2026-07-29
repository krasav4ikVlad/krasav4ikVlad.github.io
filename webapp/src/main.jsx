import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AppRoot } from '@telegram-apps/telegram-ui';

import '@telegram-apps/telegram-ui/dist/styles.css';
import './app.css';

import { App } from './App';
import { boot, currentAppearance, currentPlatform, onAppearanceChange } from './telegram';

boot();

function Root() {
  const [appearance, setAppearance] = useState(currentAppearance);

  useEffect(() => onAppearanceChange(() => setAppearance(currentAppearance())), []);

  return (
    <AppRoot platform={currentPlatform()} appearance={appearance}>
      <App />
    </AppRoot>
  );
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
