// Mobile transcript / share-sheet icons — Wave 2a / Issue #277.
//
// Verbatim ports of the SVG geometry used by `Scribe iOS.html` (mobile
// design source, SHA-256
// 421c930d9f2d5c1549dc632760f796992630a27eaa6fa38f28ff25584bc3ebb9) at
// the `I = { ... }` icon table ~746-763. The desktop `design-app/icons.jsx`
// table draws the same glyphs with different paths; the issue requires
// "SVGs verbatim from Scribe iOS.html", so the mobile branch consumes
// these iOS-source SVGs directly.
//
// Source mapping (Scribe iOS.html → this file):
//   ~755 I.share  → IconShareIOS
//   ~756 I.link   → IconLinkIOS
//   ~757 I.copy   → IconCopyIOS
//   ~758 I.rss    → IconRSSIOS
//   ~760 I.warn   → IconWarnIOS
//   ~761 I.check  → IconCheckIOS
//   ~762 I.play   → IconPlayIOS
//   ~763 I.doc    → IconDocIOS

import React from "react";

export function IconShareIOS({ size = 21 }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 22 22"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.9"
			strokeLinecap="round"
			strokeLinejoin="round"
			aria-hidden="true"
		>
			<path d="M11 14V3M7.5 6.5L11 3l3.5 3.5" />
			<path d="M6 10H4.5v8h13v-8H16" />
		</svg>
	);
}

export function IconLinkIOS({ size = 18 }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 20 20"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.7"
			strokeLinecap="round"
			aria-hidden="true"
		>
			<path d="M8 11a3 3 0 0 0 4.2 0l2.3-2.3a3 3 0 0 0-4.2-4.2L9 5.8" />
			<path d="M12 9a3 3 0 0 0-4.2 0l-2.3 2.3a3 3 0 1 0 4.2 4.2L11 14.2" />
		</svg>
	);
}

export function IconCopyIOS({ size = 18 }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 20 20"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.7"
			strokeLinejoin="round"
			aria-hidden="true"
		>
			<rect x="7" y="7" width="9" height="9" rx="2" />
			<path d="M13 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" />
		</svg>
	);
}

export function IconRSSIOS({ size = 18 }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 20 20"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.8"
			strokeLinecap="round"
			aria-hidden="true"
		>
			<circle cx="5" cy="15" r="1.3" fill="currentColor" stroke="none" />
			<path d="M4 9.5a6.5 6.5 0 0 1 6.5 6.5M4 5a11 11 0 0 1 11 11" />
		</svg>
	);
}

export function IconWarnIOS({ size = 18 }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 20 20"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.7"
			strokeLinecap="round"
			strokeLinejoin="round"
			aria-hidden="true"
		>
			<path d="M10 3l8 14H2z" />
			<path d="M10 8v4M10 14.5v.5" />
		</svg>
	);
}

export function IconCheckIOS({ size = 16 }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 16 16"
			fill="none"
			stroke="currentColor"
			strokeWidth="2.2"
			strokeLinecap="round"
			strokeLinejoin="round"
			aria-hidden="true"
		>
			<path d="M3.5 8.5l3 3 6-7" />
		</svg>
	);
}

export function IconPlayIOS({ size = 18 }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 20 20"
			fill="currentColor"
			aria-hidden="true"
		>
			<path d="M6 4l11 6-11 6z" />
		</svg>
	);
}

export function IconDocIOS({ size = 18 }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 20 20"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.7"
			strokeLinejoin="round"
			aria-hidden="true"
		>
			<path d="M5 2.5h6l4 4v11H5z" />
			<path d="M11 2.5v4h4M7.5 11h5M7.5 14h5" />
		</svg>
	);
}
