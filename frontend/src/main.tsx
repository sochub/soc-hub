import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// Apply the viewer's theme choice before first paint (Layout owns the toggle).
try { const t = localStorage.getItem('theme'); if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t); } catch { /* storage blocked */ }
import App from './App.tsx'
import { api } from './api/client'

// The website demo serves recorded fixtures instead of calling a backend.
// Inline MODE check (not the shared DEMO const) so normal builds drop the mock chunk entirely.
const ready = import.meta.env.MODE === 'demo' ? import('./demo/mock').then((m) => m.installDemo(api)) : Promise.resolve()

ready.then(() => createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
))
