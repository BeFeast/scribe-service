import { expect, test } from "bun:test";
import { refreshTranscript } from "../src/design-app/api.jsx";
import { createPollLoop } from "../src/hooks/pollLoop";
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

test("detail polling replaces saved partial with completed summary without navigation", async () => {
 let ready = false;
 let state: any;
 let next: (() => void) | undefined;
 const auth = { protectedFetch: async () => Response.json({id:517, summary_md:ready ? "Completed summary" : null, summary_state:ready ? "ready" : "generating"}) };
 const loop = createPollLoop({
  fn: signal => refreshTranscript(auth,517,signal,(value: any) => {state=value;}),
  interval:5000, isHidden:()=>false,
  setTimeout: callback => {next=callback;return 1;}, clearTimeout:()=>{next=undefined;},
 });
 loop.tick(); await flush();
 expect(state.value.summary_state).toBe("generating");
 ready=true; next?.(); await flush();
 expect(state.value.summary_md).toBe("Completed summary");
 expect(state.value.id).toBe(517);
 loop.stop();
});

test("navigation abort prevents previous transcript response from replacing current detail", async () => {
 let respond: ((value:Response)=>void) | undefined;
 const auth = {protectedFetch: () => new Promise<Response>(resolve=>{respond=resolve;})};
 const controller = new AbortController();
 let updates=0;
 const request=refreshTranscript(auth,517,controller.signal,()=>{updates++;});
 controller.abort();
 respond?.(Response.json({id:517,summary_md:"stale"}));
 await request;
 expect(updates).toBe(0);
});
