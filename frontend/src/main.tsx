import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { SharedPage } from './components/Privacy'
import './styles.css'

const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 10000 } } })
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={client}>{location.pathname.startsWith('/share/') ? <SharedPage token={location.pathname.slice(7)} /> : <App />}</QueryClientProvider></React.StrictMode>)
