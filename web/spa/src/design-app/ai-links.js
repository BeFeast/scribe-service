// Copy for AI (#489): pure helpers behind the Share sheet's "Copy for AI"
// action and "AI links" list. No React, no fetch: unit-tested in
// tests/aiLinks.test.ts.

export const AI_EXPIRY_OPTIONS = [
	{ key: "7d", label: "7d", days: 7 },
	{ key: "30d", label: "30d", days: 30 },
	{ key: "never", label: "never", days: null },
];

export const DEFAULT_AI_EXPIRY = "30d";
export const AI_EXPIRY_STORAGE_KEY = "scribe.copyForAi.expiry";
export const AI_LINK_LABEL = "copy-for-ai";

const DAY_MS = 24 * 60 * 60 * 1000;

function isExpiryKey(value) {
	return AI_EXPIRY_OPTIONS.some((option) => option.key === value);
}

/** Last remembered expiry choice; the default when storage is missing,
 * blocked (private mode, sandbox) or holds an unknown value. */
export function readAiExpiry(storage) {
	try {
		const value = storage ? storage.getItem(AI_EXPIRY_STORAGE_KEY) : null;
		return isExpiryKey(value) ? value : DEFAULT_AI_EXPIRY;
	} catch {
		return DEFAULT_AI_EXPIRY;
	}
}

/** Remember the expiry choice; storage failures are ignored. */
export function writeAiExpiry(storage, key) {
	if (!isExpiryKey(key)) return;
	try {
		if (storage) storage.setItem(AI_EXPIRY_STORAGE_KEY, key);
	} catch {
		// Storage blocked: the choice just is not remembered.
	}
}

/** Timezone-aware ISO timestamp for the API, or null for "never". */
export function aiExpiresAt(key, now = new Date()) {
	const option =
		AI_EXPIRY_OPTIONS.find((item) => item.key === key) ||
		AI_EXPIRY_OPTIONS.find((item) => item.key === DEFAULT_AI_EXPIRY);
	if (option.days == null) return null;
	return new Date(now.getTime() + option.days * DAY_MS).toISOString();
}

/** POST body for creating an ai_transcript share link. */
export function aiLinkRequestBody(key, now = new Date()) {
	return {
		target_kind: "ai_transcript",
		expires_at: aiExpiresAt(key, now),
		label: AI_LINK_LABEL,
	};
}

/** Active (not revoked, not expired) ai_transcript links. */
export function activeAiLinks(links, now = new Date()) {
	if (!Array.isArray(links)) return [];
	return links.filter((link) => {
		if (!link || link.target_kind !== "ai_transcript") return false;
		if (link.revoked_at) return false;
		if (!link.expires_at) return true;
		const expires = new Date(link.expires_at);
		return Number.isNaN(expires.getTime()) || expires > now;
	});
}

function isoDate(value) {
	if (!value) return null;
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return null;
	return date.toISOString().slice(0, 10);
}

/** One-line meta for an AI link row:
 * `label · …hint · created YYYY-MM-DD · expires YYYY-MM-DD|never · N opens`. */
export function formatAiLinkMeta(link) {
	const parts = [];
	if (link.label) parts.push(link.label);
	if (link.token_hint) parts.push(`…${link.token_hint}`);
	const created = isoDate(link.created_at);
	if (created) parts.push(`created ${created}`);
	parts.push(`expires ${isoDate(link.expires_at) || "never"}`);
	const opens = Number(link.access_count) || 0;
	parts.push(`${opens} ${opens === 1 ? "open" : "opens"}`);
	return parts.join(" · ");
}

/** Copy text that is still being fetched.
 *
 * Safari drops the click's user activation across `await fetch`, so a plain
 * `writeText` after the request fails there. Handing `clipboard.write` a
 * ClipboardItem whose value is a promise keeps the activation. Falls back to
 * `writeText` once the text resolves. Rejects when the text promise rejects
 * or no clipboard API is available. */
export async function copyTextFromPromise(
	textPromise,
	clipboard = globalThis.navigator?.clipboard,
	ClipboardItemCtor = globalThis.ClipboardItem,
) {
	if (!clipboard) {
		await textPromise;
		throw new Error("clipboard unavailable");
	}
	if (typeof clipboard.write === "function" && ClipboardItemCtor) {
		try {
			const blobPromise = Promise.resolve(textPromise).then(
				(text) => new Blob([text], { type: "text/plain" }),
			);
			// Rejection is reported through `await textPromise` below.
			blobPromise.catch(() => {});
			const item = new ClipboardItemCtor({ "text/plain": blobPromise });
			await clipboard.write([item]);
			return await textPromise;
		} catch (error) {
			// The request itself failed: report that, not a clipboard error.
			await textPromise;
			if (typeof clipboard.writeText !== "function") throw error;
		}
	}
	if (typeof clipboard.writeText !== "function") {
		await textPromise;
		throw new Error("clipboard unavailable");
	}
	const text = await textPromise;
	await clipboard.writeText(text);
	return text;
}
