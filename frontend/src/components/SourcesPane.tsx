import type { Source } from "../types";
import { KindIcon } from "../KindIcon";
import { isWorking, plural, statusText } from "../format";

interface Props {
  sources: Source[];
  included: Set<number>;
  loading: boolean;
  openSourceId: number | null;
  onToggle: (sourceId: number) => void;
  onIncludeAll: () => void;
  onOpen: (source: Source) => void;
  onAdd: () => void;
}

/** The collection's sources. Ticking one decides whether the next question can draw on it. */
export function SourcesPane({ sources, included, loading, openSourceId, onToggle, onIncludeAll, onOpen, onAdd }: Props) {
  const allIncluded = sources.length > 0 && sources.every((source) => included.has(source.id));
  return (
    <>
      <div className="pane-head">
        <h2>Sources</h2>
        <button type="button" className="button" onClick={onAdd}>Add</button>
      </div>
      {sources.length > 0 && (
        <p className="scope-line">
          {allIncluded ? (
            <>Questions use all {plural(sources.length, "source")}.</>
          ) : (
            <>
              Questions use {included.size} of {sources.length}.{" "}
              <button type="button" onClick={onIncludeAll}>Use all</button>
            </>
          )}
        </p>
      )}
      {loading && sources.length === 0 ? (
        <p className="scope-line">Loading sources…</p>
      ) : sources.length === 0 ? (
        <div className="scope-line">
          <p style={{ marginTop: 0 }}>Nothing here yet. Add documents, links, recordings, or notes to ask questions about them.</p>
          <button type="button" className="button button-primary" onClick={onAdd}>Add sources</button>
        </div>
      ) : (
        <ul className="source-list">
          {sources.map((source) => {
            const status = statusText(source);
            return (
              <li key={source.id} className={`source-row${openSourceId === source.id ? " is-open" : ""}`}>
                <input type="checkbox" checked={included.has(source.id)} onChange={() => onToggle(source.id)}
                  aria-label={`Use ${source.title} in questions`} />
                <KindIcon kind={source.kind} />
                <span>
                  <button type="button" className="source-title" onClick={() => onOpen(source)}>
                    {source.title}
                  </button>
                  {status && (
                    <span className={`status${source.status === "failed" ? " status-failed" : ""}${isWorking(source) ? " status-working" : ""}`}>
                      {status}
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
