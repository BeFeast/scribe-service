// Preflight-driven submit decision for the Scribe extension (issue #339).
//
// Pure and free of any browser-extension API reference so it loads under
// `importScripts` in the service worker AND `require()`s under bun (see
// tests/test_chrome_extension.py). The service worker calls `fetchPreflight`
// against Scribe's GET /preflight (offline yt-dlp extractor matching, #339)
// and feeds the verdict to `classifySubmit`, the single decision function for
// every submit path (toolbar popup + context menu).
(function (root) {
  // Hard cap on the preflight round-trip: past this the extension stops
  // waiting. The preflight is a courtesy check, never an infrastructure
  // hard-block — a timeout drops to the confirm state (#339).
  const PREFLIGHT_TIMEOUT_MS = 2000;

  // Popup copy for the non-submit verdicts.
  const CONFIRM_CONTAINER_MESSAGE =
    "This looks like a feed, playlist, channel, or search page — not a single video. Submit anyway?";
  const CONFIRM_UNKNOWN_MESSAGE =
    "Scribe couldn't confirm this page is a single video. Submit anyway?";
  const REFUSE_UNSUPPORTED_MESSAGE =
    "yt-dlp has no extractor for this page — nothing to submit.";

  // The submit decision (#339), pure. `preflightResult` is the parsed
  // GET /preflight body ({supported, extractor, return_type, single_media,
  // generic_only}) or null when the check failed/timed out. In priority order:
  // * local hard-refusals — non-http(s) scheme or Scribe's own host →
  //   "refuse" (never depend on the server's answer);
  // * no preflight verdict (error/timeout) → "confirm" — never hard-block a
  //   submit on infrastructure;
  // * `single_media` (a dedicated extractor whose _RETURN_TYPE is "video") →
  //   "submit" — the ONLY auto-submit signal;
  // * `supported` container (playlist/feed/channel/search) or `generic_only`
  //   (yt-dlp would only guess via Generic) → "confirm";
  // * neither → "refuse" — not even Generic wants it.
  function classifySubmit(url, baseHost, preflightResult) {
    let parsed;
    try {
      parsed = new URL(String(url || ""));
    } catch {
      return "refuse";
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "refuse";
    }
    if (baseHost && parsed.host === baseHost) {
      return "refuse";
    }
    if (!preflightResult) {
      return "confirm";
    }
    if (preflightResult.single_media) {
      return "submit";
    }
    if (preflightResult.supported || preflightResult.generic_only) {
      return "confirm";
    }
    return "refuse";
  }

  // The confirm/refuse copy for a given verdict + preflight body. Container and
  // generic-only states get a "Submit anyway" affordance; a hard-unsupported
  // URL gets a message only.
  function verdictMessage(preflightResult) {
    if (!preflightResult) {
      return CONFIRM_UNKNOWN_MESSAGE;
    }
    if (preflightResult.supported) {
      return CONFIRM_CONTAINER_MESSAGE;
    }
    if (preflightResult.generic_only) {
      return CONFIRM_UNKNOWN_MESSAGE;
    }
    return REFUSE_UNSUPPORTED_MESSAGE;
  }

  // GET /preflight?url=… with a hard AbortController timeout. Resolves to the
  // parsed verdict or null on ANY failure — timeout, network error, non-2xx,
  // unparseable body — so the caller falls back to the confirm state (#339).
  // `fetchImpl` is injectable for tests and defaults to the global fetch.
  async function fetchPreflight(
    baseUrl,
    url,
    { headers = {}, timeoutMs = PREFLIGHT_TIMEOUT_MS, fetchImpl } = {},
  ) {
    const doFetch = fetchImpl || ((...args) => fetch(...args));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await doFetch(
        `${baseUrl}/preflight?url=${encodeURIComponent(String(url || ""))}`,
        { headers, signal: controller.signal },
      );
      if (!response.ok) {
        return null;
      }
      const body = await response.json();
      return {
        supported: Boolean(body?.supported),
        extractor: body?.extractor ?? null,
        return_type: body?.return_type ?? null,
        single_media: Boolean(body?.single_media),
        generic_only: Boolean(body?.generic_only),
      };
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  root.scribePreflight = {
    PREFLIGHT_TIMEOUT_MS,
    CONFIRM_CONTAINER_MESSAGE,
    CONFIRM_UNKNOWN_MESSAGE,
    REFUSE_UNSUPPORTED_MESSAGE,
    classifySubmit,
    verdictMessage,
    fetchPreflight,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = root.scribePreflight;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
