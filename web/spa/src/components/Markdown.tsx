import React from "react";

type Block =
	| { key: string; type: "heading"; level: number; text: string }
	| { key: string; type: "paragraph"; text: string }
	| { key: string; type: "list"; ordered: boolean; items: string[] }
	| { key: string; type: "blockquote"; text: string }
	| { key: string; type: "code"; text: string };

type InlinePart =
	| { type: "text"; text: string }
	| { type: "code"; text: string }
	| { type: "strong"; text: string }
	| { type: "link"; text: string; href: string };

const inlinePattern =
	/(`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\*\*[^*]+\*\*)/g;
const plainUrlPattern = /https?:\/\/[^\s<>'"]+/g;
const trailingUrlPunctuation = ".,;:!?)]}";

export function Markdown({ body }: { body: string }) {
	const blocks = React.useMemo(() => parseBlocks(body), [body]);
	return (
		<div className="prose">{blocks.map((block) => renderBlock(block))}</div>
	);
}

function parseBlocks(body: string): Block[] {
	const lines = body.replace(/\r\n/g, "\n").split("\n");
	const blocks: Block[] = [];
	let index = 0;

	while (index < lines.length) {
		const line = lines[index] ?? "";
		if (line.trim() === "") {
			index += 1;
			continue;
		}
		if (line.startsWith("```")) {
			const code: string[] = [];
			index += 1;
			while (index < lines.length && !lines[index]?.startsWith("```")) {
				code.push(lines[index] ?? "");
				index += 1;
			}
			blocks.push({
				key: blockKey("code", code.join("\n"), blocks.length),
				type: "code",
				text: code.join("\n"),
			});
			index += 1;
			continue;
		}
		const heading = /^(#{1,6})\s+(.+)$/.exec(line);
		if (heading !== null) {
			blocks.push({
				key: blockKey("heading", line, blocks.length),
				type: "heading",
				level: heading[1].length,
				text: heading[2],
			});
			index += 1;
			continue;
		}
		if (line.startsWith(">")) {
			const quote: string[] = [];
			while (index < lines.length && (lines[index] ?? "").startsWith(">")) {
				quote.push((lines[index] ?? "").replace(/^>\s?/, ""));
				index += 1;
			}
			blocks.push({
				key: blockKey("blockquote", quote.join(" "), blocks.length),
				type: "blockquote",
				text: quote.join(" "),
			});
			continue;
		}
		const unordered = /^\s*[-*]\s+(.+)$/.exec(line);
		const ordered = /^\s*\d+[.)]\s+(.+)$/.exec(line);
		if (unordered !== null || ordered !== null) {
			const isOrdered = ordered !== null;
			const items: string[] = [];
			while (index < lines.length) {
				const next = lines[index] ?? "";
				const match = isOrdered
					? /^\s*\d+[.)]\s+(.+)$/.exec(next)
					: /^\s*[-*]\s+(.+)$/.exec(next);
				if (match === null) {
					break;
				}
				items.push(match[1]);
				index += 1;
			}
			blocks.push({
				key: blockKey("list", items.join("|"), blocks.length),
				type: "list",
				ordered: isOrdered,
				items,
			});
			continue;
		}

		const paragraph: string[] = [];
		while (index < lines.length && (lines[index] ?? "").trim() !== "") {
			paragraph.push((lines[index] ?? "").trim());
			index += 1;
		}
		blocks.push({
			key: blockKey("paragraph", paragraph.join(" "), blocks.length),
			type: "paragraph",
			text: paragraph.join(" "),
		});
	}

	return blocks;
}

function renderBlock(block: Block): React.ReactNode {
	switch (block.type) {
		case "heading": {
			const Tag = `h${block.level}` as keyof JSX.IntrinsicElements;
			return <Tag key={block.key}>{renderInline(block.text)}</Tag>;
		}
		case "paragraph":
			return <p key={block.key}>{renderInline(block.text)}</p>;
		case "blockquote":
			return (
				<blockquote key={block.key}>{renderInline(block.text)}</blockquote>
			);
		case "code":
			return (
				<pre key={block.key}>
					<code>{block.text}</code>
				</pre>
			);
		case "list": {
			const Tag = block.ordered ? "ol" : "ul";
			return (
				<Tag key={block.key}>
					{block.items.map((item, index) => (
						<li key={blockKey("item", item, index)}>{renderInline(item)}</li>
					))}
				</Tag>
			);
		}
	}
}

function renderInline(text: string): React.ReactNode[] {
	const parts: InlinePart[] = [];
	let rest = text;
	while (rest.length > 0) {
		const match = inlinePattern.exec(rest);
		const next = match?.index ?? -1;
		if (next < 0) {
			pushTextParts(parts, rest);
			break;
		}
		if (next > 0) {
			pushTextParts(parts, rest.slice(0, next));
			rest = rest.slice(next);
			inlinePattern.lastIndex = 0;
			continue;
		}
		if (rest.startsWith("`")) {
			const value = match?.[0] ?? "";
			parts.push({ type: "code", text: value.slice(1, -1) });
			rest = rest.slice(value.length);
			inlinePattern.lastIndex = 0;
			continue;
		}
		if (rest.startsWith("[")) {
			const value = match?.[0] ?? "";
			const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(value);
			if (link === null) {
				pushTextParts(parts, value);
			} else {
				parts.push({ type: "link", text: link[1], href: link[2] });
			}
			rest = rest.slice(value.length);
			inlinePattern.lastIndex = 0;
			continue;
		}
		const value = match?.[0] ?? "";
		parts.push({ type: "strong", text: value.slice(2, -2) });
		rest = rest.slice(value.length);
		inlinePattern.lastIndex = 0;
	}

	let nextKey = 0;
	return parts.map((part) => {
		const key = `${part.type}-${part.text}-${nextKey}`;
		nextKey += 1;
		switch (part.type) {
			case "code":
				return <code key={key}>{part.text}</code>;
			case "strong":
				return <strong key={key}>{part.text}</strong>;
			case "link":
				return (
					<a
						key={key}
						href={part.href}
						target="_blank"
						rel="noopener noreferrer"
					>
						{part.text}
					</a>
				);
			case "text":
				return <React.Fragment key={key}>{part.text}</React.Fragment>;
		}
	});
}

function pushTextParts(parts: InlinePart[], text: string) {
	let cursor = 0;
	for (const match of text.matchAll(plainUrlPattern)) {
		if (match.index > cursor) {
			parts.push({ type: "text", text: text.slice(cursor, match.index) });
		}
		const [href, trailing] = splitTrailingUrlPunctuation(match[0]);
		parts.push({ type: "link", text: href, href });
		if (trailing.length > 0) {
			parts.push({ type: "text", text: trailing });
		}
		cursor = match.index + match[0].length;
	}
	if (cursor < text.length) {
		parts.push({ type: "text", text: text.slice(cursor) });
	}
}

function splitTrailingUrlPunctuation(rawUrl: string): [string, string] {
	let href = rawUrl;
	let trailing = "";
	while (
		href.length > 0 &&
		trailingUrlPunctuation.includes(href[href.length - 1] ?? "")
	) {
		if (href.endsWith(")") && count(href, "(") >= count(href, ")")) {
			break;
		}
		trailing = `${href[href.length - 1]}${trailing}`;
		href = href.slice(0, -1);
	}
	return [href, trailing];
}

function count(text: string, needle: string): number {
	return text.split(needle).length - 1;
}

function blockKey(kind: string, text: string, index: number): string {
	let hash = 0;
	for (let offset = 0; offset < text.length; offset += 1) {
		hash = (hash * 31 + text.charCodeAt(offset)) | 0;
	}
	return `${kind}-${index}-${hash}`;
}
