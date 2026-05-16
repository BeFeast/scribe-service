import React from "react";

import { useEventSource } from "../hooks/useEventSource";

type LogTailProps = {
	jobId: number;
	status: string;
	error?: string | null;
};

type WorkerLogLine = {
	ts?: string | null;
	lvl?: string;
	stage?: string;
	msg?: string;
};

const TERMINAL = new Set(["done", "failed"]);

function parseLine(raw: string): WorkerLogLine | null {
	try {
		return JSON.parse(raw) as WorkerLogLine;
	} catch {
		return null;
	}
}

function formatStamp(value?: string | null) {
	if (!value) {
		return "--:--:--";
	}
	return new Intl.DateTimeFormat(undefined, {
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
	}).format(new Date(value));
}

function tag(line: WorkerLogLine) {
	return line.stage ?? line.lvl?.toLowerCase() ?? "job";
}

export function LogTail({ jobId, status, error }: LogTailProps) {
	const [lines, setLines] = React.useState<WorkerLogLine[]>([]);
	const terminal = TERMINAL.has(status);

	React.useEffect(() => {
		setLines([]);
	}, [jobId]);

	useEventSource(
		`/api/jobs/${jobId}/log/stream`,
		(raw) => {
			const parsed = parseLine(raw);
			if (parsed !== null) {
				setLines((current) => [...current, parsed].slice(-200));
			}
		},
		{ enabled: !terminal || lines.length === 0 },
	);

	const displayLines =
		lines.length > 0
			? lines
			: [
					{
						ts: null,
						stage: status,
						msg: status === "failed" ? (error ?? "job failed") : "waiting for worker logs",
					},
				];

	return (
		<section className="log-tail" aria-label="Log tail">
			<div className="section-heading">
				<h2>Log tail</h2>
				{!terminal ? <span className="live-dot" aria-hidden="true" /> : null}
			</div>
			<pre>
				{displayLines.map((line, index) => (
					<code key={`${line.ts ?? "pending"}-${index}`}>
						{formatStamp(line.ts)} {tag(line)} {line.msg ?? ""}
					</code>
				))}
			</pre>
		</section>
	);
}
