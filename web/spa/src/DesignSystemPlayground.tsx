import { useMemo, useState } from "react";

type Variant = "paper" | "terminal" | "console";
type Theme = "light" | "dark";
type Density = "compact" | "cozy" | "comfy";

const variants: Variant[] = ["paper", "terminal", "console"];
const themes: Theme[] = ["light", "dark"];
const densities: Density[] = ["compact", "cozy", "comfy"];

export function DesignSystemPlayground() {
	const [density, setDensity] = useState<Density>("cozy");
	const combos = useMemo(
		() =>
			variants.flatMap((variant) =>
				themes.map((theme) => ({ variant, theme })),
			),
		[],
	);

	return (
		<main className="ds-playground">
			<section className="pane-header">
				<div>
					<h1 className="pane-h1">Design system playground</h1>
					<p className="pane-sub">
						All variants, themes, densities, and shared component classes.
					</p>
				</div>
				<div className="toggle" aria-label="Density">
					{densities.map((item) => (
						<button
							aria-pressed={density === item}
							key={item}
							onClick={() => setDensity(item)}
							type="button"
						>
							{item}
						</button>
					))}
				</div>
			</section>

			<section
				className="ds-playground-grid"
				aria-label="Design system variants"
			>
				{combos.map(({ variant, theme }) => (
					<article
						className="ds-scope"
						data-density={density}
						data-theme={theme}
						data-variant={variant}
						key={`${variant}-${theme}`}
					>
						<ComponentSampler
							density={density}
							theme={theme}
							variant={variant}
						/>
					</article>
				))}
			</section>
		</main>
	);
}

function ComponentSampler({
	density,
	theme,
	variant,
}: {
	density: Density;
	theme: Theme;
	variant: Variant;
}) {
	return (
		<div className="ds-sample-stack">
			<header className="pane-header">
				<div>
					<p className="section-label">
						{variant} / {theme} / {density}
					</p>
					<h2 className="detail-h1">Transcript review</h2>
					<div className="detail-meta">
						<span className="tag">youtube</span>
						<span className="tag">summary</span>
						<span className="kbd">K</span>
					</div>
				</div>
				<button className="iconbtn" aria-label="Refresh" type="button">
					R
				</button>
			</header>

			<div className="ds-row">
				<button className="btn primary" type="button">
					Primary
				</button>
				<button className="btn" type="button">
					Default
				</button>
				<button className="btn ghost" type="button">
					Ghost
				</button>
				<span className="spinner" aria-label="Loading" />
				<span className="live-dot" aria-label="Live" />
			</div>

			<div className="ds-row">
				<span className="chip ok">ok</span>
				<span className="chip warn">warn</span>
				<span className="chip err">err</span>
				<span className="chip info">info</span>
				<span className="chip run">run</span>
			</div>

			<div className="seg" aria-label="View mode">
				<button aria-pressed="true" type="button">
					Feed
				</button>
				<button aria-pressed="false" type="button">
					Table
				</button>
				<button aria-pressed="false" type="button">
					Cards
				</button>
			</div>

			<div className="metric-grid">
				<div className="metric">
					<span className="muted">Queue</span>
					<strong className="tnum">12</strong>
				</div>
				<div className="metric">
					<span className="muted">Spend</span>
					<strong className="tnum">$4.83</strong>
				</div>
			</div>

			<table className="lib-table">
				<thead>
					<tr>
						<th>Title</th>
						<th>Status</th>
						<th>Age</th>
					</tr>
				</thead>
				<tbody>
					<tr>
						<td>Design handoff review</td>
						<td>
							<span className="chip ok">done</span>
						</td>
						<td className="tnum">08:31</td>
					</tr>
				</tbody>
			</table>

			<div className="lib-feed">
				<article className="feed-item">
					<h3 className="feed-title">Library feed item</h3>
					<p className="feed-excerpt">
						A compact summary excerpt with enough text to validate rhythm, line
						height, and muted content color.
					</p>
				</article>
			</div>

			<div className="lib-cards">
				<article className="card">
					<strong>Card surface</strong>
					<span className="hint">Card metadata and helper copy.</span>
				</article>
			</div>

			<section className="transcript-body prose">
				<p>
					Prose supports <code>inline code</code>, links, lists, and block
					quotes for transcript detail pages.
				</p>
				<ul>
					<li>First note</li>
					<li>Second note</li>
				</ul>
				<ol>
					<li>Queued</li>
					<li>Transcribed</li>
				</ol>
				<blockquote>Quoted transcript passage.</blockquote>
			</section>

			<div className="pipeline">
				<div className="stage done">Download</div>
				<div className="stage active">Whisper</div>
				<div className="stage pending">Summarize</div>
				<div className="stage failed">Webhook</div>
			</div>

			<div className="bar-track" aria-label="Bar track">
				<span style={{ width: "68%" }} />
			</div>
			<div className="progressbar" aria-label="Progress">
				<span style={{ width: "42%" }} />
			</div>

			<div className="failure-row">
				<p className="err-title">Webhook failed</p>
				<p className="err-msg">
					The downstream endpoint rejected the callback.
				</p>
				<p className="err-meta mono">HTTP 503 after 3 attempts</p>
			</div>

			<svg
				className="spark"
				viewBox="0 0 120 36"
				role="img"
				aria-label="Sparkline"
			>
				<path
					className="area"
					d="M0 30 L0 22 L18 18 L36 24 L54 12 L72 16 L90 8 L120 14 L120 30 Z"
				/>
				<path
					className="line"
					d="M0 22 L18 18 L36 24 L54 12 L72 16 L90 8 L120 14"
				/>
				<circle className="dot" cx="90" cy="8" r="3" />
			</svg>

			<div className="settings-group">
				<div className="settings-row">
					<div>
						<div className="row-label">Pipeline</div>
						<div className="hint">Worker and callback defaults.</div>
					</div>
					<div className="row-control">
						<button className="btn" type="button">
							Save
						</button>
					</div>
				</div>
			</div>

			<div className="cmdk">
				<input
					className="cmdk-input"
					aria-label="Command input"
					value="open transcript"
					readOnly
				/>
				<div className="cmdk-list">
					<div className="cmdk-item" aria-selected="true">
						<span>Open transcript</span>
						<span className="kbd">Enter</span>
					</div>
				</div>
			</div>
			<div className="cmdk-overlay" hidden />
		</div>
	);
}
