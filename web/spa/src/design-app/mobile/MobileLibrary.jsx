// Mobile Library page — Wave 2a / Issue #276
//
// Literal port of `viewLibrary()` from the mobile design source
// `Scribe iOS.html` (~lines 909–980). The desktop Library
// (web/spa/src/design-app/library.jsx) offers three layouts
// (table / feed / cards); on phones the iOS spec is feed-only, so
// the layout token is forced to feed here. Phones surface a single
// list of `.tcard`s with a search field + segment filter (All /
// partial / top tags) and an RSS button in the navbar.
//
// Source mapping (`Scribe iOS.html` → this file):
//   ~909 function libRows               → buildRows() helper
//   ~919 function viewLibrary           → <MobileLibrary />
//   ~924 const filters                  → buildFilters()
//   ~927 const rowsHtml                 → renderRows() / <Row />
//   ~944 .searchbar / .searchfield      → <SearchBar />
//   ~945 .seg                           → <SegmentFilter />
//   ~947 navRight rss button            → <NavRightPortal /> + RSS button
//   ~957 wire(page) click + input wiring → React onClick / onChange
//   ~973 toast("RSS feed link copied")  → <Toast /> (own state, 2s)
//
// Translation contract:
//   - Vanilla JS innerHTML → React JSX. The card markup, meta-top
//     dot separators, partial chip, t-title / t-ex / t-foot, .tag
//     pills, and trailing chevron are all preserved verbatim. The
//     iOS prototype uses an inline SVG icon module (I.search, I.rss,
//     I.chevR); the production port reuses the existing icons
//     module (IconSearch, IconRSS, IconArrow), the same glyph set
//     already shared with the desktop Library.
//   - state.lib.{q,filter} → useState. The prototype's mutation
//     pattern (rebuild rowsHtml + re-bind onClick after every input)
//     becomes a React render keyed on q + filter.
//   - Click handlers map onto navigate("transcript", { id }) — the
//     same hash-router target the desktop Library uses.
//   - Real data: TRANSCRIPTS, STATS, tagCounts, fmtDuration,
//     fmtRelative come from ./data.js (live runtime via
//     setRuntimeData, identical to the desktop Library). No mock
//     transcripts, no fake counts.
//   - The empty state copy mirrors the prototype verbatim.
//   - The RSS button copies `${origin}/feed.xml` to clipboard and
//     shows a self-managed toast (2s) using the .toast recipe from
//     Wave 0. The button is portaled into the existing
//     `.mobile-navbar .nb-side.right` slot so MobileShell's chrome
//     contract stays unchanged.
//
// Layout decision: on mobile the Settings → Appearance "library
// layout" token (table / feed / cards) is force-collapsed to feed.
// Phones don't need three layouts; the iOS design spec is feed
// only and the .tcard recipe (.feed > .tcard list) is the only one
// that ports cleanly to a 360-wide viewport.

import React from "react";
import { createPortal } from "react-dom";
import {
	STATS,
	TRANSCRIPTS,
	fmtDuration,
	fmtRelative,
	tagCounts,
} from "../data.js";
import { IconArrow, IconRSS, IconSearch } from "../icons.jsx";

/* ── helpers ─────────────────────────────────────────────────────────── */

function buildFilters() {
	// Port of: const topTags = tagCounts().slice(0,5);
	//          const filters = [["all","All"], ...topTags.map(([t])=>[t,t])];
	// "partial" is added on top of the prototype set when STATS exposes
	// at least one partial transcript, so the filter only appears when
	// the chip would have something to find.
	const topTags = tagCounts().slice(0, 5);
	const base = [["all", "All"]];
	if ((STATS?.transcripts_partial ?? 0) > 0) {
		base.push(["partial", "partial"]);
	}
	for (const [tag] of topTags) {
		base.push([tag, tag]);
	}
	return base;
}

function libRows(q, filter) {
	// Port of libRows(): same predicate order (partial → tag → search).
	const needle = q.trim().toLowerCase();
	return TRANSCRIPTS.filter((t) => {
		if (filter === "partial") return t.partial;
		if (filter !== "all" && !(t.tags ?? []).includes(filter)) return false;
		if (needle) {
			const haystackTitle = (t.title ?? "").toLowerCase();
			const haystackTags = (t.tags ?? []).join(" ").toLowerCase();
			if (!haystackTitle.includes(needle) && !haystackTags.includes(needle)) {
				return false;
			}
		}
		return true;
	});
}

function firstSummaryLine(transcript) {
	// Port of the prototype's excerpt extraction:
	//   t.summary_md ? strip H2/markdown chrome → first non-empty line
	//                : t.transcript_excerpt
	if (transcript.summary_md) {
		return (
			transcript.summary_md
				.replace(/^##.*$/m, "")
				.replace(/[#*>`]/g, "")
				.trim()
				.split("\n")
				.find((line) => line.trim()) ?? ""
		);
	}
	return transcript.transcript_excerpt ?? "";
}

/* ── subcomponents ───────────────────────────────────────────────────── */

function NavRightPortal({ children }) {
	// Portal the RSS button into the existing
	// `.mobile-navbar .nb-side.right` slot. The host node is rendered by
	// MobileShell on mount, so we re-resolve it on every commit; if it is
	// not yet attached (first render) we render nothing and the next
	// effect tick picks it up. We also clear the slot on unmount so the
	// portaled node does not survive a route change.
	const [host, setHost] = React.useState(null);
	React.useEffect(() => {
		const el = document.querySelector(".mobile-navbar .nb-side.right");
		setHost(el ?? null);
		return () => setHost(null);
	}, []);
	if (!host) return null;
	return createPortal(children, host);
}

function SearchBar({ value, onChange }) {
	const [focused, setFocused] = React.useState(false);
	return (
		<div className="searchbar">
			<div className={focused ? "searchfield focus" : "searchfield"}>
				<IconSearch size={17} />
				<input
					type="text"
					placeholder="Paste URL or search transcripts"
					value={value}
					onChange={(event) => onChange(event.target.value)}
					onFocus={() => setFocused(true)}
					onBlur={() => setFocused(false)}
				/>
			</div>
		</div>
	);
}

function SegmentFilter({ filters, value, onChange }) {
	return (
		<div className="seg">
			{filters.map(([key, label]) => (
				<button
					key={key}
					type="button"
					className={value === key ? "active" : ""}
					onClick={() => onChange(key)}
				>
					{label}
				</button>
			))}
		</div>
	);
}

function Row({ transcript, onOpen }) {
	const excerpt = firstSummaryLine(transcript);
	const tags = (transcript.tags ?? []).slice(0, 3);
	return (
		<div
			className="tcard"
			// biome-ignore lint/a11y/useSemanticElements: <button> would be invalid here because the card body contains flow content (<h3>, <p>); keep div+role to match HTML semantics.
			role="button"
			tabIndex={0}
			onClick={() => onOpen(transcript.id)}
			onKeyDown={(event) => {
				if (event.key === "Enter" || event.key === " ") {
					event.preventDefault();
					onOpen(transcript.id);
				}
			}}
		>
			<div className="meta-top">
				<span>#{transcript.id}</span>
				<span className="sep">·</span>
				<span>{fmtRelative(transcript.created_at)}</span>
				<span className="sep">·</span>
				<span>{fmtDuration(transcript.duration_seconds)}</span>
				{transcript.partial ? (
					<>
						<span className="sep">·</span>
						<span className="chip warn" style={{ padding: "2px 7px" }}>
							<span className="dot" />
							partial
						</span>
					</>
				) : null}
			</div>
			<h3 className="t-title">{transcript.title}</h3>
			<p className="t-ex">{excerpt}</p>
			<div className="t-foot">
				{tags.map((tag) => (
					<span key={tag} className="tag">
						{tag}
					</span>
				))}
				<span style={{ flex: 1 }} />
				<span className="chev">
					<IconArrow size={16} />
				</span>
			</div>
		</div>
	);
}

function EmptyState() {
	return (
		<div className="empty">
			<div className="e-title">No transcripts</div>
			<div>Try another search or paste a URL to add one.</div>
		</div>
	);
}

function Toast({ message }) {
	if (!message) return null;
	return (
		<output className="toast show" aria-live="polite">
			<span className="t-ic">
				<IconRSS size={15} />
			</span>
			<span>{message}</span>
		</output>
	);
}

/* ── route-tag → segment-filter sync ─────────────────────────────────── */

function initialFilter(routeTag, filters) {
	if (!routeTag) return "all";
	if (filters.some(([key]) => key === routeTag)) return routeTag;
	return "all";
}

/* ── MobileLibrary (default export entry point) ──────────────────────── */

export function MobileLibrary({ navigate, routeTag }) {
	const filters = React.useMemo(buildFilters, []);
	const [q, setQ] = React.useState("");
	const [filter, setFilter] = React.useState(() =>
		initialFilter(routeTag, filters),
	);
	const [toastMessage, setToastMessage] = React.useState(null);

	// Keep the segment in sync if a deep-linked tag arrives after mount.
	React.useEffect(() => {
		if (!routeTag) return;
		if (filters.some(([key]) => key === routeTag)) {
			setFilter(routeTag);
		}
	}, [routeTag, filters]);

	const rows = React.useMemo(() => libRows(q, filter), [q, filter]);

	const showToast = React.useCallback((message) => {
		setToastMessage(message);
		window.setTimeout(() => setToastMessage(null), 2000);
	}, []);

	const onCopyRss = React.useCallback(async () => {
		const url = `${window.location.origin}/feed.xml`;
		try {
			await navigator.clipboard.writeText(url);
			showToast("RSS feed link copied");
		} catch {
			showToast("Copy failed — long-press to copy");
		}
	}, [showToast]);

	const onOpen = React.useCallback(
		(id) => navigate("transcript", { id }),
		[navigate],
	);

	return (
		<>
			<NavRightPortal>
				<button
					type="button"
					className="nb-btn icon"
					aria-label="Copy RSS feed link"
					onClick={onCopyRss}
				>
					<IconRSS size={19} />
				</button>
			</NavRightPortal>
			<SearchBar value={q} onChange={setQ} />
			<SegmentFilter filters={filters} value={filter} onChange={setFilter} />
			<div className="mobile-library-list">
				{rows.length === 0 ? (
					<EmptyState />
				) : (
					<div className="feed">
						{rows.map((transcript) => (
							<Row
								key={transcript.id}
								transcript={transcript}
								onOpen={onOpen}
							/>
						))}
					</div>
				)}
			</div>
			<Toast message={toastMessage} />
		</>
	);
}

export default MobileLibrary;
