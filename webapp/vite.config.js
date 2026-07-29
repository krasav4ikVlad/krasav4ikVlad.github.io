import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import Icons from 'unplugin-icons/vite';

export default defineConfig({
  // кабинет отдаётся с корня домена (console.rscore.app)
  base: './',
  plugins: [
    react(),
    // иконки Solar компилируются в JSX на этапе сборки:
    // в бандл попадают только те, что реально импортированы
    Icons({ compiler: 'jsx', jsx: 'react' }),
  ],
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    target: 'es2019',      // WebView в Telegram на старых Android
    sourcemap: false,
  },
  server: { host: true, port: 5173 },
});
