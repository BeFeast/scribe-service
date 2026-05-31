// Mobile Settings — Wave 2e / Issue #280.
//
// Literal port of viewSettings() (`Scribe iOS.html` ~lines 1140-1197) wired
// to real Scribe runtime data and the production `useTweaks()` adapter.
//
// Source mapping (Scribe iOS.html → this file):
//   ~1133 settingRow(icIcon, color, label, sub, valHtml, act)
//                                              → <SettingRow />
//   ~1140 viewSettings()                       → <MobileSettings />
//   ~1144 .me-card (currentUser)               → me card block
//   ~1153 Appearance section (Dark mode toggle, Text size chevron)
//                                              → Appearance section
//                                                + variant / library-layout
//                                                inline pickers + density
//                                                segmented control so the
//                                                full 4×2×3×3 appearance
//                                                matrix is reachable from
//                                                mobile (AGENTS.md HARD).
//   ~1159 Capture section                      → Capture section (real
//                                                STATS spend cap + worker
//                                                pool count).
//   ~1166 Pipeline section                     → Pipeline section
//                                                (worker pool / spend cap).
//   ~1172 Account section                      → Account section. "Manage
//                                                access" pushes the in-page
//                                                Access sub-view (no new
//                                                top-level route — local
//                                                useState, per task brief).
//   ~1178 Sign out big button                  → calls `auth.signOut()` if
//                                                available, else surfaces an
//                                                inline error row.
//   ~1196 viewAccess()                         → <MobileAccess /> (separate
//                                                file, rendered when the
//                                                local sub-view state is
//                                                "access").
//
// Translation contract:
//   - Vanilla DOM (`page.querySelector('[data-act=...]').onclick = ...`) is
//     replaced by inline React handlers; classNames are preserved verbatim.
//   - The prototype's `app.setAttribute("data-theme", ...)` is replaced by
//     `setTweak("theme", "light"|"dark")`; the existing useTweaks adapter
//     persists to localStorage and the runtime applies the data-attributes
//     on `#app`. Same applies to `variant`, `density`, `libraryLayout`.
//   - Forbidden: floating Tweaks/debug panel — this file does NOT import
//     `tweaks-panel.jsx`. The full appearance matrix is reachable via
//     inline segmented pickers in this surface.
//   - No window.alert/confirm/prompt; status feedback uses inline rows.
//   - No mock users / "prototype" microcopy on action buttons.

import React from "react";
import { useAuth } from "../../hooks/useAuth";
import { STATS, fmtUsd } from "../data.js";
import {
	IconArrow,
	IconCards,
	IconDollar,
	IconFeed,
	IconMoon,
	IconRefresh,
	IconSettings,
	IconSun,
	IconTable,
	IconTerminal,
	IconWave,
} from "../icons.jsx";
import { MobileAccess } from "./MobileAccess.jsx";

const VARIANT_OPTIONS = [
	{ value: "field", label: "Field" },
	{ value: "paper", label: "Paper" },
	{ value: "terminal", label: "Terminal" },
	{ value: "console", label: "Console" },
];

const DENSITY_OPTIONS = [
	{ value: "compact", label: "Compact" },
	{ value: "cozy", label: "Cozy" },
	{ value: "comfy", label: "Comfy" },
];

const LAYOUT_OPTIONS = [
	{ value: "feed", label: "Feed", Icon: IconFeed },
	{ value: "table", label: "Table", Icon: IconTable },
	{ value: "cards", label: "Cards", Icon: IconCards },
];

const APP_VERSION = "v0.1.0";

function initials(name) {
	if (!name) return "?";
	const trimmed = String(name).trim();
	return trimmed ? trimmed[0].toUpperCase() : "?";
}

function SettingRow({ icon, iconBg, label, sub, value, onClick, last }) {
	const Tag = onClick ? "button" : "div";
	const extraProps = onClick ? { type: "button", onClick } : {};
	return (
		<Tag className={last ? "grow grow-last" : "grow"} {...extraProps}>
			<span className="g-ic" style={{ background: iconBg }}>
				{icon}
			</span>
			<span className="g-label">
				{label}
				{sub ? <small>{sub}</small> : null}
			</span>
			<span className="g-val">{value}</span>
		</Tag>
	);
}

function ChevronVal({ children }) {
	return (
		<>
			{children !== undefined && children !== null ? (
				<span className="g-val-text">{children}</span>
			) : null}
			<IconArrow size={14} />
		</>
	);
}

function Toggle({ on, onClick, ariaLabel }) {
	return (
		<button
			type="button"
			className={on ? "toggle on" : "toggle"}
			aria-pressed={on}
			aria-label={ariaLabel}
			onClick={onClick}
		/>
	);
}

function SegPicker({ value, options, onChange, ariaLabel }) {
	return (
		<div className="ms-seg" aria-label={ariaLabel}>
			{options.map((opt) => {
				const active = opt.value === value;
				return (
					<button
						type="button"
						key={opt.value}
						aria-pressed={active}
						className={active ? "ms-seg-btn active" : "ms-seg-btn"}
						onClick={() => onChange(opt.value)}
					>
						{opt.Icon ? <opt.Icon size={14} /> : null}
						<span>{opt.label}</span>
					</button>
				);
			})}
		</div>
	);
}

function MobileSettingsBody({ t, setTweak, users, currentUser, onOpenAccess }) {
	const auth = useAuth();
	const [signoutError, setSignoutError] = React.useState(null);
	const [signoutPending, setSignoutPending] = React.useState(false);

	const me = currentUser ?? users.find((u) => u.is_me) ?? null;
	const meName = me?.display_name ?? me?.name ?? me?.email ?? "Unknown user";
	const meEmail = me?.email ?? "";
	const meRole = me?.role ?? "user";
	const dark = t.theme === "dark";

	const memberCount = users.length;

	async function handleSignOut() {
		if (signoutPending) return;
		setSignoutError(null);
		setSignoutPending(true);
		try {
			if (typeof auth?.signOut === "function") {
				await auth.signOut();
			} else {
				setSignoutError("Sign-out is unavailable in this deployment.");
			}
		} catch (error) {
			setSignoutError(
				error instanceof Error ? error.message : "Sign-out failed.",
			);
		} finally {
			setSignoutPending(false);
		}
	}

	return (
		<>
			<div className="me-card">
				<span className="avatar">{initials(meName)}</span>
				<div className="me-main">
					<div className="me-name">{meName}</div>
					{meEmail ? <div className="me-mail">{meEmail}</div> : null}
				</div>
				<span className={`role ${meRole === "admin" ? "admin" : "user"}`}>
					{meRole}
				</span>
			</div>

			<div className="sec-label">Appearance</div>
			<div className="glist">
				<SettingRow
					icon={dark ? <IconMoon size={16} /> : <IconSun size={16} />}
					iconBg="#5d6b7e"
					label="Dark mode"
					sub="Field palette, dimmed"
					value={
						<Toggle
							on={dark}
							ariaLabel="Toggle dark mode"
							onClick={() => setTweak("theme", dark ? "light" : "dark")}
						/>
					}
				/>
				<div className="grow grow-stack">
					<span className="g-ic" style={{ background: "#7a8a55" }}>
						<IconTerminal size={16} />
					</span>
					<span className="g-label">
						Variant
						<small>Paper · Terminal · Console · Field</small>
					</span>
					<SegPicker
						value={t.variant}
						options={VARIANT_OPTIONS}
						onChange={(value) => setTweak("variant", value)}
						ariaLabel="Variant"
					/>
				</div>
				<div className="grow grow-stack">
					<span className="g-ic" style={{ background: "#7a8a55" }}>
						<IconRefresh size={16} />
					</span>
					<span className="g-label">
						Density
						<small>Compact · Cozy · Comfy</small>
					</span>
					<SegPicker
						value={t.density}
						options={DENSITY_OPTIONS}
						onChange={(value) => setTweak("density", value)}
						ariaLabel="Density"
					/>
				</div>
				<div className="grow grow-stack grow-last">
					<span className="g-ic" style={{ background: "#5d7088" }}>
						<IconFeed size={16} />
					</span>
					<span className="g-label">
						Library layout
						<small>Feed · Table · Cards</small>
					</span>
					<SegPicker
						value={t.libraryLayout}
						options={LAYOUT_OPTIONS}
						onChange={(value) => setTweak("libraryLayout", value)}
						ariaLabel="Library layout"
					/>
				</div>
			</div>

			<div className="sec-label">Capture</div>
			<div className="glist">
				<SettingRow
					icon={<IconWave size={16} />}
					iconBg="var(--accent)"
					label="Daily spend cap"
					sub="Vast.ai budget per day"
					value={
						<span className="g-val-text">
							{fmtUsd(STATS.daily_spend_cap_usd ?? 0)}
						</span>
					}
				/>
				<SettingRow
					icon={<IconWave size={16} />}
					iconBg="#8a6b3a"
					label="Spend last 24h"
					sub="Live telemetry"
					last
					value={
						<span className="g-val-text">
							{fmtUsd(STATS.vast_spend_24h ?? 0)}
						</span>
					}
				/>
			</div>

			<div className="sec-label">Pipeline</div>
			<div className="glist">
				<SettingRow
					icon={<IconDollar size={16} />}
					iconBg="#5b7345"
					label="Queue depth"
					sub="Across all stages"
					value={<span className="g-val-text">{STATS.queue_depth ?? 0}</span>}
				/>
				<SettingRow
					icon={<IconRefresh size={16} />}
					iconBg="#8a4f6b"
					label="Worker pool"
					sub="Active / total"
					last
					value={
						<span className="g-val-text">
							{STATS.worker_pool?.active ?? 0}
							{" / "}
							{STATS.worker_pool?.total ?? 0}
						</span>
					}
				/>
			</div>

			<div className="sec-label">Account</div>
			<div className="glist">
				<SettingRow
					icon={<IconSettings size={16} />}
					iconBg="#5d6b7e"
					label="Manage access"
					sub="Members, roles & invites"
					onClick={onOpenAccess}
					value={
						<ChevronVal>{memberCount > 0 ? memberCount : null}</ChevronVal>
					}
				/>
				<SettingRow
					icon={<IconSettings size={16} />}
					iconBg="#8a8a8a"
					label="About Scribe"
					sub="Version & build"
					last
					value={<span className="g-val-text">{APP_VERSION}</span>}
				/>
			</div>

			<div className="ms-bigbtn-wrap">
				<button
					type="button"
					className="ms-bigbtn ms-bigbtn-danger"
					onClick={handleSignOut}
					disabled={signoutPending}
				>
					{signoutPending ? "Signing out…" : "Sign out"}
				</button>
				{signoutError ? (
					<div className="ms-inline-error" aria-live="assertive">
						{signoutError}
					</div>
				) : null}
			</div>
		</>
	);
}

export function MobileSettings({
	t,
	setTweak,
	users = [],
	currentUser = null,
	onConfigSaved,
	auth: _injectedAuth,
}) {
	void onConfigSaved;
	void _injectedAuth;
	const [subview, setSubview] = React.useState("settings");
	const [accessNotice, setAccessNotice] = React.useState(null);

	const onOpenAccess = React.useCallback(() => {
		setAccessNotice(null);
		setSubview("access");
	}, []);
	const onCloseAccess = React.useCallback(() => {
		setSubview("settings");
	}, []);
	const onInvite = React.useCallback(() => {
		setAccessNotice(
			"Invites are managed in the Clerk dashboard — open it from your admin console.",
		);
	}, []);

	if (subview === "access") {
		return (
			<MobileAccess
				users={users}
				notice={accessNotice}
				onBack={onCloseAccess}
				onInvite={onInvite}
			/>
		);
	}
	return (
		<MobileSettingsBody
			t={t}
			setTweak={setTweak}
			users={users}
			currentUser={currentUser}
			onOpenAccess={onOpenAccess}
		/>
	);
}
