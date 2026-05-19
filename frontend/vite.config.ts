/// <reference types="vitest" />
import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"
import { resolve } from "node:path"

export default defineConfig(({ mode }) => ({
    plugins: [vue()],
    resolve: {
        alias: {
            "@": resolve(__dirname, "./src"),
        },
    },
    server: {
        host: true,
        port: 80,
        watch: { usePolling: true },
        proxy:
            mode === "development"
                ? {
                      "/api": {
                          target: process.env.VITE_BACKEND_URL || "http://localhost:1314",
                          changeOrigin: true,
                      },
                  }
                : undefined,
    },
    test: {
        environment: "happy-dom",
        include: ["src/**/*.{test,spec}.ts"],
    },
}))
