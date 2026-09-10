import { expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { adaptLibraryRow, adaptTranscript } from "../src/design-app/adapters.js";
import { PartialNotice } from "../src/design-app/transcript-detail.jsx";
import { PartialBanner } from "../src/design-app/mobile/MobileTranscriptDetail.jsx";
import { summaryPresentation } from "../src/design-app/summaryState.js";

for (const Component of [PartialNotice, PartialBanner]) {
 test(`${Component.name} distinguishes active work from failure`, () => {
  const running = renderToStaticMarkup(<Component transcript={{summary_state:"generating"}} />);
  expect(running).toContain("Summarizing");
  expect(running).not.toContain("failed");
  expect(running).not.toContain("<button");
  expect(running).not.toContain("POST");
  const failed = renderToStaticMarkup(<Component transcript={{summary_state:"failed"}} />);
  expect(failed).toContain("Summary failed");
  expect(failed).toContain("<button");
  const skipped = renderToStaticMarkup(<Component transcript={{summary_state:"not_requested"}} />);
  expect(skipped).toContain("Transcript only");
  expect(skipped).not.toContain("failed");
 });
}
test("same library ID transitions from generating to completed summary", () => {
 const pending = adaptLibraryRow({ id:517, is_partial:true, summary_state:"generating", summary_excerpt:"" });
 expect(summaryPresentation(pending).retry).toBe(false);
 const done = adaptLibraryRow({ id:517, is_partial:false, summary_state:"ready", summary_excerpt:"A completed summary" });
 expect(done.id).toBe(pending.id);
 expect(done.summary_md).toBe("A completed summary");
 expect(adaptTranscript({id:517,summary_md:null,summary_state:"generating"}).summary_state).toBe("generating");
});
