import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LibCards, LibFeed, LibTable } from "../src/design-app/library.jsx";
import { Row } from "../src/design-app/mobile/MobileLibrary.jsx";

const title = "A legacy social caption ".repeat(100);
const row = { id: 722, title, summary_md: "Summary", tags: [], duration_seconds: 12, created_at: new Date().toISOString() };

for (const View of [LibCards, LibFeed, LibTable]) {
  test(`${View.name} preserves the full title in a tooltip and uses the clamp class`, () => {
    const html = renderToStaticMarkup(<View rows={[row]} navigate={() => {}} onTag={() => {}} />);
    expect(html).toContain(`title="${title}"`);
    expect(html).toContain("library-title");
    expect(html).toContain(`>${title}</`);
  });
}

test("mobile Library uses the same safety net", () => {
  const html = renderToStaticMarkup(<Row transcript={row} onOpen={() => {}} />);
  expect(html).toContain(`title="${title}"`);
  expect(html).toContain("library-title");
});

test("CSS bounds Library titles to three lines and lets detail titles wrap", () => {
  const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  const clamp = css.match(/\.library-title\s*\{([^}]+)\}/)?.[1] ?? "";
  expect(clamp).toContain("-webkit-line-clamp: 3");
  expect(clamp).toContain("display: -webkit-box");
  expect(clamp).toContain("-webkit-box-orient: vertical");
  expect(clamp).toContain("overflow: hidden");
  expect(css).toMatch(/\.detail-h1,\s*\.detail-title\s*\{\s*overflow-wrap: anywhere;/);
});
