import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// В режиме разработки /api проксируется на бэкенд (VITE_BACKEND_URL), чтобы не настраивать CORS.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.VITE_BACKEND_URL || "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      // Явно IPv4: иначе на Windows Vite слушает только ::1 и localhost из браузера может не открыться.
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": { target: backend, changeOrigin: true, ws: true, rewrite: (p) => p.replace(/^\/api/, "") },
      },
    },
  };
});
