import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const defaultRuntimeTarget = "http://127.0.0.1:13120";

export default defineConfig(({ mode }) => {
	const env = loadEnv(mode, process.cwd(), "");
	const runtimeTarget =
		env.SCRIBE_SPA_PROXY_TARGET ||
		env.VITE_SCRIBE_PROXY_TARGET ||
		defaultRuntimeTarget;
	const apiProxy = {
		target: runtimeTarget,
		changeOrigin: true,
	};

	return {
		base: "/static/spa/",
		plugins: [react()],
		build: {
			outDir: "../../src/scribe/web/static/spa",
			emptyOutDir: true,
			manifest: true,
		},
		server: {
			proxy: {
				"/api": apiProxy,
				"/transcripts": apiProxy,
				"/admin": apiProxy,
				"/jobs": apiProxy,
				"/feed.xml": apiProxy,
			},
		},
		preview: {
			proxy: {
				"/api": apiProxy,
				"/transcripts": apiProxy,
				"/admin": apiProxy,
				"/jobs": apiProxy,
				"/feed.xml": apiProxy,
			},
		},
	};
});
