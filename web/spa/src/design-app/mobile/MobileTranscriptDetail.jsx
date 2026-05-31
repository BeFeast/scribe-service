// Mobile Transcript detail — Wave 2b / Issue #277.
//
// Literal port of `viewTranscript(id)` from `Scribe iOS.html` (mobile design
// source, SHA-256 421c930d9f2d5c1549dc632760f796992630a27eaa6fa38f28ff25584bc3ebb9),
// lines ~965-1015 + recipe CSS ~lines 290-315 (.detail-head/.detail-meta/
// .detail-tags/.seg/.transcript-body/.prose/.banner).
//
// Source mapping (Scribe iOS.html → this file):
//   ~966 function viewTranscript      → <MobileTranscriptDetail />
//   ~971 head (detail-head/title/meta) → DetailHead
//   ~983 segCtl                       → SegmentedControl
//   ~987 partialBanner                → PartialBanner
//   ~988 summaryHtml / transcriptHtml → ProseBody / TranscriptBody
//   ~1000 navRight share              → onShare prop (rendered by parent)
//   ~999 wire(): seg toggle           → React useState (segValue)
//
// Real-data wiring contract:
//   - Reads CURRENT_TRANSCRIPT / CURRENT_TRANSCRIPT_STATE from
//     design-app/data.js (same source as the desktop TranscriptDetail).
//   - Renders `t.summary_md` and `t.transcript_excerpt` directly. No mock
//     content, no fake share telemetry.
//   - Share trigger opens the ShareSheet with real navigator.share /
//     navigator.clipboard actions; fake "Shared via Telegram" toasts from
//     the prototype are dropped per AGENTS.md.

import React from "react";
import {
	CURRENT_TRANSCRIPT,
	CURRENT_TRANSCRIPT_STATE,
	TRANSCRIPTS,
	fmtDuration,
	fmtRelative,
	fmtUsd,
} from "../data.js";
import { IconAlert, IconExternal, IconWave } from "../icons.jsx";
import { ShareSheet } from "./ShareSheet.jsx";

/* ── Public component ────────────────────────────────────────────────── */

export function MobileTranscriptDetail({
	id,
	navigate,
	onRefresh: _onRefresh,
}) {
	void _onRefresh; // reserved for future re-fetch wiring; kept for parity
	const t =
		CURRENT_TRANSCRIPT ||
		(TRANSCRIPTS || []).find((row) => row.id === id) ||
		null;

	const [segValue, setSegValue] = React.useState(() =>
		t?.summary_md ? "summary" : "transcript",
	);
	const [shareOpen, setShareOpen] = React.useState(false);

	if (CURRENT_TRANSCRIPT_STATE.loading) {
		return (
			<DetailEmpty
				title="Loading transcript"
				body="Fetching /transcripts/{id}."
			/>
		);
	}
	if (!t || CURRENT_TRANSCRIPT_STATE.error) {
		return (
			<DetailEmpty
				title="Transcript unavailable"
				body={CURRENT_TRANSCRIPT_STATE.error || "No transcript is loaded."}
				navigate={navigate}
			/>
		);
	}

	const showSeg = Boolean(t.summary_md);
	const effectiveSeg = showSeg ? segValue : "transcript";

	return (
		<>
			<DetailHead t={t} onShare={() => setShareOpen(true)} />
			{showSeg ? (
				<SegmentedControl value={effectiveSeg} onChange={setSegValue} />
			) : null}
			{t.partial ? <PartialBanner /> : null}
			<div id="t-content">
				{effectiveSeg === "summary" ? (
					<ProseBody src={t.summary_md} />
				) : (
					<TranscriptBody src={t.transcript_excerpt} />
				)}
			</div>
			{shareOpen ? (
				<ShareSheet transcript={t} onClose={() => setShareOpen(false)} />
			) : null}
		</>
	);
}

/* ── Detail head (port of `head` template literal ~lines 971-982) ────── */

function DetailHead({ t, onShare }) {
	const lang = (t.lang || "").toString().toUpperCase();
	return (
		<div className="detail-head">
			<div className="detail-head-row">
				<h1 className="detail-title">{t.title}</h1>
				<button
					type="button"
					className="nb-btn icon detail-share-btn"
					aria-label="Share"
					onClick={onShare}
				>
					<IconExternal size={18} />
				</button>
			</div>
			<div className="detail-meta">
				<span>#{t.id}</span>
				<span className="sep">·</span>
				<span>{fmtDuration(t.duration_seconds)}</span>
				{lang ? (
					<>
						<span className="sep">·</span>
						<span>{lang}</span>
					</>
				) : null}
				<span className="sep">·</span>
				<span>{fmtUsd(t.vast_cost)}</span>
				<span className="sep">·</span>
				<span>{fmtRelative(t.created_at)}</span>
			</div>
			{Array.isArray(t.tags) && t.tags.length > 0 ? (
				<div className="detail-tags">
					{t.tags.map((tg) => (
						<span key={tg} className="tag">
							{tg}
						</span>
					))}
				</div>
			) : null}
		</div>
	);
}

/* ── Segmented control (port of `segCtl` ~lines 983-986) ─────────────── */

function SegmentedControl({ value, onChange }) {
	return (
		<div className="seg" id="t-seg" style={{ marginTop: 6 }}>
			<button
				type="button"
				data-v="summary"
				className={value === "summary" ? "active" : ""}
				onClick={() => onChange("summary")}
			>
				Summary
			</button>
			<button
				type="button"
				data-v="transcript"
				className={value === "transcript" ? "active" : ""}
				onClick={() => onChange("transcript")}
			>
				<IconWave size={15} /> Transcript
			</button>
		</div>
	);
}

/* ── Partial banner (port of `partialBanner` ~line 987) ──────────────── */

function PartialBanner() {
	return (
		<div className="banner partial-banner">
			<span className="b-ic">
				<IconAlert size={18} />
			</span>
			<div>
				<b>Summary unavailable.</b> The summarizer timed out. The transcript is
				saved — you can re-run summarization from Ops.
			</div>
		</div>
	);
}

/* ── Body renderers ──────────────────────────────────────────────────── */

function TranscriptBody({ src }) {
	return <div className="transcript-body">{src || ""}</div>;
}

// Minimal markdown renderer for the mobile detail page. Handles headings
// (#, ##, ###), paragraphs, bold (**...**), italic (*...*), inline code
// (`...`) and unordered lists. Sufficient for production summary_md
// content shape; falls back gracefully for unsupported syntax.
// Strip a leading YAML frontmatter block (--- ... ---) from summary_md
// before rendering. summary_md ships with frontmatter; the desktop
// SummaryBody strips it too. Without this the raw block leaks into the
// mobile Summary tab.
function stripSummaryFrontmatter(text) {
	if (!text.startsWith("---")) return text;
	const end = text.indexOf("\n---", 3);
	if (end === -1) return text;
	return text.slice(end + 4).replace(/^\n+/, "");
}

function ProseBody({ src }) {
	const blocks = React.useMemo(
		() => parseProse(stripSummaryFrontmatter(src || "")),
		[src],
	);
	return (
		<div className="prose">
			{blocks.map((block, idx) => (
				<ProseBlock key={`pb-${idx}-${block.kind}`} block={block} />
			))}
		</div>
	);
}

function ProseBlock({ block }) {
	if (block.kind === "h1") return <h1>{renderInline(block.text)}</h1>;
	if (block.kind === "h2") return <h2>{renderInline(block.text)}</h2>;
	if (block.kind === "h3") return <h3>{renderInline(block.text)}</h3>;
	if (block.kind === "ul") {
		return (
			<ul>
				{block.items.map((item, idx) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: list items are positional in source markdown; index is the stable identity
					<li key={`li-${idx}`}>{renderInline(item)}</li>
				))}
			</ul>
		);
	}
	if (block.kind === "code") {
		return (
			<pre>
				<code>{block.text}</code>
			</pre>
		);
	}
	return <p>{renderInline(block.text)}</p>;
}

function parseProse(src) {
	const lines = src.replace(/\r\n/g, "\n").split("\n");
	const blocks = [];
	let i = 0;
	while (i < lines.length) {
		const line = lines[i];
		if (!line.trim()) {
			i += 1;
			continue;
		}
		if (line.startsWith("```")) {
			const buf = [];
			i += 1;
			while (i < lines.length && !lines[i].startsWith("```")) {
				buf.push(lines[i]);
				i += 1;
			}
			if (i < lines.length) i += 1; // skip closing fence
			blocks.push({ kind: "code", text: buf.join("\n") });
			continue;
		}
		if (line.startsWith("### ")) {
			blocks.push({ kind: "h3", text: line.slice(4).trim() });
			i += 1;
			continue;
		}
		if (line.startsWith("## ")) {
			blocks.push({ kind: "h2", text: line.slice(3).trim() });
			i += 1;
			continue;
		}
		if (line.startsWith("# ")) {
			blocks.push({ kind: "h1", text: line.slice(2).trim() });
			i += 1;
			continue;
		}
		if (/^[-*]\s+/.test(line)) {
			const items = [];
			while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
				items.push(lines[i].replace(/^[-*]\s+/, ""));
				i += 1;
			}
			blocks.push({ kind: "ul", items });
			continue;
		}
		// paragraph: collect until blank line or block boundary
		const buf = [line];
		i += 1;
		while (
			i < lines.length &&
			lines[i].trim() &&
			!/^(#{1,3}\s|[-*]\s+|```)/.test(lines[i])
		) {
			buf.push(lines[i]);
			i += 1;
		}
		blocks.push({ kind: "p", text: buf.join(" ") });
	}
	return blocks;
}

function renderInline(text) {
	// Tokenize bold (**), italic (*), and inline code (`). Emit a flat
	// array of strings + React nodes so the parent can render a fragment.
	const out = [];
	let rest = text;
	let key = 0;
	const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/;
	while (rest.length > 0) {
		const match = re.exec(rest);
		if (!match) {
			out.push(rest);
			break;
		}
		if (match.index > 0) out.push(rest.slice(0, match.index));
		const token = match[0];
		const k = `inline-${key}`;
		key += 1;
		if (token.startsWith("**")) {
			out.push(<strong key={k}>{token.slice(2, -2)}</strong>);
		} else if (token.startsWith("`")) {
			out.push(<code key={k}>{token.slice(1, -1)}</code>);
		} else {
			out.push(<em key={k}>{token.slice(1, -1)}</em>);
		}
		rest = rest.slice(match.index + token.length);
	}
	return out;
}

/* ── Empty/error state ───────────────────────────────────────────────── */

function DetailEmpty({ title, body, navigate }) {
	return (
		<div className="detail-head">
			<h1 className="detail-title">{title}</h1>
			<div className="detail-meta">
				<span>{body}</span>
			</div>
			{navigate ? (
				<div className="detail-tags">
					<button
						type="button"
						className="nb-btn"
						onClick={() => navigate("library")}
					>
						Back to Library
					</button>
				</div>
			) : null}
		</div>
	);
}
