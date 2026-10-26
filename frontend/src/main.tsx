import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/inter-tight/500.css'
import '@fontsource/inter-tight/600.css'
import '@fontsource/inter-tight/700.css'
import '@fontsource/inter-tight/800.css'
import '@fontsource/space-mono/400.css'
import '@fontsource/space-mono/700.css'
import './index.css'
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
