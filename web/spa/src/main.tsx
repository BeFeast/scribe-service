import React from "react";
import { createRoot } from "react-dom/client";

import { DesignSystemPlayground } from "./DesignSystemPlayground";
import { CommandPalette } from "./components/CommandPalette";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { useRoute } from "./hooks/useRoute";
import { useTweaks } from "./hooks/useTweaks";
import type { DisplayCurrency } from "./lib/currency";
import { parseDisplayCurrency } from "./lib/currency";
import { JobDetail } from "./pages/JobDetail";
import { Library } from "./pages/Library";
import { Ops } from "./pages/Ops";
import { Queue } from "./pages/Queue";
import { Settings } from "./pages/Settings";
import { Transcript } from "./pages/Transcript";
import "./styles.css";

const CONFIG_SAVED_EVENT = "scribe-config-saved";

function App() {
	const { route, navigate } = useRoute();
	const { tweaks, setTheme, replaceTweaks } = useTweaks();
	const [displayCurrency, setDisplayCurrency] =
		React.useState<DisplayCurrency>("USD");

	const loadDisplayCurrency = React.useCallback(async () => {
		try {
			const response = await fetch("/api/config");
			if (!response.ok) {
				return;
			}
			const body = (await response.json()) as {
				config?: { display_currency?: { value?: unknown } };
			};
			setDisplayCurrency(
				parseDisplayCurrency(body.config?.display_currency?.value),
			);
		} catch {
			// Display falls back to USD if the runtime config endpoint is unavailable.
		}
	}, []);

	React.useEffect(() => {
		void loadDisplayCurrency();
		document.addEventListener(CONFIG_SAVED_EVENT, loadDisplayCurrency);
		return () =>
			document.removeEventListener(CONFIG_SAVED_EVENT, loadDisplayCurrency);
	}, [loadDisplayCurrency]);

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
							displayCurrency={displayCurrency}
							route={route}
							navigate={navigate}
						/>
					) : route.page === "transcript" ? (
						<Transcript
							id={route.params.id}
							displayCurrency={displayCurrency}
							navigate={navigate}
						/>
					) : route.page === "ops" ? (
						<Ops displayCurrency={displayCurrency} navigate={navigate} />
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
			<CommandPalette navigate={navigate} />
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
