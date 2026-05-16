import React from "react";

export type RoutePage =
	| "library"
	| "transcript"
	| "queue"
	| "job"
	| "ops"
	| "settings";

export type Route = {
	page: RoutePage;
	params: {
		id?: number;
		tag?: string;
	};
};

type RouteAction =
	| { type: "navigate"; route: Route }
	| { type: "sync"; route: Route };

const DEFAULT_ROUTE: Route = { page: "library", params: {} };

function routeReducer(_state: Route, action: RouteAction): Route {
	return action.route;
}

function parseId(value: string | undefined): number | undefined {
	if (value === undefined) {
		return undefined;
	}
	const id = Number.parseInt(value, 10);
	return Number.isFinite(id) ? id : undefined;
}

function routeFromHash(hash: string): Route {
	const raw = hash.startsWith("#") ? hash.slice(1) : hash;
	const normalized = raw.startsWith("/") ? raw.slice(1) : raw;
	const [path = "", query = ""] = normalized.split("?");
	const params = new URLSearchParams(query);
	const [page = "library", id] = path.split("/");
	const tag = params.get("tag") ?? undefined;

	switch (page) {
		case "transcript":
			return { page, params: { id: parseId(id), tag } };
		case "queue":
		case "ops":
		case "settings":
			return { page, params: { tag } };
		case "job":
			return { page: "job", params: { id: parseId(id), tag } };
		default:
			return { page: "library", params: { tag } };
	}
}

function routeToHash(route: Route): string {
	const parts: string[] = [route.page];
	if (
		(route.page === "transcript" || route.page === "job") &&
		route.params.id !== undefined
	) {
		parts.push(String(route.params.id));
	}
	const params = new URLSearchParams();
	if (route.params.tag !== undefined) {
		params.set("tag", route.params.tag);
	}
	const query = params.toString();
	return `#/${parts.join("/")}${query ? `?${query}` : ""}`;
}

export function useRoute() {
	const [route, dispatch] = React.useReducer(routeReducer, DEFAULT_ROUTE, () =>
		routeFromHash(window.location.hash),
	);

	React.useEffect(() => {
		const sync = () =>
			dispatch({ type: "sync", route: routeFromHash(window.location.hash) });
		window.addEventListener("hashchange", sync);
		return () => window.removeEventListener("hashchange", sync);
	}, []);

	const navigate = React.useCallback((next: Route) => {
		const hash = routeToHash(next);
		if (window.location.hash === hash) {
			dispatch({ type: "navigate", route: next });
			return;
		}
		window.location.hash = hash;
	}, []);

	return { route, navigate };
}
