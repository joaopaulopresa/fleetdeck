import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API is the cluster's ingress.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8090" } },
});
