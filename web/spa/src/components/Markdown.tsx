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
	| { type: "strong"; text: string };

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
				key: blockKey("code", code.join("\n")),
				type: "code",
				text: code.join("\n"),
			});
			index += 1;
			continue;
		}
		const heading = /^(#{1,6})\s+(.+)$/.exec(line);
		if (heading !== null) {
			blocks.push({
				key: blockKey("heading", line),
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
				key: blockKey("blockquote", quote.join(" ")),
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
				key: blockKey("list", items.join("|")),
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
			key: blockKey("paragraph", paragraph.join(" ")),
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
					{block.items.map((item) => (
						<li key={blockKey("item", item)}>{renderInline(item)}</li>
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
		const codeStart = rest.indexOf("`");
		const strongStart = rest.indexOf("**");
		const starts = [codeStart, strongStart].filter((value) => value >= 0);
		const next = starts.length > 0 ? Math.min(...starts) : -1;
		if (next < 0) {
			parts.push({ type: "text", text: rest });
			break;
		}
		if (next > 0) {
			parts.push({ type: "text", text: rest.slice(0, next) });
			rest = rest.slice(next);
			continue;
		}
		if (rest.startsWith("`")) {
			const end = rest.indexOf("`", 1);
			if (end < 0) {
				parts.push({ type: "text", text: rest });
				break;
			}
			parts.push({ type: "code", text: rest.slice(1, end) });
			rest = rest.slice(end + 1);
			continue;
		}
		const end = rest.indexOf("**", 2);
		if (end < 0) {
			parts.push({ type: "text", text: rest });
			break;
		}
		parts.push({ type: "strong", text: rest.slice(2, end) });
		rest = rest.slice(end + 2);
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
			case "text":
				return <React.Fragment key={key}>{part.text}</React.Fragment>;
		}
	});
}

function blockKey(kind: string, text: string): string {
	let hash = 0;
	for (let index = 0; index < text.length; index += 1) {
		hash = (hash * 31 + text.charCodeAt(index)) | 0;
	}
	return `${kind}-${hash}`;
}
