import {
	convertUsdToDisplayCurrency as convertUsdAmount,
	parseDisplayCurrency,
} from "../lib/currency";

export const FALLBACK_STATS = {
	window_days: 1,
	jobs_by_status: {
		done: 0,
		failed: 0,
		queued: 0,
		downloading: 0,
		transcribing: 0,
		summarizing: 0,
	},
	transcripts_done: 0,
	transcripts_partial: 0,
	queue_depth: 0,
	vast_spend_24h: 0,
	vast_spend_7d: 0,
	vast_spend_30d: 0,
	daily_spend_cap_usd: 0,
	backup: {
		last_success_iso: null,
		age_seconds: null,
		stale_after: 90000,
		stale: false,
		path: "",
	},
	worker_pool: { active: 0, total: 0 },
	system: [],
};

export let TRANSCRIPTS = [];
export let ACTIVE_JOBS = [];
export let RECENT_FAILURES = [];
export let STATS = FALLBACK_STATS;
export let SPEND_SERIES = [];
export let SCRIBE_USERS = [];
export let CURRENT_TRANSCRIPT = null;
export let CURRENT_TRANSCRIPT_STATE = { loading: false, error: null };
export let CURRENT_JOB = null;
export let CURRENT_JOB_STATE = { loading: false, error: null };
export let CURRENT_JOB_LOG = { connected: false, error: null, lines: [] };
export let DISPLAY_CURRENCY = "ILS";
export let PUBLIC_BASE_URL = "";

export function setRuntimeData(next) {
	TRANSCRIPTS = next.transcripts ?? TRANSCRIPTS;
	ACTIVE_JOBS = next.activeJobs ?? ACTIVE_JOBS;
	RECENT_FAILURES = next.failures ?? RECENT_FAILURES;
	STATS = next.stats ?? STATS;
	SPEND_SERIES = next.spendSeries ?? SPEND_SERIES;
	SCRIBE_USERS = next.users ?? SCRIBE_USERS;
	CURRENT_TRANSCRIPT = next.currentTranscript ?? null;
	CURRENT_TRANSCRIPT_STATE = next.currentTranscriptState ?? {
		loading: false,
		error: null,
	};
	CURRENT_JOB = next.currentJob ?? null;
	CURRENT_JOB_STATE = next.currentJobState ?? { loading: false, error: null };
	CURRENT_JOB_LOG = next.currentJobLog ?? {
		connected: false,
		error: null,
		lines: [],
	};
	DISPLAY_CURRENCY = normalizeDisplayCurrency(
		next.config?.display_currency ?? DISPLAY_CURRENCY,
	);
	PUBLIC_BASE_URL = next.config?.public_base_url ?? PUBLIC_BASE_URL;
	if (
		CURRENT_TRANSCRIPT &&
		!TRANSCRIPTS.some((row) => row.id === CURRENT_TRANSCRIPT.id)
	) {
		TRANSCRIPTS = [CURRENT_TRANSCRIPT, ...TRANSCRIPTS];
	}
	if (CURRENT_JOB && !ACTIVE_JOBS.some((job) => job.id === CURRENT_JOB.id)) {
		ACTIVE_JOBS = [CURRENT_JOB, ...ACTIVE_JOBS];
	}
}

export function tagCounts() {
	const map = new Map();
	for (const transcript of TRANSCRIPTS) {
		for (const tag of transcript.tags ?? []) {
			map.set(tag, (map.get(tag) ?? 0) + 1);
		}
	}
	return [...map.entries()].sort((a, b) => b[1] - a[1]);
}

export function fmtDuration(seconds) {
	if (seconds == null || Number.isNaN(Number(seconds))) return "\u2014";
	const total = Math.max(0, Math.round(Number(seconds)));
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	const rest = total % 60;
	if (hours > 0)
		return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
	return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function fmtElapsed(seconds) {
	if (seconds == null || Number.isNaN(Number(seconds))) return "0s";
	const total = Math.max(0, Math.round(Number(seconds)));
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	const rest = total % 60;
	if (hours > 0) return `${hours}h ${minutes}m`;
	return minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
}

export function fmtRelative(iso) {
	if (!iso) return "never";
	const timestamp = new Date(iso).getTime();
	if (!Number.isFinite(timestamp)) return "unknown";
	const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
	if (seconds < 90) return "a few seconds ago";
	if (seconds < 7200) return `${Math.round(seconds / 60)}m ago`;
	if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
	return `${Math.round(seconds / 86400)}d ago`;
}

export function fmtDate(iso) {
	if (!iso) return "\u2014";
	const date = new Date(iso);
	if (!Number.isFinite(date.getTime())) return "\u2014";
	return date.toLocaleString(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	});
}

export function fmtUsd(value) {
	return fmtDisplayCurrency(value);
}

export function fmtDisplayCurrency(value, currency = DISPLAY_CURRENCY) {
	if (value == null || Number.isNaN(Number(value))) return "\u2014";
	const normalized = normalizeDisplayCurrency(currency);
	const number = convertUsdToDisplayCurrency(Number(value), normalized);
	const fractionDigits = Math.abs(number) < 0.1 ? 4 : 2;
	if (normalized === "ILS") {
		return `₪${number.toFixed(fractionDigits)} ILS`;
	}
	return new Intl.NumberFormat(undefined, {
		style: "currency",
		currency: normalized,
		minimumFractionDigits: fractionDigits,
		maximumFractionDigits: fractionDigits,
	}).format(number);
}

export function convertUsdToDisplayCurrency(
	value,
	currency = DISPLAY_CURRENCY,
) {
	if (value == null || Number.isNaN(Number(value))) return Number.NaN;
	const normalized = normalizeDisplayCurrency(currency);
	return convertUsdAmount(Number(value), normalized);
}

export function normalizeDisplayCurrency(value) {
	return parseDisplayCurrency(value);
}

export function publicBaseUrl() {
	if (PUBLIC_BASE_URL) return PUBLIC_BASE_URL;
	if (typeof window !== "undefined" && window.location?.origin) {
		return window.location.origin;
	}
	return "https://scribe.oklabs.uk/";
}
