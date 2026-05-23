import { afterEach, describe, expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
	PrivateShareLinks,
	copyTextToClipboard,
} from "../src/components/PrivateShareLinks";
import { AuthProvider } from "../src/hooks/useAuth";

const originalDocument = globalThis.document;
const originalNavigator = globalThis.navigator;

afterEach(() => {
	Object.defineProperty(globalThis, "document", {
		configurable: true,
		value: originalDocument,
		writable: true,
	});
	Object.defineProperty(globalThis, "navigator", {
		configurable: true,
		value: originalNavigator,
		writable: true,
	});
});

function installClipboardDocument(execResult: boolean) {
	const appended: unknown[] = [];
	const removed: unknown[] = [];
	const textarea = {
		focusCalled: false,
		selectCalled: false,
		setAttribute(name: string, value: string) {
			this[name] = value;
		},
		focus() {
			this.focusCalled = true;
		},
		select() {
			this.selectCalled = true;
		},
		style: {},
		value: "",
	} as Record<string, unknown>;

	Object.defineProperty(globalThis, "document", {
		configurable: true,
		value: {
			body: {
				appendChild(node: unknown) {
					appended.push(node);
				},
				removeChild(node: unknown) {
					removed.push(node);
				},
			},
			createElement(tag: string) {
				expect(tag).toBe("textarea");
				return textarea;
			},
			execCommand(command: string) {
				expect(command).toBe("copy");
				return execResult;
			},
		},
		writable: true,
	});
	Object.defineProperty(globalThis, "navigator", {
		configurable: true,
		value: {},
		writable: true,
	});

	return { appended, removed, textarea };
}

describe("copyTextToClipboard", () => {
	test("falls back to a selected textarea when clipboard API is unavailable", async () => {
		const { appended, removed, textarea } = installClipboardDocument(true);

		await expect(
			copyTextToClipboard("https://scribe.test/share/abc"),
		).resolves.toBe(true);

		expect(textarea.value).toBe("https://scribe.test/share/abc");
		expect(textarea.focusCalled).toBe(true);
		expect(textarea.selectCalled).toBe(true);
		expect(appended).toHaveLength(1);
		expect(removed).toEqual(appended);
	});

	test("falls back when navigator.clipboard rejects", async () => {
		const { textarea } = installClipboardDocument(true);
		Object.defineProperty(globalThis, "navigator", {
			configurable: true,
			value: {
				clipboard: {
					writeText: () => Promise.reject(new Error("denied")),
				},
			},
			writable: true,
		});

		await expect(copyTextToClipboard("fallback text")).resolves.toBe(true);

		expect(textarea.value).toBe("fallback text");
	});
});

describe("PrivateShareLinks", () => {
	test("renders a compact share disclosure instead of inline target links", () => {
		const html = renderToStaticMarkup(
			<AuthProvider>
				<PrivateShareLinks
					id={42}
					targetKinds={new Set(["page"])}
					copyKinds={new Set(["page"])}
				/>
			</AuthProvider>,
		);

		expect(html).toContain("<details");
		expect(html).toContain("<summary");
		expect(html).toContain(">Share</summary>");
		expect(html).toContain("Create link");
		expect(html).toContain(">Page</button>");
		expect(html).not.toContain("Summary .md</button>");
		expect(html).not.toContain("Transcript .md</button>");
	});
});
