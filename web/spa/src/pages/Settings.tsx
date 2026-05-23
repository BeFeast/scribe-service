import React from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { Markdown } from "../components/Markdown";
import { useAuth } from "../hooks/useAuth";
import type {
	LibraryLayout,
	ScribeDensity,
	ScribeTheme,
	ScribeVariant,
	Tweaks,
} from "../hooks/useTweaks";
import { isAuthStatus } from "../lib/auth";
import type { DisplayCurrency } from "../lib/currency";
import { displayCurrencies } from "../lib/currency";

type ConfigValue = boolean | number | string;

type ConfigEntry = {
	value: ConfigValue;
	source: "env" | "db" | string;
	mutable: boolean;
};

type ConfigResponse = {
	config: Record<string, ConfigEntry>;
	restart_required: string[];
};

type PromptVersion = {
	id: PromptVersionId;
	len_chars: number;
	len_tokens_est: number;
	first_line: string;
	is_active: boolean;
};

type PromptListResponse = {
	active_version: PromptVersionId;
	versions: PromptVersion[];
};

type ExtensionTokenResponse = {
	token: string;
	token_type: string;
};

type CurrentUser = {
	authenticated: boolean;
	kind: string;
	role: string;
	user_id: number | null;
	owner_id: number | null;
	email: string | null;
	display_name: string | null;
};

type AdminUser = {
	id: number;
	owner_id: number;
	clerk_subject: string | null;
	primary_email: string;
	display_name: string | null;
	role: "admin" | "user" | string;
	disabled: boolean;
	created_at: string;
	updated_at: string;
};

type UserRole = "user" | "admin";

type PromptVersionId = "v1" | "v2" | "v3";
type ConfigKey =
	| "daily_spend_cap_usd"
	| "worker_concurrency"
	| "bot_wall_retry"
	| "public_base_url"
	| "display_currency"
	| "short_description_language"
	| "webhook_default"
	| "webhook_embed_transcript";

type SettingsProps = {
	tweaks: Tweaks;
	setTheme: (value: ScribeTheme) => void;
	replaceTweaks: (value: Tweaks) => void;
};

type SettingsRowProps = {
	label: string;
	hint: string;
	source?: string;
	children: React.ReactNode;
};

const EXTENSION_TOKEN_KEY = "scribe.extensionToken";
const CONFIG_SAVED_EVENT = "scribe-config-saved";
const configKeys: ConfigKey[] = [
	"daily_spend_cap_usd",
	"worker_concurrency",
	"bot_wall_retry",
	"public_base_url",
	"display_currency",
	"short_description_language",
	"webhook_default",
	"webhook_embed_transcript",
];
const promptVersions: PromptVersionId[] = ["v1", "v2", "v3"];

export function Settings({ tweaks, setTheme, replaceTweaks }: SettingsProps) {
	const auth = useAuth();
	const [config, setConfig] = React.useState<Record<string, ConfigEntry>>({});
	const [draft, setDraft] = React.useState<Record<ConfigKey, ConfigValue>>(
		{} as Record<ConfigKey, ConfigValue>,
	);
	const [savedTweaks, setSavedTweaks] = React.useState<Tweaks>(tweaks);
	const [dirtyKeys, setDirtyKeys] = React.useState<Set<ConfigKey>>(new Set());
	const [restartKeys, setRestartKeys] = React.useState<string[]>([]);
	const [extensionToken, setExtensionToken] = React.useState(
		readStoredExtensionToken,
	);
	const [promptList, setPromptList] = React.useState<PromptListResponse | null>(
		null,
	);
	const [promptVersion, setPromptVersion] =
		React.useState<PromptVersionId>("v1");
	const [promptBody, setPromptBody] = React.useState("");
	const [savedPromptBody, setSavedPromptBody] = React.useState("");
	const [savedPromptVersion, setSavedPromptVersion] =
		React.useState<PromptVersionId>("v1");
	const [dryRunTranscriptId, setDryRunTranscriptId] = React.useState("");
	const [dryRunMarkdown, setDryRunMarkdown] = React.useState<string | null>(
		null,
	);
	const [status, setStatus] = React.useState<string | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [loading, setLoading] = React.useState(true);
	const [saving, setSaving] = React.useState(false);
	const [dryRunning, setDryRunning] = React.useState(false);
	const [creatingExtensionToken, setCreatingExtensionToken] =
		React.useState(false);
	const [showExtensionToken, setShowExtensionToken] = React.useState(false);
	const [currentUser, setCurrentUser] = React.useState<CurrentUser | null>(
		null,
	);
	const [adminUsers, setAdminUsers] = React.useState<AdminUser[]>([]);
	const [accessLoading, setAccessLoading] = React.useState(true);
	const [accessError, setAccessError] = React.useState<string | null>(null);
	const [adminRequired, setAdminRequired] = React.useState(false);
	const [userEmail, setUserEmail] = React.useState("");
	const [userDisplayName, setUserDisplayName] = React.useState("");
	const [userRole, setUserRole] = React.useState<UserRole>("user");
	const [savingUser, setSavingUser] = React.useState(false);
	const [disableTarget, setDisableTarget] = React.useState<AdminUser | null>(
		null,
	);
	const [disablingUser, setDisablingUser] = React.useState(false);

	const promptDirty =
		promptBody !== savedPromptBody || promptVersion !== savedPromptVersion;
	const appearanceDirty = !tweaksEqual(tweaks, savedTweaks);
	const hasUnsavedChanges =
		dirtyKeys.size > 0 || promptDirty || appearanceDirty;

	const loadSettings = React.useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const [configResponse, promptsResponse] = await Promise.all([
				auth.protectedFetch("/api/config"),
				auth.protectedFetch("/api/prompts"),
			]);
			if (!configResponse.ok) {
				if (isAuthStatus(configResponse.status)) {
					throw new Error("Sign in required to load operator settings.");
				}
				throw new Error(await responseMessage(configResponse));
			}
			if (!promptsResponse.ok) {
				if (isAuthStatus(promptsResponse.status)) {
					throw new Error("Sign in required to load operator settings.");
				}
				throw new Error(await responseMessage(promptsResponse));
			}
			const configBody = (await configResponse.json()) as ConfigResponse;
			const promptsBody = (await promptsResponse.json()) as PromptListResponse;
			const activeVersion = promptsBody.active_version;
			const promptResponse = await auth.protectedFetch(
				`/api/prompts/${activeVersion}`,
			);
			if (!promptResponse.ok) {
				throw new Error(await responseMessage(promptResponse));
			}
			const body = await promptResponse.text();
			setConfig(configBody.config);
			setDraft(draftFromConfig(configBody.config));
			setDirtyKeys(new Set());
			setRestartKeys(configBody.restart_required);
			setPromptList(promptsBody);
			setPromptVersion(activeVersion);
			setSavedPromptVersion(activeVersion);
			setPromptBody(body);
			setSavedPromptBody(body);
			setStatus(null);
			return true;
		} catch (loadError) {
			setError(loadError instanceof Error ? loadError.message : "load failed");
			return false;
		} finally {
			setLoading(false);
		}
	}, [auth]);

	React.useEffect(() => {
		void loadSettings();
	}, [loadSettings]);

	const loadAccess = React.useCallback(async () => {
		setAccessLoading(true);
		setAccessError(null);
		setAdminRequired(false);
		try {
			const meResponse = await auth.protectedFetch("/api/auth/me");
			if (!meResponse.ok) {
				if (isAuthStatus(meResponse.status)) {
					setCurrentUser(null);
					setAdminUsers([]);
					return false;
				}
				throw new Error(await responseMessage(meResponse));
			}
			const me = (await meResponse.json()) as CurrentUser;
			setCurrentUser(me);
			if (!canManageUsers(me)) {
				setAdminUsers([]);
				setAdminRequired(true);
				return true;
			}
			const usersResponse = await auth.protectedFetch("/api/admin/users");
			if (!usersResponse.ok) {
				if (usersResponse.status === 403) {
					setAdminUsers([]);
					setAdminRequired(true);
					return true;
				}
				throw new Error(await responseMessage(usersResponse));
			}
			setAdminUsers((await usersResponse.json()) as AdminUser[]);
			return true;
		} catch (loadError) {
			setAccessError(
				loadError instanceof Error ? loadError.message : "access load failed",
			);
			return false;
		} finally {
			setAccessLoading(false);
		}
	}, [auth]);

	React.useEffect(() => {
		void loadAccess();
	}, [loadAccess]);

	async function selectPromptVersion(next: string) {
		const version = next as PromptVersionId;
		setError(null);
		try {
			const response = await auth.protectedFetch(`/api/prompts/${version}`);
			if (!response.ok) {
				throw new Error(await responseMessage(response));
			}
			const body = await response.text();
			setPromptVersion(version);
			setPromptBody(body);
			if (version === savedPromptVersion) {
				setSavedPromptBody(body);
			}
		} catch (selectError) {
			setError(
				selectError instanceof Error
					? selectError.message
					: "prompt load failed",
			);
		}
	}

	function updateDraft(key: ConfigKey, value: ConfigValue) {
		setDraft((current) => ({ ...current, [key]: value }));
		setDirtyKeys((current) => {
			const next = new Set(current);
			const saved = config[key]?.value;
			if (value === saved) {
				next.delete(key);
			} else {
				next.add(key);
			}
			return next;
		});
	}

	async function save() {
		setSaving(true);
		setError(null);
		setStatus(null);
		try {
			if (dirtyKeys.size > 0) {
				const payload: Partial<Record<ConfigKey, ConfigValue>> = {};
				for (const key of dirtyKeys) {
					payload[key] = draft[key];
				}
				const response = await auth.protectedFetch("/api/config", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(payload),
				});
				if (!response.ok) {
					throw new Error(await responseMessage(response));
				}
				const body = (await response.json()) as ConfigResponse;
				setRestartKeys(body.restart_required);
			}
			if (promptBody !== savedPromptBody) {
				const response = await auth.protectedFetch(
					`/api/prompts/${promptVersion}`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ body: promptBody }),
					},
				);
				if (!response.ok) {
					throw new Error(await responseMessage(response));
				}
			}
			if (promptVersion !== savedPromptVersion) {
				const response = await auth.protectedFetch("/api/prompts/active", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ version: promptVersion }),
				});
				if (!response.ok) {
					throw new Error(await responseMessage(response));
				}
			}
			if (await loadSettings()) {
				document.dispatchEvent(new CustomEvent(CONFIG_SAVED_EVENT));
				setSavedTweaks(tweaks);
				setStatus("Saved");
			}
		} catch (saveError) {
			setError(saveError instanceof Error ? saveError.message : "save failed");
		} finally {
			setSaving(false);
		}
	}

	async function dryRun() {
		const transcriptId = Number.parseInt(dryRunTranscriptId, 10);
		if (!Number.isFinite(transcriptId)) {
			setError("Dry-run transcript id must be a number");
			return;
		}
		setDryRunning(true);
		setError(null);
		try {
			const response = await auth.protectedFetch("/api/prompts/dry-run", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					version: promptVersion,
					transcript_id: transcriptId,
					prompt_body: promptBody,
				}),
			});
			if (!response.ok) {
				throw new Error(await responseMessage(response));
			}
			const body = (await response.json()) as { summary_md: string };
			setDryRunMarkdown(body.summary_md);
		} catch (dryRunError) {
			setError(
				dryRunError instanceof Error ? dryRunError.message : "dry-run failed",
			);
		} finally {
			setDryRunning(false);
		}
	}

	function storeExtensionToken(nextToken: string) {
		setExtensionToken(nextToken.trim());
		try {
			localStorage.setItem(EXTENSION_TOKEN_KEY, nextToken.trim());
		} catch {
			// Runtime use still works for this tab.
		}
	}

	async function copyExtensionToken() {
		if (!extensionToken) {
			return;
		}
		if (await writeClipboard(extensionToken)) {
			setStatus("Extension token copied");
		} else {
			setError("Could not copy token; select and copy it manually.");
		}
	}

	async function createExtensionToken() {
		setCreatingExtensionToken(true);
		setError(null);
		setStatus(null);
		try {
			const response = await auth.protectedFetch("/api/auth/extension-token", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ label: "Chrome extension" }),
			});
			if (!response.ok) {
				throw new Error(await responseMessage(response));
			}
			const body = (await response.json()) as ExtensionTokenResponse;
			storeExtensionToken(body.token);
			if (await writeClipboard(body.token)) {
				setStatus("Extension token created and copied");
			} else {
				setStatus("Extension token created; select and copy it manually.");
			}
		} catch (createError) {
			setError(
				createError instanceof Error
					? createError.message
					: "extension token creation failed",
			);
		} finally {
			setCreatingExtensionToken(false);
		}
	}

	async function saveUser() {
		const email = userEmail.trim();
		if (!email) {
			setAccessError("Email is required.");
			return;
		}
		setSavingUser(true);
		setAccessError(null);
		setStatus(null);
		try {
			const response = await auth.protectedFetch("/api/admin/users", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					email,
					display_name: userDisplayName.trim() || null,
					role: userRole,
				}),
			});
			if (!response.ok) {
				if (response.status === 403) {
					setAdminRequired(true);
					throw new Error("Admin role required to manage Scribe users.");
				}
				throw new Error(await responseMessage(response));
			}
			setUserEmail("");
			setUserDisplayName("");
			setUserRole("user");
			await loadAccess();
			setStatus("User saved");
		} catch (saveError) {
			setAccessError(
				saveError instanceof Error ? saveError.message : "user save failed",
			);
		} finally {
			setSavingUser(false);
		}
	}

	function requestDisableUser(user: AdminUser) {
		const reason = disableBlockedReason(user, currentUser, adminUsers);
		if (reason !== null) {
			setAccessError(reason);
			return;
		}
		setDisableTarget(user);
	}

	async function disableUser() {
		if (disableTarget === null) {
			return;
		}
		const reason = disableBlockedReason(disableTarget, currentUser, adminUsers);
		if (reason !== null) {
			setDisableTarget(null);
			setAccessError(reason);
			return;
		}
		setDisablingUser(true);
		setAccessError(null);
		setStatus(null);
		try {
			const response = await auth.protectedFetch(
				`/api/admin/users/${disableTarget.id}/disable`,
				{ method: "POST" },
			);
			if (!response.ok) {
				if (response.status === 403) {
					setAdminRequired(true);
					throw new Error("Admin role required to manage Scribe users.");
				}
				throw new Error(await responseMessage(response));
			}
			setDisableTarget(null);
			await loadAccess();
			setStatus("User disabled");
		} catch (disableError) {
			setAccessError(
				disableError instanceof Error
					? disableError.message
					: "user disable failed",
			);
		} finally {
			setDisablingUser(false);
		}
	}

	return (
		<section className="settings-page pane">
			<header className="pane-header">
				<div>
					<p className="eyebrow">Settings</p>
					<h1 className="pane-h1">Pipeline, summaries, appearance, access</h1>
					<p className="pane-sub">
						Runtime controls are saved in the service database and refreshed
						from the API.
					</p>
				</div>
				<div className="settings-actions">
					<span className="access-status">{auth.accessStatus}</span>
					{!auth.canWrite && auth.clerkConfigured ? (
						<>
							<button
								className="btn"
								type="button"
								onClick={() => void auth.signUp()}
								disabled={!auth.clerkReady || auth.authRedirectInFlight}
								title={auth.authBlockedMessage ?? undefined}
							>
								Sign up
							</button>
							<button
								className="btn ghost"
								type="button"
								onClick={() => void auth.signIn()}
								disabled={!auth.clerkReady || auth.authRedirectInFlight}
								title={auth.authBlockedMessage ?? undefined}
							>
								Sign in
							</button>
						</>
					) : null}
					{auth.accessStatus === "Signed in" ? (
						<button className="btn ghost" type="button" onClick={auth.signOut}>
							Sign out
						</button>
					) : null}
					<button className="btn ghost" type="button" onClick={loadSettings}>
						Refresh
					</button>
					<button
						className="btn primary"
						type="button"
						disabled={saving || loading || !hasUnsavedChanges}
						onClick={save}
					>
						{saving ? "Saving" : "Save"}
					</button>
				</div>
			</header>

			{restartKeys.includes("worker_concurrency") ||
			dirtyKeys.has("worker_concurrency") ? (
				<output className="settings-banner warn">
					<span>Worker concurrency changes require a restart.</span>
					<code>docker compose restart scribe</code>
				</output>
			) : null}

			{error !== null ? (
				<div className="settings-banner err" role="alert">
					{error}
				</div>
			) : null}
			{status !== null ? (
				<output className="settings-banner ok">{status}</output>
			) : null}

			<div className="settings-layout" aria-busy={loading}>
				<AccessSection
					currentUser={currentUser}
					users={adminUsers}
					loading={accessLoading}
					error={accessError ?? auth.authBlockedMessage}
					adminRequired={adminRequired}
					auth={auth}
					email={userEmail}
					displayName={userDisplayName}
					role={userRole}
					savingUser={savingUser}
					onEmail={setUserEmail}
					onDisplayName={setUserDisplayName}
					onRole={setUserRole}
					onSaveUser={saveUser}
					onDisableRequest={requestDisableUser}
					onRefresh={loadAccess}
				/>

				<section className="settings-group">
					<h2 className="section-label">API access</h2>
					<TokenRow
						token={extensionToken}
						showToken={showExtensionToken}
						isCreating={creatingExtensionToken}
						canCreate={auth.accessStatus === "Signed in"}
						onCreate={createExtensionToken}
						onCopy={copyExtensionToken}
						onToggleShow={() => setShowExtensionToken((value) => !value)}
					/>
				</section>

				<section className="settings-group">
					<h2 className="section-label">Pipeline</h2>
					<NumberRow
						label="Daily spend cap"
						hint="Rolling 24-hour Vast.ai cap in USD; 0 disables the cap."
						source={config.daily_spend_cap_usd?.source}
						value={numberDraft(draft.daily_spend_cap_usd)}
						min={0}
						step={0.25}
						disabled={!isMutable(config.daily_spend_cap_usd)}
						onChange={(value) => updateDraft("daily_spend_cap_usd", value)}
					/>
					<NumberRow
						label="Worker concurrency"
						hint="Number of local pipeline workers. Restart required."
						source={config.worker_concurrency?.source}
						value={numberDraft(draft.worker_concurrency)}
						min={1}
						step={1}
						disabled={!isMutable(config.worker_concurrency)}
						onChange={(value) => updateDraft("worker_concurrency", value)}
					/>
					<ToggleRow
						label="Bot-wall retry"
						hint="Enable alternate downloader retries when YouTube blocks a client."
						source={config.bot_wall_retry?.source}
						checked={booleanDraft(draft.bot_wall_retry)}
						disabled={!isMutable(config.bot_wall_retry)}
						onChange={(value) => updateDraft("bot_wall_retry", value)}
					/>
					<UrlRow
						label="Public base URL"
						hint="Used for generated web links and shortlink targets."
						source={config.public_base_url?.source}
						value={stringDraft(draft.public_base_url)}
						disabled={!isMutable(config.public_base_url)}
						onChange={(value) => updateDraft("public_base_url", value)}
					/>
					<LanguageRow
						label="Card description language"
						hint="Language used for generated short descriptions in the library feed."
						source={config.short_description_language?.source}
						value={stringDraft(draft.short_description_language)}
						disabled={!isMutable(config.short_description_language)}
						onChange={(value) =>
							updateDraft("short_description_language", value)
						}
					/>
					<CurrencyRow
						label="Display currency"
						hint="Display Vast costs in the UI. Stored costs and spend caps remain USD."
						source={config.display_currency?.source}
						value={stringDraft(draft.display_currency)}
						disabled={!isMutable(config.display_currency)}
						onChange={(value) => updateDraft("display_currency", value)}
					/>
					<UrlRow
						label="Default webhook"
						hint="Callback URL used when a job does not provide one."
						source={config.webhook_default?.source}
						value={stringDraft(draft.webhook_default)}
						disabled={!isMutable(config.webhook_default)}
						onChange={(value) => updateDraft("webhook_default", value)}
					/>
					<ToggleRow
						label="Embed transcript"
						hint="Include transcript markdown in webhook payloads."
						source={config.webhook_embed_transcript?.source}
						checked={booleanDraft(draft.webhook_embed_transcript)}
						disabled={!isMutable(config.webhook_embed_transcript)}
						onChange={(value) => updateDraft("webhook_embed_transcript", value)}
					/>
				</section>

				<PromptEditor
					promptList={promptList}
					version={promptVersion}
					body={promptBody}
					transcriptId={dryRunTranscriptId}
					isDirty={promptDirty}
					isDryRunning={dryRunning}
					onVersion={selectPromptVersion}
					onBody={setPromptBody}
					onTranscriptId={setDryRunTranscriptId}
					onDryRun={dryRun}
				/>

				<section className="settings-group">
					<h2 className="section-label">Appearance</h2>
					<SegRow
						label="Theme"
						hint="Global light or dark mode."
						value={tweaks.theme}
						options={["light", "dark"]}
						onChange={(value) => setTheme(value as ScribeTheme)}
					/>
					<SegRow
						label="Variant"
						hint="Design-system skin for the app shell."
						value={tweaks.variant}
						options={["paper", "terminal", "console"]}
						onChange={(value) =>
							replaceTweaks({ ...tweaks, variant: value as ScribeVariant })
						}
					/>
					<SegRow
						label="Library layout"
						hint="Default layout for transcript browsing."
						value={tweaks.libraryLayout}
						options={["feed", "table", "cards"]}
						onChange={(value) =>
							replaceTweaks({
								...tweaks,
								libraryLayout: value as LibraryLayout,
							})
						}
					/>
					<SegRow
						label="Density"
						hint="Spacing and type scale for data-heavy screens."
						value={tweaks.density}
						options={["compact", "cozy", "comfy"]}
						onChange={(value) =>
							replaceTweaks({ ...tweaks, density: value as ScribeDensity })
						}
					/>
				</section>
			</div>

			{dryRunMarkdown !== null ? (
				<div className="modal-backdrop" role="presentation">
					<dialog className="settings-modal" aria-label="Dry-run summary" open>
						<header>
							<strong>Dry-run summary</strong>
							<button
								className="iconbtn"
								type="button"
								aria-label="Close dry-run"
								onClick={() => setDryRunMarkdown(null)}
							>
								x
							</button>
						</header>
						<Markdown body={dryRunMarkdown} />
					</dialog>
				</div>
			) : null}
			{disableTarget !== null ? (
				<ConfirmDialog
					title="Disable user"
					body={`Disable ${userLabel(disableTarget)}? They will lose Scribe access until an admin adds them again.`}
					confirmLabel="Disable"
					busyLabel="Disabling"
					busy={disablingUser}
					onCancel={() => setDisableTarget(null)}
					onConfirm={disableUser}
				/>
			) : null}
		</section>
	);
}

export function AccessSection({
	currentUser,
	users,
	loading,
	error,
	adminRequired,
	auth,
	email,
	displayName,
	role,
	savingUser,
	onEmail,
	onDisplayName,
	onRole,
	onSaveUser,
	onDisableRequest,
	onRefresh,
}: {
	currentUser: CurrentUser | null;
	users: AdminUser[];
	loading: boolean;
	error: string | null;
	adminRequired: boolean;
	auth: ReturnType<typeof useAuth>;
	email: string;
	displayName: string;
	role: UserRole;
	savingUser: boolean;
	onEmail: (value: string) => void;
	onDisplayName: (value: string) => void;
	onRole: (value: UserRole) => void;
	onSaveUser: () => void;
	onDisableRequest: (user: AdminUser) => void;
	onRefresh: () => void;
}) {
	const signedOut =
		currentUser === null && !loading && error === null && auth.authBlockedMessage === null;
	const adminControlsEnabled =
		currentUser !== null && canManageUsers(currentUser) && !adminRequired;

	return (
		<section className="settings-group access-group">
			<div className="prompt-head">
				<h2 className="section-label">Access</h2>
				<button className="btn ghost" type="button" onClick={onRefresh}>
					Refresh access
				</button>
			</div>

			<div className="access-card">
				<div>
					<div className="row-label">
						{currentUser !== null ? userIdentity(currentUser) : "Not signed in"}
					</div>
					<div className="hint">
						{currentUser !== null
							? "Current Scribe auth state from /api/auth/me."
							: "Sign in to see your Scribe role and access details."}
					</div>
				</div>
				<div className="access-facts">
					<span className="chip info">role {currentUser?.role ?? "none"}</span>
					<span className="chip">kind {currentUser?.kind ?? "signed-out"}</span>
					<span className="chip">
						{auth.trustedNetwork ? "trusted network" : auth.accessStatus}
					</span>
				</div>
			</div>

			{error !== null ? (
				<div className="settings-banner err" role="alert">
					<span>{error}</span>
					{auth.authBlockedMessage !== null ? (
						<button
							className="btn ghost"
							type="button"
							onClick={onRefresh}
							disabled={loading}
						>
							Retry access
						</button>
					) : (
						<button
							className="btn ghost"
							type="button"
							onClick={() => void auth.signIn()}
							disabled={!auth.clerkReady || auth.authRedirectInFlight}
						>
							Retry sign in
						</button>
					)}
				</div>
			) : null}

			{signedOut ? (
				<div className="settings-banner warn">
					<span>Sign in required to manage Scribe access.</span>
					<div className="settings-auth-actions">
						<button
							className="btn"
							type="button"
							onClick={() => void auth.signUp()}
							disabled={!auth.clerkReady || auth.authRedirectInFlight}
						>
							Sign up
						</button>
						<button
							className="btn ghost"
							type="button"
							onClick={() => void auth.signIn()}
							disabled={!auth.clerkReady || auth.authRedirectInFlight}
						>
							Sign in
						</button>
					</div>
				</div>
			) : null}

			{adminRequired ? (
				<div className="settings-banner warn">
					Admin role required to manage Scribe users.
				</div>
			) : null}

			<div className="access-form" aria-disabled={!adminControlsEnabled}>
				<input
					className="settings-input"
					type="email"
					value={email}
					placeholder="user@example.test"
					disabled={!adminControlsEnabled || savingUser}
					onChange={(event) => onEmail(event.currentTarget.value)}
				/>
				<input
					className="settings-input"
					value={displayName}
					placeholder="Display name"
					disabled={!adminControlsEnabled || savingUser}
					onChange={(event) => onDisplayName(event.currentTarget.value)}
				/>
				<select
					className="settings-input"
					value={role}
					disabled={!adminControlsEnabled || savingUser}
					onChange={(event) => onRole(event.currentTarget.value as UserRole)}
				>
					<option value="user">user</option>
					<option value="admin">admin</option>
				</select>
				<button
					className="btn primary"
					type="button"
					disabled={!adminControlsEnabled || savingUser}
					onClick={onSaveUser}
				>
					{savingUser ? "Saving user" : "Add or update user"}
				</button>
			</div>

			<div className="access-table-wrap">
				<table className="access-table">
					<thead>
						<tr>
							<th>Email</th>
							<th>Name</th>
							<th>Role</th>
							<th>State</th>
							<th>Clerk subject</th>
							<th>Action</th>
						</tr>
					</thead>
					<tbody>
						{users.length > 0 ? (
							users.map((user) => {
								const blockReason = disableBlockedReason(
									user,
									currentUser,
									users,
								);
								return (
									<tr key={user.id}>
										<td>{user.primary_email}</td>
										<td>{user.display_name || "—"}</td>
										<td>{user.role}</td>
										<td>{user.disabled ? "disabled" : "active"}</td>
										<td className="access-subject">
											{user.clerk_subject || "not linked"}
										</td>
										<td>
											<button
												className="btn ghost"
												type="button"
												disabled={
													!adminControlsEnabled ||
													user.disabled ||
													blockReason !== null
												}
												title={blockReason ?? undefined}
												onClick={() => onDisableRequest(user)}
											>
												Disable
											</button>
										</td>
									</tr>
								);
							})
						) : (
							<tr>
								<td colSpan={6}>
									{loading ? "Loading users" : "No users to display"}
								</td>
							</tr>
						)}
					</tbody>
				</table>
			</div>
		</section>
	);
}

export function SettingsRow({
	label,
	hint,
	source,
	children,
}: SettingsRowProps) {
	return (
		<div className="settings-row">
			<div>
				<div className="row-label">{label}</div>
				<div className="hint">{hint}</div>
			</div>
			<div className="row-control">
				{source !== undefined ? (
					<span className="source-chip">{source}</span>
				) : null}
				{children}
			</div>
		</div>
	);
}

export function NumberRow({
	label,
	hint,
	source,
	value,
	min,
	step,
	disabled,
	onChange,
}: {
	label: string;
	hint: string;
	source?: string;
	value: number;
	min: number;
	step: number;
	disabled: boolean;
	onChange: (value: number) => void;
}) {
	return (
		<SettingsRow label={label} hint={hint} source={source}>
			<input
				className="settings-input number"
				type="number"
				value={value}
				min={min}
				step={step}
				disabled={disabled}
				onChange={(event) => onChange(event.currentTarget.valueAsNumber)}
			/>
		</SettingsRow>
	);
}

export function UrlRow({
	label,
	hint,
	source,
	value,
	disabled,
	onChange,
}: {
	label: string;
	hint: string;
	source?: string;
	value: string;
	disabled: boolean;
	onChange: (value: string) => void;
}) {
	return (
		<SettingsRow label={label} hint={hint} source={source}>
			<input
				className="settings-input url"
				type="url"
				value={value}
				disabled={disabled}
				placeholder="https://example.test/webhook"
				onChange={(event) => onChange(event.currentTarget.value)}
			/>
		</SettingsRow>
	);
}

export function ToggleRow({
	label,
	hint,
	source,
	checked,
	disabled,
	onChange,
}: {
	label: string;
	hint: string;
	source?: string;
	checked: boolean;
	disabled: boolean;
	onChange: (value: boolean) => void;
}) {
	return (
		<SettingsRow label={label} hint={hint} source={source}>
			<label className="switch">
				<input
					type="checkbox"
					checked={checked}
					disabled={disabled}
					onChange={(event) => onChange(event.currentTarget.checked)}
				/>
				<span />
			</label>
		</SettingsRow>
	);
}

export function LanguageRow({
	label,
	hint,
	source,
	value,
	disabled,
	onChange,
}: {
	label: string;
	hint: string;
	source?: string;
	value: string;
	disabled: boolean;
	onChange: (value: string) => void;
}) {
	return (
		<SettingsRow label={label} hint={hint} source={source}>
			<select
				className="settings-input"
				value={value || "ru"}
				disabled={disabled}
				onChange={(event) => onChange(event.currentTarget.value)}
			>
				<option value="ru">Russian</option>
				<option value="en">English</option>
			</select>
		</SettingsRow>
	);
}

export function CurrencyRow({
	label,
	hint,
	source,
	value,
	disabled,
	onChange,
}: {
	label: string;
	hint: string;
	source?: string;
	value: string;
	disabled: boolean;
	onChange: (value: DisplayCurrency) => void;
}) {
	return (
		<SettingsRow label={label} hint={hint} source={source}>
			<select
				className="settings-input"
				value={value || "USD"}
				disabled={disabled}
				onChange={(event) =>
					onChange(event.currentTarget.value as DisplayCurrency)
				}
			>
				{displayCurrencies.map((currency) => (
					<option value={currency} key={currency}>
						{currency}
					</option>
				))}
			</select>
		</SettingsRow>
	);
}

export function SegRow({
	label,
	hint,
	value,
	options,
	onChange,
}: {
	label: string;
	hint: string;
	value: string;
	options: string[];
	onChange: (value: string) => void;
}) {
	return (
		<SettingsRow label={label} hint={hint}>
			<div className="seg" aria-label={label}>
				{options.map((option) => (
					<button
						key={option}
						type="button"
						aria-pressed={option === value}
						onClick={() => onChange(option)}
					>
						{option}
					</button>
				))}
			</div>
		</SettingsRow>
	);
}

export function PromptEditor({
	promptList,
	version,
	body,
	transcriptId,
	isDirty,
	isDryRunning,
	onVersion,
	onBody,
	onTranscriptId,
	onDryRun,
}: {
	promptList: PromptListResponse | null;
	version: PromptVersionId;
	body: string;
	transcriptId: string;
	isDirty: boolean;
	isDryRunning: boolean;
	onVersion: (value: string) => void;
	onBody: (value: string) => void;
	onTranscriptId: (value: string) => void;
	onDryRun: () => void;
}) {
	const selected = promptList?.versions.find((item) => item.id === version);
	const chars = body.length;
	const tokens = Math.ceil(chars / 4);

	return (
		<section className="settings-group prompt-group">
			<div className="prompt-head">
				<h2 className="section-label">Advanced summarizer prompt</h2>
				{isDirty ? <span className="chip warn">dirty</span> : null}
			</div>
			<SegRow
				label="Version"
				hint={
					selected?.first_line ??
					"LLM instructions used to generate summary markdown, tags, and card descriptions."
				}
				value={version}
				options={promptVersions}
				onChange={onVersion}
			/>
			<div className="prompt-editor">
				<textarea
					value={body}
					spellCheck={false}
					onChange={(event) => onBody(event.currentTarget.value)}
				/>
				<div className="prompt-meter">
					<span>{chars.toLocaleString()} chars</span>
					<span>{tokens.toLocaleString()} tokens est.</span>
					{selected !== undefined ? (
						<span>{selected.len_tokens_est.toLocaleString()} saved est.</span>
					) : null}
				</div>
			</div>
			<SettingsRow
				label="Dry-run"
				hint="Run the selected prompt against an existing transcript id."
			>
				<input
					className="settings-input number"
					inputMode="numeric"
					value={transcriptId}
					placeholder="Transcript id"
					onChange={(event) => onTranscriptId(event.currentTarget.value)}
				/>
				<button
					className="btn"
					type="button"
					disabled={isDryRunning}
					onClick={onDryRun}
				>
					{isDryRunning ? "Running" : "Dry-run"}
				</button>
			</SettingsRow>
		</section>
	);
}

export function TokenRow({
	token,
	showToken,
	isCreating,
	canCreate,
	onCreate,
	onCopy,
	onToggleShow,
}: {
	token: string;
	showToken: boolean;
	isCreating: boolean;
	canCreate: boolean;
	onCreate: () => void;
	onCopy: () => void;
	onToggleShow: () => void;
}) {
	return (
		<SettingsRow
			label="Chrome extension token"
			hint="Create a per-user bearer token, copy it, then paste it into the Chrome extension options."
		>
			<input
				className="settings-input token"
				type={showToken ? "text" : "password"}
				value={token}
				placeholder="No extension token created"
				readOnly
			/>
			<button
				className="btn"
				type="button"
				disabled={!canCreate || isCreating}
				onClick={onCreate}
			>
				{isCreating ? "Creating" : "Create"}
			</button>
			<button className="btn" type="button" disabled={!token} onClick={onCopy}>
				Copy
			</button>
			<button
				className="btn ghost"
				type="button"
				disabled={!token}
				onClick={onToggleShow}
			>
				{showToken ? "Hide" : "Show"}
			</button>
		</SettingsRow>
	);
}

function readStoredExtensionToken(): string {
	try {
		return localStorage.getItem(EXTENSION_TOKEN_KEY) ?? "";
	} catch {
		return "";
	}
}

async function writeClipboard(value: string): Promise<boolean> {
	try {
		await navigator.clipboard.writeText(value);
		return true;
	} catch {
		return false;
	}
}

function draftFromConfig(
	config: Record<string, ConfigEntry>,
): Record<ConfigKey, ConfigValue> {
	const draft = {} as Record<ConfigKey, ConfigValue>;
	for (const key of configKeys) {
		draft[key] = config[key]?.value ?? "";
	}
	return draft;
}

function isMutable(entry: ConfigEntry | undefined): boolean {
	return entry?.mutable ?? false;
}

function stringDraft(value: ConfigValue | undefined): string {
	return typeof value === "string" ? value : "";
}

function numberDraft(value: ConfigValue | undefined): number {
	return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function booleanDraft(value: ConfigValue | undefined): boolean {
	return typeof value === "boolean" ? value : false;
}

function canManageUsers(user: CurrentUser): boolean {
	return (
		user.role === "admin" || user.role === "lan" || user.role === "machine"
	);
}

function disableBlockedReason(
	user: AdminUser,
	currentUser: CurrentUser | null,
	users: AdminUser[],
): string | null {
	if (currentUser?.user_id === user.id) {
		return "You cannot disable your own admin account.";
	}
	const activeAdminCount = users.filter(
		(candidate) => candidate.role === "admin" && !candidate.disabled,
	).length;
	if (user.role === "admin" && !user.disabled && activeAdminCount <= 1) {
		return "At least one active admin account is required.";
	}
	return null;
}

function userIdentity(user: CurrentUser): string {
	return user.display_name || user.email || user.kind;
}

function userLabel(user: AdminUser): string {
	return user.display_name || user.primary_email;
}

function tweaksEqual(left: Tweaks, right: Tweaks): boolean {
	return (
		left.variant === right.variant &&
		left.theme === right.theme &&
		left.density === right.density &&
		left.libraryLayout === right.libraryLayout
	);
}

async function responseMessage(response: Response): Promise<string> {
	const text = await response.text();
	if (!text) {
		return `${response.status} ${response.statusText}`;
	}
	try {
		const parsed = JSON.parse(text) as { detail?: unknown };
		return typeof parsed.detail === "string"
			? parsed.detail
			: JSON.stringify(parsed.detail ?? parsed);
	} catch {
		return text;
	}
}
