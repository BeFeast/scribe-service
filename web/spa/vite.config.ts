import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiProxyTarget =
	process.env.SCRIBE_API_PROXY_TARGET ?? "http://10.10.0.13:13120";

export default defineConfig({
	base: "/static/spa/",
	plugins: [react()],
	build: {
		outDir: "../../src/scribe/web/static/spa",
		emptyOutDir: true,
		manifest: true,
	},
	server: {
		proxy: {
			"/api": {
				target: apiProxyTarget,
				changeOrigin: true,
			},
			"/transcripts": {
				target: apiProxyTarget,
				changeOrigin: true,
			},
			"/admin": {
				target: apiProxyTarget,
				changeOrigin: true,
			},
		},
	},
	preview: {
		proxy: {
			"/api": {
				target: apiProxyTarget,
				changeOrigin: true,
			},
			"/transcripts": {
				target: apiProxyTarget,
				changeOrigin: true,
			},
			"/admin": {
				target: apiProxyTarget,
				changeOrigin: true,
			},
		},
	},
});
