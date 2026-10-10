import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import './style.css';

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });
createRoot(document.getElementById('root')!).render(
  <StrictMode><QueryClientProvider client={client}><ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#2563eb', borderRadius: 8, fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif' } }}><AntApp><App/></AntApp></ConfigProvider></QueryClientProvider></StrictMode>,
);
