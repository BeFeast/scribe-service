import { FALLBACK_STATS } from "./data.js";

const defaultStages = {
	queued: { state: "pending" },
	downloading: { state: "pending" },
	transcribing: { state: "pending" },
	summarizing: { state: "pending" },
	done: { state: "pending" },
};

export function adaptLibraryRow(row) {
	const summary = row.summary_md ?? row.summary_excerpt ?? null;
	return {
		id: Number(row.id),
		video_id: row.video_id ?? "",
		title: row.title || row.video_id || `Transcript #${row.id}`,
		tags: Array.isArray(row.tags) ? row.tags : [],
		lang: row.lang ?? "\u2014",
		duration_seconds: row.duration_seconds ?? null,
		vast_cost: row.vast_cost ?? null,
		created_at: row.created_at ?? new Date().toISOString(),
		source_url: row.source_url ?? null,
		source_label: row.source_label ?? null,
		summary_md: row.is_partial ? null : summary,
		summary_excerpt: row.summary_excerpt ?? summary ?? "",
		is_partial: Boolean(row.is_partial || summary == null),
		transcript_excerpt: row.transcript_excerpt ?? row.transcript_md ?? "",
		job_id: row.job_id ?? null,
	};
}

export function adaptTranscript(row) {
	return {
		...adaptLibraryRow({
			...row,
			summary_excerpt: row.summary_md ?? row.summary_excerpt,
		}),
		summary_md: row.summary_md ?? null,
		transcript_excerpt: stripFrontmatter(
			row.transcript_md ?? row.transcript_excerpt ?? "",
		),
		job_id: row.job_id ?? null,
	};
}

export function adaptJob(job) {
	const id = Number(job.id ?? job.job_id);
	const status = job.status ?? "queued";
	return {
		id,
		job_id: id,
		video_id: job.video_id ?? "",
		url:
			job.url ??
			job.source_url ??
			(job.video_id ? `https://youtu.be/${job.video_id}` : ""),
		source_url: job.source_url ?? null,
		source_label: job.source_label ?? null,
		title: job.title || job.transcript?.title || job.video_id || `Job #${id}`,
		status,
		source: job.source ?? job.source_label ?? "manual",
		started_at: job.started_at ?? new Date().toISOString(),
		elapsed_s: Math.round(job.elapsed_s ?? 0),
		error: job.error ?? null,
		callback_url: job.callback_url ?? null,
		transcript: job.transcript ?? null,
		stages: normalizeStages(job.stages, status),
	};
}

export function adaptFailure(row) {
	return {
		id: Number(row.id ?? row.job_id),
		video_id: row.video_id ?? "",
		url: row.url ?? row.source_url ?? "",
		title: row.title || row.video_id || `Job #${row.id ?? row.job_id}`,
		error: row.error ?? "failed",
		failed_at:
			row.failed_at ??
			row.updated_at ??
			row.finished_at ??
			new Date().toISOString(),
		updated_at: row.updated_at ?? row.failed_at ?? new Date().toISOString(),
		source: row.source ?? row.source_label ?? "manual",
	};
}

export function adaptOps(body) {
	return {
		...FALLBACK_STATS,
		...body,
		worker_pool: {
			...FALLBACK_STATS.worker_pool,
			...(body?.worker_pool ?? {}),
		},
		backup: { ...FALLBACK_STATS.backup, ...(body?.backup ?? {}) },
		jobs_by_status: {
			...FALLBACK_STATS.jobs_by_status,
			...(body?.jobs_by_status ?? {}),
		},
	};
}

export function adaptUsers(me, users) {
	const rows = Array.isArray(users) ? users : [];
	if (rows.length === 0 && me?.email) {
		return [
			adaptUser(
				{
					...me,
					primary_email: me.email,
					display_name: me.display_name,
					disabled: false,
				},
				me,
			),
		];
	}
	return rows.map((user) => adaptUser(user, me));
}

function adaptUser(user, me) {
	const email = user.primary_email ?? user.email ?? "unknown";
	return {
		id: user.id ?? null,
		owner_id: user.owner_id ?? null,
		email,
		name: user.display_name ?? user.name ?? email,
		role: user.role ?? "user",
		state: user.disabled ? "disabled" : "active",
		source: user.clerk_subject ? "clerk" : "manual",
		clerk_subject: user.clerk_subject ?? null,
		last_seen: user.updated_at ?? user.last_seen ?? null,
		calls_24h: user.calls_24h ?? 0,
		is_me: Boolean(
			(me?.email && me.email === email) ||
				(me?.user_id && me.user_id === user.id),
		),
	};
}

function normalizeStages(stages, status) {
	const normalized = structuredClone(defaultStages);
	for (const [key, value] of Object.entries(stages ?? {})) {
		normalized[key] = { ...normalized[key], ...value };
	}
	const order = [
		"queued",
		"downloading",
		"transcribing",
		"summarizing",
		"done",
	];
	const activeIndex = order.indexOf(status);
	if (activeIndex >= 0) {
		for (let index = 0; index < activeIndex; index += 1) {
			normalized[order[index]] = { ...normalized[order[index]], state: "done" };
		}
		normalized[status] = {
			...normalized[status],
			state: status === "done" ? "done" : "active",
		};
	}
	if (status === "failed") {
		const failedKey =
			order.find((key) => normalized[key]?.state === "active") ?? "summarizing";
		normalized[failedKey] = { ...normalized[failedKey], state: "failed" };
	}
	return normalized;
}

function stripFrontmatter(text) {
	if (!text.startsWith("---")) return text;
	const end = text.indexOf("\n---", 3);
	if (end === -1) return text;
	return text.slice(end + 4).replace(/^\n+/, "");
}
