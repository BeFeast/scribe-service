import React from "react";

import { Markdown } from "../components/Markdown";
import type {
	LibraryLayout,
	ScribeDensity,
	ScribeTheme,
	ScribeVariant,
	Tweaks,
} from "../hooks/useTweaks";

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

type PromptVersionId = "v1" | "v2" | "v3";
type ConfigKey =
	| "daily_spend_cap_usd"
	| "worker_concurrency"
	| "bot_wall_retry"
	| "public_base_url"
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

const CONFIG_TOKEN_KEY = "scribe.configToken";
const configKeys: ConfigKey[] = [
	"daily_spend_cap_usd",
	"worker_concurrency",
	"bot_wall_retry",
	"public_base_url",
	"short_description_language",
	"webhook_default",
	"webhook_embed_transcript",
];
const promptVersions: PromptVersionId[] = ["v1", "v2", "v3"];

export function Settings({ tweaks, setTheme, replaceTweaks }: SettingsProps) {
	const [config, setConfig] = React.useState<Record<string, ConfigEntry>>({});
	const [draft, setDraft] = React.useState<Record<ConfigKey, ConfigValue>>(
		{} as Record<ConfigKey, ConfigValue>,
	);
	const [dirtyKeys, setDirtyKeys] = React.useState<Set<ConfigKey>>(new Set());
	const [restartKeys, setRestartKeys] = React.useState<string[]>([]);
	const [token, setToken] = React.useState(readStoredToken);
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
	const [showRotate, setShowRotate] = React.useState(false);
	const [status, setStatus] = React.useState<string | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [loading, setLoading] = React.useState(true);
	const [saving, setSaving] = React.useState(false);
	const [dryRunning, setDryRunning] = React.useState(false);

	const headers = React.useMemo(() => authHeaders(token), [token]);
	const promptDirty =
		promptBody !== savedPromptBody || promptVersion !== savedPromptVersion;

	const loadSettings = React.useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const [configResponse, promptsResponse] = await Promise.all([
				fetch("/api/config", { headers }),
				fetch("/api/prompts"),
			]);
			if (!configResponse.ok) {
				throw new Error(await responseMessage(configResponse));
			}
			if (!promptsResponse.ok) {
				throw new Error(await responseMessage(promptsResponse));
			}
			const configBody = (await configResponse.json()) as ConfigResponse;
			const promptsBody = (await promptsResponse.json()) as PromptListResponse;
			const activeVersion = promptsBody.active_version;
			const promptResponse = await fetch(`/api/prompts/${activeVersion}`);
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
	}, [headers]);

	React.useEffect(() => {
		void loadSettings();
	}, [loadSettings]);

	async function selectPromptVersion(next: string) {
		const version = next as PromptVersionId;
		setError(null);
		try {
			const response = await fetch(`/api/prompts/${version}`);
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
				const response = await fetch("/api/config", {
					method: "POST",
					headers: { ...headers, "Content-Type": "application/json" },
					body: JSON.stringify(payload),
				});
				if (!response.ok) {
					throw new Error(await responseMessage(response));
				}
				const body = (await response.json()) as ConfigResponse;
				setRestartKeys(body.restart_required);
			}
			if (promptBody !== savedPromptBody) {
				const response = await fetch(`/api/prompts/${promptVersion}`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ body: promptBody }),
				});
				if (!response.ok) {
					throw new Error(await responseMessage(response));
				}
			}
			if (promptVersion !== savedPromptVersion) {
				const response = await fetch("/api/prompts/active", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ version: promptVersion }),
				});
				if (!response.ok) {
					throw new Error(await responseMessage(response));
				}
			}
			if (await loadSettings()) {
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
			const response = await fetch("/api/prompts/dry-run", {
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

	function storeToken(nextToken: string) {
		setToken(nextToken.trim());
		try {
			localStorage.setItem(CONFIG_TOKEN_KEY, nextToken.trim());
		} catch {
			// Runtime use still works for this tab.
		}
	}

	async function copyToken() {
		if (!token) {
			return;
		}
		await navigator.clipboard.writeText(token);
		setStatus("Token copied");
	}

	async function rotateToken() {
		setError(null);
		try {
			const response = await fetch("/api/config/rotate-token", {
				method: "POST",
				headers,
			});
			if (!response.ok) {
				throw new Error(await responseMessage(response));
			}
			setStatus("Token rotated");
		} catch (rotateError) {
			setError(
				rotateError instanceof Error
					? rotateError.message
					: "token rotation failed",
			);
		} finally {
			setShowRotate(false);
		}
	}

	return (
		<section className="settings-page pane">
			<header className="pane-header">
				<div>
					<p className="eyebrow">Settings</p>
					<h1 className="pane-h1">Pipeline, summaries, appearance, access</h1>
					<p className="pane-sub">
						Runtime controls are saved in the service database and refreshed from the API.
					</p>
				</div>
				<div className="settings-actions">
					<button className="btn ghost" type="button" onClick={loadSettings}>
						Refresh
					</button>
					<button
						className="btn primary"
						type="button"
						disabled={saving || (dirtyKeys.size === 0 && !promptDirty)}
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
				<section className="settings-group">
					<h2 className="section-label">API access</h2>
					<TokenRow
						token={token}
						onStore={storeToken}
						onCopy={copyToken}
						onRotate={() => setShowRotate(true)}
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

			{showRotate ? (
				<div className="modal-backdrop" role="presentation">
					<dialog
						className="settings-modal compact"
						aria-label="Rotate API token"
						open
					>
						<header>
							<strong>Rotate API token</strong>
						</header>
						<p className="hint">
							Existing clients will need the new bearer token after rotation.
							The endpoint is currently a server-side stub.
						</p>
						<div className="modal-actions">
							<button
								className="btn ghost"
								type="button"
								onClick={() => setShowRotate(false)}
							>
								Cancel
							</button>
							<button
								className="btn primary"
								type="button"
								onClick={rotateToken}
							>
								Rotate
							</button>
						</div>
					</dialog>
				</div>
			) : null}
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
	onStore,
	onCopy,
	onRotate,
}: {
	token: string;
	onStore: (value: string) => void;
	onCopy: () => void;
	onRotate: () => void;
}) {
	function enterToken() {
		const next = window.prompt("Bearer token", token);
		if (next !== null) {
			onStore(next);
		}
	}

	return (
		<SettingsRow
			label="Bearer token"
			hint="Stored in this browser and used for protected config calls."
		>
			<input
				className="settings-input token"
				type="password"
				value={token ? maskToken(token) : ""}
				placeholder="No token set"
				readOnly
			/>
			<button className="btn" type="button" onClick={enterToken}>
				Use
			</button>
			<button className="btn" type="button" disabled={!token} onClick={onCopy}>
				Copy
			</button>
			<button className="btn ghost" type="button" onClick={onRotate}>
				Rotate
			</button>
		</SettingsRow>
	);
}

function readStoredToken(): string {
	try {
		return localStorage.getItem(CONFIG_TOKEN_KEY) ?? "";
	} catch {
		return "";
	}
}

function authHeaders(token: string): HeadersInit {
	if (!token) {
		return {};
	}
	return { Authorization: `Bearer ${token}` };
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

function maskToken(token: string): string {
	if (token.length <= 8) {
		return "****";
	}
	return `${token.slice(0, 4)}****${token.slice(-4)}`;
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
