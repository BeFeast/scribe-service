import React from "react";

import type { Route, RoutePage } from "../hooks/useRoute";

type SidebarProps = {
  route: Route;
  navigate: (route: Route) => void;
};

type LibraryResponse = {
  rows?: Array<{ tags?: string[] | null }>;
};

type OpsResponse = {
  queue_depth?: number;
  transcripts_done?: number;
  transcripts_partial?: number;
  worker_pool?: {
    active?: number;
    total?: number;
  };
};

type TagCount = {
  tag: string;
  count: number;
};

type PipelineStats = {
  queueDepth: number;
  done: number;
  partial: number;
  workers: string;
};

const navItems: Array<{ page: RoutePage; label: string }> = [
  { page: "library", label: "Library" },
  { page: "queue", label: "Queue" },
  { page: "ops", label: "Ops" },
  { page: "settings", label: "Settings" },
];

// TODO: replace with /api/library + /api/ops once the sidebar gets server-provided tag counts.
const mockTags: TagCount[] = [
  { tag: "systems", count: 8 },
  { tag: "research", count: 5 },
  { tag: "ops", count: 3 },
];

// TODO: replace with /api/library + /api/ops once the pipeline mini-stats contract is final.
const mockPipeline: PipelineStats = {
  queueDepth: 2,
  done: 42,
  partial: 4,
  workers: "1/2",
};

function tagCountsFromLibrary(body: LibraryResponse): TagCount[] {
  const counts = new Map<string, number>();
  for (const row of body.rows ?? []) {
    for (const tag of row.tags ?? []) {
      counts.set(tag, (counts.get(tag) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))
    .slice(0, 8);
}

function pipelineFromOps(body: OpsResponse): PipelineStats {
  const active = body.worker_pool?.active ?? 0;
  const capacity = body.worker_pool?.total ?? 0;
  return {
    queueDepth: body.queue_depth ?? 0,
    done: body.transcripts_done ?? 0,
    partial: body.transcripts_partial ?? 0,
    workers: `${active}/${capacity}`,
  };
}

export function Sidebar({ route, navigate }: SidebarProps) {
  const [tags, setTags] = React.useState<TagCount[]>(mockTags);
  const [pipeline, setPipeline] = React.useState<PipelineStats>(mockPipeline);
  const [isMock, setIsMock] = React.useState(true);

  React.useEffect(() => {
    const abort = new AbortController();

    async function loadSidebarData() {
      try {
        const [libraryResponse, opsResponse] = await Promise.all([
          fetch("/api/library?limit=100", { signal: abort.signal }),
          fetch("/api/ops", { signal: abort.signal }),
        ]);
        if (!libraryResponse.ok || !opsResponse.ok) {
          throw new Error("sidebar endpoints unavailable");
        }
        const libraryBody = (await libraryResponse.json()) as LibraryResponse;
        const opsBody = (await opsResponse.json()) as OpsResponse;
        setTags(tagCountsFromLibrary(libraryBody));
        setPipeline(pipelineFromOps(opsBody));
        setIsMock(false);
      } catch (error) {
        if (!abort.signal.aborted) {
          setTags(mockTags);
          setPipeline(mockPipeline);
          setIsMock(true);
        }
      }
    }

    void loadSidebarData();
    return () => abort.abort();
  }, []);

  return (
    <aside className="sidebar" aria-label="Primary">
      <section className="sidebar-section">
        <h2>Browse</h2>
        <div className="nav-list">
          {navItems.map((item) => (
            <button
              type="button"
              key={item.page}
              className={route.page === item.page ? "nav-item active" : "nav-item"}
              onClick={() => navigate({ page: item.page, params: {} })}
            >
              {item.label}
            </button>
          ))}
        </div>
      </section>
      <section className="sidebar-section">
        <div className="section-heading">
          <h2>Tags</h2>
          {isMock ? <span className="mock-chip">[mock]</span> : null}
        </div>
        <div className="tag-list">
          {tags.length > 0 ? (
            tags.map((item) => (
              <button
                type="button"
                key={item.tag}
                className={route.params.tag === item.tag ? "tag-pill active" : "tag-pill"}
                onClick={() => navigate({ page: "library", params: { tag: item.tag } })}
              >
                <span>{item.tag}</span>
                <span className="tnum">{item.count}</span>
              </button>
            ))
          ) : (
            <p className="empty-note">No tags yet</p>
          )}
        </div>
      </section>
      <section className="sidebar-section pipeline-mini">
        <div className="section-heading">
          <h2>Pipeline</h2>
          {isMock ? <span className="mock-chip">[mock]</span> : null}
        </div>
        <dl className="mini-stats">
          <div>
            <dt>Queue</dt>
            <dd>{pipeline.queueDepth}</dd>
          </div>
          <div>
            <dt>Workers</dt>
            <dd>{pipeline.workers}</dd>
          </div>
          <div>
            <dt>Done</dt>
            <dd>{pipeline.done}</dd>
          </div>
          <div>
            <dt>Partial</dt>
            <dd>{pipeline.partial}</dd>
          </div>
        </dl>
      </section>
    </aside>
  );
}
