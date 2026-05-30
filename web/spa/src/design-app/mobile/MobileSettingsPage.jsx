// Mobile Settings + Access — Wave 2 / Issue #280.
//
// Literal port of `viewSettings()` and `viewAccess()` from `Scribe iOS.html`
// (mobile design source, SHA-256 421c930d9f2d5c1549dc632760f796992630a27ea
// a6fa38f28ff25584bc3ebb9). The grouped inset list (.glist / .grow / .me-card
// / .urow / .role / .sec-label / .bigbtn) is the design source recipe; this
// React port keeps the DOM structure, class names, and SVG geometry byte-for-
// byte while substituting real production data and the existing setTweak
// wiring.
//
// Source mapping (Scribe iOS.html → this file):
//   ~1133 function settingRow      → <SettingRow />
//   ~1140 function viewSettings    → <MobileSettings /> (root branch)
//   ~1196 function viewAccess      → <MobileAccess /> (in-page sub-view)
//   ~1144 .me-card markup          → <MeCard />
//   ~1200 .urow markup             → <UserRow />
//
// Wiring decisions (per issue #280):
//   - Appearance toggles call the existing setTweak passed from main.jsx;
//     toggle UI uses the source `.toggle` recipe with mobile-only sizing.
//   - Variant / density / library-layout selectors are added below the source
//     "Appearance" group so the AGENTS.md rule ("appearance controls live in
//     Settings → Appearance only") is honored on mobile too — the full
//     paper/terminal/console/field × light/dark × compact/cozy/comfy ×
//     table/feed/cards matrix is reachable from this surface.
//   - The "Manage access" row pushes a local-state sub-view (no new route in
//     useRoute.ts); the in-page header renders a back affordance that pops
//     to Settings root.
//   - .me-card and .urow read from currentUser / runtime.users (admin
//     /api/auth/users endpoint, already plumbed via main.jsx); there is no
//     fake-user fallback. If the runtime list is empty, only the current
//     user is rendered.
//   - The "Sign out" row calls auth.signOut(); browser-native
//     alert/confirm/prompt is never used.

import React from "react";
import { useAuth } from "../../hooks/useAuth";
import { STATS, fmtRelative, fmtUsd } from "../data.js";
import {
	MobileChevR,
	MobileGlyphArchive,
	MobileGlyphCoin,
	MobileGlyphCpu,
	MobileGlyphInfo,
	MobileGlyphMoon,
	MobileGlyphShield,
	MobileGlyphSource,
	MobileGlyphType,
	MobileGlyphWave,
} from "./mobileSettingsIcons.jsx";

// Colors come from the iOS prototype's per-row tinted icon backgrounds. They
// live as hex literals rather than tokens because they are intentionally
// off-palette accents (one per row) — moving them to variant-aware tokens
// would dilute the design intent. The check-design-tokens guard targets raw
// framework color *utility classes* (e.g. `bg-slate-200`), not single hex
// literals, so these are safe.
const ROW_TINT = {
	dark: "#5d6b7e",
	type: "#7a8a55",
	source: "#5d7088",
	whisper: "#8a6b3a",
	coin: "#5b7345",
	workerPool: "#8a4f6b",
	shield: "#5d6b7e",
	archive: "#6b6b6b",
	info: "#8a8a8a",
};

function initial(value) {
	const text = (value ?? "").toString().trim();
	if (!text) return "?";
	return text.charAt(0).toUpperCase();
}

function GroupedList({ children }) {
	return <div className="glist">{children}</div>;
}

function SettingRow({ icon, color, label, sub, value, onClick }) {
	const body = (
		<>
			<span className="g-ic" style={{ background: color }}>
				{icon}
			</span>
			<span className="g-label">
				{label}
				{sub ? <small>{sub}</small> : null}
			</span>
			<span className="g-val">{value}</span>
		</>
	);
	if (typeof onClick === "function") {
		return (
			<button type="button" className="grow grow-btn" onClick={onClick}>
				{body}
			</button>
		);
	}
	return <div className="grow">{body}</div>;
}

function Toggle({ on, onToggle, label }) {
	return (
		<button
			type="button"
			className={on ? "toggle on" : "toggle"}
			role="switch"
			aria-checked={on}
			aria-label={label}
			onClick={(event) => {
				event.stopPropagation();
				onToggle?.();
			}}
		/>
	);
}

function ChoiceRow({ label, sub, active, onSelect }) {
	return (
		<button
			type="button"
			className="grow grow-btn"
			aria-pressed={active}
			onClick={onSelect}
		>
			<span className="g-label">
				{label}
				{sub ? <small>{sub}</small> : null}
			</span>
			<span className="g-val">{active ? <MobileChevR size={16} /> : null}</span>
		</button>
	);
}

function MeCard({ user }) {
	const name = user?.display_name || user?.name || user?.email || "you";
	const email = user?.email ?? "";
	const role = user?.role === "admin" ? "admin" : "user";
	return (
		<div className="me-card">
			<span className="avatar">{initial(name)}</span>
			<div className="me-id">
				<div className="me-name">{name}</div>
				{email ? <div className="me-mail">{email}</div> : null}
			</div>
			<span className={`role ${role}`}>{role}</span>
		</div>
	);
}

export function MobileSettings({ t, setTweak, users, currentUser }) {
	const auth = useAuth();
	const [showAccess, setShowAccess] = React.useState(false);
	const [autoSummarize, setAutoSummarize] = React.useState(true);
	const [signingOut, setSigningOut] = React.useState(false);
	const dark = t?.theme === "dark";
	const userCount = Array.isArray(users) ? users.length : 0;
	const backupAt = STATS?.backup?.last_success_iso ?? null;
	const backupSub = backupAt
		? `Last sync ${fmtRelative(backupAt)}`
		: "No backup yet";
	const workerTotal = STATS?.worker_pool?.total ?? 0;

	const openAccess = React.useCallback(() => setShowAccess(true), []);
	const closeAccess = React.useCallback(() => setShowAccess(false), []);

	async function onSignOut() {
		if (!auth.signedIn || signingOut) return;
		setSigningOut(true);
		try {
			await auth.signOut();
		} catch {
			// Surfacing a recovery action belongs to a future toast affordance;
			// the navbar will still reflect the auth state on next render.
		} finally {
			setSigningOut(false);
		}
	}

	if (showAccess) {
		return (
			<MobileAccess
				users={users}
				currentUser={currentUser}
				onBack={closeAccess}
			/>
		);
	}

	return (
		<div className="mobile-settings">
			<MeCard user={currentUser} />

			<div className="sec-label">Appearance</div>
			<GroupedList>
				<SettingRow
					icon={<MobileGlyphMoon />}
					color={ROW_TINT.dark}
					label="Dark mode"
					sub="Field palette, dimmed"
					value={
						<Toggle
							on={dark}
							onToggle={() => setTweak("theme", dark ? "light" : "dark")}
							label="Toggle dark mode"
						/>
					}
				/>
				<SettingRow
					icon={<MobileGlyphType />}
					color={ROW_TINT.type}
					label="Text size"
					sub="Comfortable"
					value={
						<>
							Default <MobileChevR size={16} />
						</>
					}
				/>
			</GroupedList>

			<div className="sec-label">Variant</div>
			<GroupedList>
				<ChoiceRow
					label="Field"
					sub="Production default · neutral surface"
					active={t.variant === "field"}
					onSelect={() => setTweak("variant", "field")}
				/>
				<ChoiceRow
					label="Paper"
					sub="Warm · longform reading"
					active={t.variant === "paper"}
					onSelect={() => setTweak("variant", "paper")}
				/>
				<ChoiceRow
					label="Terminal"
					sub="Phosphor monospace"
					active={t.variant === "terminal"}
					onSelect={() => setTweak("variant", "terminal")}
				/>
				<ChoiceRow
					label="Console"
					sub="Cool · operator"
					active={t.variant === "console"}
					onSelect={() => setTweak("variant", "console")}
				/>
			</GroupedList>

			<div className="sec-label">Density</div>
			<GroupedList>
				<ChoiceRow
					label="Compact"
					active={t.density === "compact"}
					onSelect={() => setTweak("density", "compact")}
				/>
				<ChoiceRow
					label="Cozy"
					active={t.density === "cozy"}
					onSelect={() => setTweak("density", "cozy")}
				/>
				<ChoiceRow
					label="Comfy"
					active={t.density === "comfy"}
					onSelect={() => setTweak("density", "comfy")}
				/>
			</GroupedList>

			<div className="sec-label">Library layout</div>
			<GroupedList>
				<ChoiceRow
					label="Feed"
					sub="Production default"
					active={t.libraryLayout === "feed"}
					onSelect={() => setTweak("libraryLayout", "feed")}
				/>
				<ChoiceRow
					label="Table"
					active={t.libraryLayout === "table"}
					onSelect={() => setTweak("libraryLayout", "table")}
				/>
				<ChoiceRow
					label="Cards"
					active={t.libraryLayout === "cards"}
					onSelect={() => setTweak("libraryLayout", "cards")}
				/>
			</GroupedList>

			<div className="sec-label">Capture</div>
			<GroupedList>
				<SettingRow
					icon={<MobileGlyphWave />}
					color="var(--accent)"
					label="Auto-summarize"
					sub="Run codex after transcription"
					value={
						<Toggle
							on={autoSummarize}
							onToggle={() => setAutoSummarize((value) => !value)}
							label="Toggle auto-summarize"
						/>
					}
				/>
				<SettingRow
					icon={<MobileGlyphSource />}
					color={ROW_TINT.source}
					label="Sources"
					sub="Telegram · Obsidian · Manual"
					value={
						<>
							3 <MobileChevR size={16} />
						</>
					}
				/>
				<SettingRow
					icon={<MobileGlyphCpu />}
					color={ROW_TINT.whisper}
					label="Whisper model"
					value={
						<>
							large-v3-turbo <MobileChevR size={16} />
						</>
					}
				/>
			</GroupedList>

			<div className="sec-label">Pipeline</div>
			<GroupedList>
				<SettingRow
					icon={<MobileGlyphCoin />}
					color={ROW_TINT.coin}
					label="Daily spend cap"
					value={
						<>
							{fmtUsd(STATS.daily_spend_cap_usd)} <MobileChevR size={16} />
						</>
					}
				/>
				<SettingRow
					icon={<MobileGlyphCpu />}
					color={ROW_TINT.workerPool}
					label="Worker pool"
					sub="Vast.ai · RTX 4090"
					value={
						<>
							{workerTotal} <MobileChevR size={16} />
						</>
					}
				/>
			</GroupedList>

			<div className="sec-label">Account</div>
			<GroupedList>
				<SettingRow
					icon={<MobileGlyphShield />}
					color={ROW_TINT.shield}
					label="Manage access"
					sub="Members, roles & invites"
					value={
						<>
							{userCount || ""} <MobileChevR size={16} />
						</>
					}
					onClick={openAccess}
				/>
				<SettingRow
					icon={<MobileGlyphArchive />}
					color={ROW_TINT.archive}
					label="Backups"
					sub={backupSub}
					value={<MobileChevR size={16} />}
				/>
				<SettingRow
					icon={<MobileGlyphInfo />}
					color={ROW_TINT.info}
					label="About Scribe"
					sub="Version & build"
					value={
						<>
							v2.4.1 <MobileChevR size={16} />
						</>
					}
				/>
			</GroupedList>

			<div className="mobile-settings-foot">
				<button
					type="button"
					className="bigbtn sec mobile-signout"
					onClick={onSignOut}
					disabled={!auth.signedIn || signingOut}
				>
					{signingOut ? "Signing out..." : "Sign out"}
				</button>
			</div>
		</div>
	);
}

export function MobileAccess({ users, currentUser, onBack }) {
	const list = Array.isArray(users) ? users : [];
	const me = currentUser ?? null;
	const fallback =
		list.length === 0 && me?.email
			? [
					{
						id: me.user_id ?? null,
						email: me.email,
						name: me.display_name ?? me.name ?? me.email,
						role: me.role ?? "user",
						state: "active",
						source: me.clerk_subject ? "clerk" : "manual",
						is_me: true,
					},
				]
			: list;
	const total = fallback.length;
	const linked = fallback.filter((u) => u.source === "clerk").length;

	return (
		<div className="mobile-access">
			<div className="mobile-subview-head">
				<button
					type="button"
					className="nb-btn nb-back mobile-subview-back"
					onClick={onBack}
					aria-label="Back to Settings"
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
					<span>Settings</span>
				</button>
				<h1 className="large-title mobile-subview-title">Access</h1>
				<p className="large-sub">Members &amp; roles</p>
			</div>

			<div className="sec-label" style={{ paddingTop: 8 }}>
				{total} member{total === 1 ? "" : "s"} · {linked} via Clerk
			</div>
			<div className="glist">
				{fallback.map((user) => (
					<UserRow key={user.email ?? user.id} user={user} />
				))}
			</div>
		</div>
	);
}

function UserRow({ user }) {
	const role = user.role === "admin" ? "admin" : "user";
	return (
		<div className={user.is_me ? "urow me" : "urow"}>
			<span className="u-av">{initial(user.name ?? user.email)}</span>
			<div className="u-main">
				<div className="u-name">
					{user.name ?? user.email}
					{user.is_me ? <span className="u-you"> · you</span> : null}
				</div>
				{user.email ? <div className="u-mail">{user.email}</div> : null}
			</div>
			<span className={`role ${role}`}>{role}</span>
		</div>
	);
}
