import { expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { CopyForAiItem } from "../src/design-app/transcript-detail.jsx";

// Regression (PR #490 review): the Copy for AI trigger was a <div onClick>, so
// keyboard and screen-reader users could not reach the only way to create an AI link.
test("Copy for AI trigger is a keyboard-reachable native button", () => {
	const html = renderToStaticMarkup(<CopyForAiItem onCopy={() => {}} busy={false} status={null} />);
	expect(html.startsWith("<button")).toBe(true);
	expect(html).toContain('type="button"');
	expect(html).toContain('class="sh-item sh-item-button"');
	expect(html).toContain("Copy for AI");
	// A native button needs no role/tabIndex; a negative tabindex would hide it from Tab.
	expect(html).not.toContain("tabindex");
	expect(html).not.toContain("aria-busy");
	// <button> allows phrasing content only: no block <div> children.
	expect(html).not.toContain("<div");
});

test("Copy for AI trigger fires onCopy on click and exposes busy state", () => {
	let calls = 0;
	const element = CopyForAiItem({ onCopy: () => { calls += 1; }, busy: true, status: null });
	expect(element.type).toBe("button");
	element.props.onClick();
	expect(calls).toBe(1);
	expect(element.props["aria-busy"]).toBe("true");
});

test("Copy for AI trigger shows the copy status in place of the copy glyph", () => {
	const html = renderToStaticMarkup(
		<CopyForAiItem onCopy={() => {}} busy={false} status={<span className="sh-status ok">link copied</span>} />,
	);
	expect(html).toContain("link copied");
	expect(html).not.toContain("sh-keys");
});
