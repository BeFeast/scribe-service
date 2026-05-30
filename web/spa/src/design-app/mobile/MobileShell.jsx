// Mobile shell — Wave 1 / Issue #275
//
// Literal port of the bottom tab bar, collapsing nav bar, per-tab push/pop
// nav stack, and slide transitions from `Scribe iOS.html` (mobile design
// source, SHA-256 421c930d9f2d5c1549dc632760f796992630a27eaa6fa38f28ff25584bc3ebb9).
//
// Source mapping (Scribe iOS.html → this file):
//   ~787 const TABS              → MOBILE_TABS
//   ~795 function renderTabbar   → <TabBar />
//   ~810 function buildPage      → <MobileShell> render (navbar + scroller)
//   ~849 navStack                → React state via useRoute (hash router)
//   ~851 function setRoot        → navigate({page: tab})
//   ~860 function push           → navigate({page, params}) + slide CSS
//   ~876 function navBack        → window.history.back()
//   ~893 function switchTab      → tab onClick → switchTab handler
//
// Translation contract:
//   - Vanilla JS → React: same TABS array shape, same scroll thresholds
//     (>26px adds .scrolled), same class names defined in Wave 0 styles.css.
//   - Manual DOM (innerHTML / appendChild / insertBefore / offsetWidth /
//     setTimeout 360ms) is replaced with React state + a `transitionKey`
//     that drives a CSS slide via the .mobile-page.enter rule. The
//     prototype's `state.tab` becomes derived from the current route
//     (`tabRouteFor(route)`); navStack becomes `window.history.back()`.
//   - The 5-tab IA (Library, Queue, Capture-orb, Ops, Settings) is preserved
//     verbatim. transcript drill-in highlights Library; job drill-in
//     highlights Queue; Capture is an action (opens sheet, never navigates).
//     The existing #/history route stays reachable from Library/Ops
//     affordances (no tab) — IA decision recorded in PRD addendum.
//   - SVGs are reused from existing `icons.jsx` (IconLibrary/IconQueue/
//     IconOps/IconSettings/IconPlus). No glyph is redrawn here.

import React from "react";
import {
	IconLibrary,
	IconOps,
	IconPlus,
	IconQueue,
	IconSettings,
} from "../icons.jsx";

/* ── TABS array (verbatim port) ───────────────────────────────────────── */

export const MOBILE_TABS = [
	{ id: "library", label: "Library", Icon: IconLibrary },
	{ id: "queue", label: "Queue", Icon: IconQueue },
	{ id: "capture", label: "Capture", Icon: IconPlus, capture: true },
	{ id: "ops", label: "Ops", Icon: IconOps },
	{ id: "settings", label: "Settings", Icon: IconSettings },
];

/* ── Route ↔ active-tab mapping (per IA-preserved decision) ──────────── */

export function tabRouteFor(tabId) {
	// Capture is an action (opens sheet); it is never a route.
	if (tabId === "capture") return null;
	return tabId;
}

export function activeTabFor(route) {
	switch (route.page) {
		case "library":
		case "transcript": // transcript drill-in highlights Library
			return "library";
		case "queue":
		case "job": // job drill-in highlights Queue
			return "queue";
		case "ops":
			return "ops";
		case "settings":
			return "settings";
		case "history":
			// History has no tab in the 5-tab IA; reachable from Library/Ops.
			// While viewing /history, none of the tabs is active.
			return null;
		default:
			return "library";
	}
}

/* ── TabBar (port of renderTabbar + onclick wiring) ──────────────────── */

export function TabBar({ active, onTabSelect, onCapture, badges }) {
	return (
		<nav className="tabbar" aria-label="Primary">
			{MOBILE_TABS.map((tab) => {
				if (tab.capture) {
					return (
						<button
							key={tab.id}
							type="button"
							className="tab capture"
							aria-label="Capture"
							onClick={onCapture}
						>
							<span className="cap-orb">
								<IconPlus size={22} />
							</span>
						</button>
					);
				}
				const isActive = active === tab.id;
				const badge = badges?.[tab.id];
				return (
					<button
						key={tab.id}
						type="button"
						className={isActive ? "tab active" : "tab"}
						aria-current={isActive ? "page" : undefined}
						onClick={() => onTabSelect(tab.id)}
					>
						<tab.Icon size={25} />
						<span className="tlabel">{tab.label}</span>
						{badge ? (
							<span className="tab-badge" aria-label={badge.aria}>
								{badge.text}
							</span>
						) : null}
					</button>
				);
			})}
		</nav>
	);
}

/* ── MobileNavbar (port of .navbar + .nb-title + .nb-back) ───────────── */

export function MobileNavbar({ title, large, sub, canBack, onBack, scrolled }) {
	return (
		<div className={scrolled ? "mobile-navbar scrolled" : "mobile-navbar"}>
			<div className="nb-side">
				{canBack ? (
					<button
						type="button"
						className="nb-btn nb-back"
						onClick={onBack}
						aria-label="Back"
					>
						<svg
							aria-hidden="true"
							focusable="false"
							width="13"
							height="20"
							viewBox="0 0 13 20"
							fill="none"
							stroke="currentColor"
							strokeWidth="2.5"
							strokeLinecap="round"
							strokeLinejoin="round"
						>
							<path d="M11 1.5L2 10l9 8.5" />
						</svg>
						<span>Back</span>
					</button>
				) : null}
			</div>
			<div className="nb-title">{title}</div>
			<div className="nb-side right" />
			{large !== false ? (
				<div className="large-title-wrap" data-collapse-when-scrolled>
					<h1 className="large-title">{large ?? title}</h1>
					{sub ? <p className="large-sub">{sub}</p> : null}
				</div>
			) : null}
		</div>
	);
}

/* ── MobileShell (port of buildPage skeleton) ───────────────────────── */
//
// In the prototype, buildPage(route) builds <div class="navbar"> + <div
// class="scroller"> {large-title-wrap, body} </div> for each route, then
// setRoot/push/pop swap the .page elements. In React, the page node is
// passed in as `children` (rendered by the existing main.jsx switch on
// route.page), and only the chrome (navbar + scroller wrapper + tab bar +
// optional back button) is the responsibility of this shell. setRoot /
// switchTab map onto navigate({page:tab}); push maps onto navigate
// ({page,params}); navBack maps onto window.history.back().

export function MobileShell({
	route,
	navigate,
	onCapture,
	badges,
	title,
	large,
	sub,
	canBack,
	children,
}) {
	const scrollerRef = React.useRef(null);
	const [scrolled, setScrolled] = React.useState(false);

	// Port of `scroller.addEventListener("scroll", () => navbar.classList
	// .toggle("scrolled", scroller.scrollTop > 26))`.
	React.useEffect(() => {
		const el = scrollerRef.current;
		if (!el) return undefined;
		function onScroll() {
			setScrolled(el.scrollTop > 26);
		}
		el.addEventListener("scroll", onScroll, { passive: true });
		return () => el.removeEventListener("scroll", onScroll);
	}, []);

	// Reset scroll position when route changes (port of the prototype
	// re-mounting a fresh .page on every push/pop/switchTab).
	const routeKey = `${route.page}:${route.params?.id ?? ""}`;
	React.useEffect(() => {
		// `routeKey` is read so the lint deps are honest — the body does not
		// otherwise need it; the effect just fires on every key change.
		void routeKey;
		const el = scrollerRef.current;
		if (el) el.scrollTop = 0;
		setScrolled(false);
	}, [routeKey]);

	const active = activeTabFor(route);
	const onTabSelect = React.useCallback(
		(tabId) => {
			const target = tabRouteFor(tabId);
			if (!target) return;
			navigate(target, {});
		},
		[navigate],
	);
	const onBack = React.useCallback(() => {
		// Port of navBack(): use the real hash-router history rather than
		// the prototype's manual navStack (the hash router already records
		// it on every navigate()).
		window.history.back();
	}, []);

	const transitionKey = `${route.page}:${route.params?.id ?? ""}`;

	return (
		<>
			<MobileNavbar
				title={title}
				large={large}
				sub={sub}
				canBack={canBack}
				onBack={onBack}
				scrolled={scrolled}
			/>
			<div
				ref={scrollerRef}
				className="mobile-scroller"
				data-transition-key={transitionKey}
			>
				<div className="mobile-page" key={transitionKey}>
					{children}
				</div>
			</div>
			<TabBar
				active={active}
				onTabSelect={onTabSelect}
				onCapture={onCapture}
				badges={badges}
			/>
		</>
	);
}
