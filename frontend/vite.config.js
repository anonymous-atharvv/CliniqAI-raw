import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const apiBase = env.VITE_API_BASE_URL || "http://localhost:8000";
  const wsBase  = env.VITE_WS_URL       || "ws://localhost:8000";

  return {
    plugins: [react()],
    root: ".",
    publicDir: "public",

    define: {
      __APP_VERSION__: JSON.stringify(env.VITE_APP_VERSION || "1.0.0"),
    },

    server: {
      port: 3000,
      host: "0.0.0.0",
      proxy: {
        "/api":  { target: apiBase, changeOrigin: true },
        "/auth": { target: apiBase, changeOrigin: true },
        "/health": { target: apiBase, changeOrigin: true },
        "/ws":   { target: wsBase,  changeOrigin: true, ws: true },
      },
    },

    build: {
      outDir: "dist",
      sourcemap: false,
      minify: "esbuild",
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
          },
        },
      },
    },
  };
});
