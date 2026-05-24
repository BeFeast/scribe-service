import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const runtimeTarget = "http://10.10.0.13:13120";
const apiProxy = {
	target: runtimeTarget,
	changeOrigin: true,
};

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
});
