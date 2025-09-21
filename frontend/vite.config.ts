import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"
import { resolve } from "path"
import terminal from "vite-plugin-terminal"

export default defineConfig(({ mode }) => ({
    plugins: [
        vue(),
        terminal({
            console: "terminal",
            output: ["terminal", "console"],
        }),
    ],
    resolve: {
        alias: {
            "@": resolve(__dirname, "./src"),
        },
    },
    server: {
        host: true,
        port: 80,
        watch: {
            usePolling: true,
        },
        proxy:
            mode === "development"
                ? {
                      "/api": {
                          target:
                              process.env.VITE_BACKEND_URL ||
                              "http://localhost:1314",
                          changeOrigin: true,
                          rewrite: (path) => path.replace(/^\/api/, "/api"),
                      },
                  }
                : undefined,
    },
}))