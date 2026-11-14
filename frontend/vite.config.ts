import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        configure(proxy) {
          proxy.on("error", (_error, _request, response) => {
            if (
              !("writeHead" in response) ||
              typeof response.writeHead !== "function" ||
              response.headersSent
            ) {
              return;
            }

            const body = JSON.stringify({
              code: "BACKEND_UNAVAILABLE",
              detail:
                "Нет соединения с сервером. Проверьте, запущен ли FastAPI",
            });
            response.writeHead(503, {
              "Content-Type": "application/json; charset=utf-8",
              "Content-Length": Buffer.byteLength(body),
            });
            response.end(body);
          });
        },
      },
    },
  },
});
