import React from "react";
import { createRoot } from "react-dom/client";

import { DesignSystemPlayground } from "./DesignSystemPlayground";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { CMDK_OPEN_EVENT } from "./constants";
import { useRoute } from "./hooks/useRoute";
import { useTweaks } from "./hooks/useTweaks";
import { JobDetail } from "./pages/JobDetail";
import { Library } from "./pages/Library";
import { Ops } from "./pages/Ops";
import { Queue } from "./pages/Queue";
import { Settings } from "./pages/Settings";
import { Transcript } from "./pages/Transcript";
import "./styles.css";

function App() {
	const { route, navigate } = useRoute();
	const { tweaks, setTheme, replaceTweaks } = useTweaks();

	React.useEffect(() => {
		const open = () => {
			console.info(
				"Command palette requested; #40 will mount the palette body.",
			);
		};
		const keydown = (event: KeyboardEvent) => {
			if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
				event.preventDefault();
				document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT));
			}
		};
		document.addEventListener(CMDK_OPEN_EVENT, open);
		document.addEventListener("keydown", keydown);
		return () => {
			document.removeEventListener(CMDK_OPEN_EVENT, open);
			document.removeEventListener("keydown", keydown);
		};
	}, []);

	if (window.location.pathname === "/__spa__/__playground__") {
		return <DesignSystemPlayground />;
	}

	return (
		<div className="app-shell">
			<TopBar theme={tweaks.theme} onThemeChange={setTheme} />
			<div className="shell-body">
				<Sidebar route={route} navigate={navigate} />
				<main className="content-pane">
					{route.page === "queue" ? (
						<Queue navigate={navigate} />
					) : route.page === "job" ? (
						<JobDetail id={route.params.id} navigate={navigate} />
					) : route.page === "library" ? (
						<Library
							layout={tweaks.libraryLayout}
							route={route}
							navigate={navigate}
						/>
					) : route.page === "transcript" ? (
						<Transcript id={route.params.id} navigate={navigate} />
					) : route.page === "ops" ? (
						<Ops navigate={navigate} />
					) : route.page === "settings" ? (
						<Settings
							tweaks={tweaks}
							setTheme={setTheme}
							replaceTweaks={replaceTweaks}
						/>
					) : (
						<Placeholder page={route.page} id={route.params.id} />
					)}
				</main>
			</div>
		</div>
	);
}

function Placeholder({ page, id }: { page: string; id?: number }) {
	return (
		<section className="placeholder-pane">
			<p className="eyebrow">{page}</p>
			<h1>pages coming online — see issue #27</h1>
			{id !== undefined ? (
				<span className="active-filter">id: {id}</span>
			) : null}
		</section>
	);
}

const root = document.getElementById("root");

if (root === null) {
	throw new Error("Missing #root element");
}

createRoot(root).render(
	<React.StrictMode>
		<App />
	</React.StrictMode>,
);
