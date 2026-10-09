import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// 构建产物直接输出到 web/dist：main.py 检测到 web/dist/index.html 存在时
// 会优先加载它，否则回退到 legacy 单页，因此切换/回滚都是零风险的。
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: fileURLToPath(new URL('../web/dist', import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      '/chat': 'http://127.0.0.1:8000',
      '/api': 'http://127.0.0.1:8000',
      '/tools': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/static': 'http://127.0.0.1:8000',
    },
  },
})
