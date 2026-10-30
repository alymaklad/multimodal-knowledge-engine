import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { KIND_LABEL, formatDate, formatDuration, statusText } from "../format";
import { useToast } from "../toast";
import type { Citation, Source, StoredObject } from "../types";

export type Selection = { type: "citation"; citation: Citation; claim: string } | { type: "source"; source: Source };

const SENTENCE_END = /(?<=[.!?])\s+/;
const words = (text: string) => new Set(text.toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) ?? []);

/**
 * Split a passage into sentences and pick the one sharing the most words with the claim it supports.
 * Nothing is picked when no sentence shares at least two words: a guessed highlight would mislead.
 */
function highlightedSentences(passage: string, claim: string): { text: string; marked: boolean }[] {
  const sentences = passage.split(SENTENCE_END).filter((sentence) => sentence.trim());
  const claimWords = words(claim);
  let best = -1;
  let bestScore = 1;
  sentences.forEach((sentence, index) => {
    const score = [...words(sentence)].filter((word) => claimWords.has(word)).length;
    if (score > bestScore) {
      best = index;
      bestScore = score;
    }
  });
  return sentences.map((text, index) => ({ text, marked: index === best }));
}

interface Props {
  selection: Selection;
  sources: Source[];
  collectionId: number;
  onOpenSource: (source: Source) => void;
  onClose: () => void;
  onChanged: () => void;
}

/** What a citation quotes, or what a source is: shown beside the conversation, never replacing it. */
export function PassagePane({ selection, sources, collectionId, onOpenSource, onClose, onChanged }: Props) {
  return (
    <div className="passage">
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "0.5rem" }}>
        <button type="button" className="button button-quiet" onClick={onClose} aria-label="Close panel">Close</button>
      </div>
      {selection.type === "citation" ? (
        <CitationView citation={selection.citation} claim={selection.claim}
          source={sources.find((source) => source.id === selection.citation.source_id)} onOpenSource={onOpenSource} />
      ) : (
        <SourceView source={selection.source} collectionId={collectionId} onChanged={onChanged} onClose={onClose} />
      )}
    </div>
  );
}

function CitationView({ citation, claim, source, onOpenSource }: {
  citation: Citation;
  claim: string;
  source?: Source;
  onOpenSource: (source: Source) => void;
}) {
  return (
    <article aria-label={`Source ${citation.marker}`}>
      <p className="passage-source">{citation.title}</p>
      <p className="passage-where">{citation.citation ?? "Whole source"}</p>
      <p className="passage-text">
        {highlightedSentences(citation.snippet, claim).map((sentence, index) => (
          <span key={index}>
            {index > 0 && " "}
            {sentence.marked ? <mark>{sentence.text}</mark> : sentence.text}
          </span>
        ))}
      </p>
      <div className="actions">
        {source && (
          <button type="button" className="button" onClick={() => onOpenSource(source)}>About this source</button>
        )}
        {citation.origin_url && (
          <a className="button" href={citation.origin_url} target="_blank" rel="noreferrer noopener">Open the original page</a>
        )}
      </div>
    </article>
  );
}

function SourceView({ source, collectionId, onChanged, onClose }: { source: Source; collectionId: number; onChanged: () => void; onClose: () => void }) {
  const toast = useToast();
  const [original, setOriginal] = useState<StoredObject | null>(null);

  useEffect(() => {
    let cancelled = false;
    setOriginal(null);
    api
      .sourceMedia(source.id)
      .then((objects) => {
        if (!cancelled) setOriginal(objects.find((object) => object.kind === "original" || object.kind === "video") ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [source.id, source.status]);

  async function retry() {
    try {
      const job = await api.jobForSource(source.id);
      await api.retry(job.id);
      toast("Trying again.");
      onChanged();
    } catch (caught) {
      toast(caught instanceof ApiError ? caught.message : "It couldn't be retried.", "error");
    }
  }

  async function remove() {
    try {
      await api.removeFromCollection(collectionId, source.id);
      toast("Removed from this collection. It's still in your library.");
      onChanged();
      onClose();
    } catch (caught) {
      toast(caught instanceof ApiError ? caught.message : "It couldn't be removed.", "error");
    }
  }

  const status = statusText(source);
  const pages = typeof source.details.page_count === "number" ? source.details.page_count : null;
  const duration = formatDuration(source.duration);

  return (
    <article>
      <p className="passage-source">{source.title}</p>
      {status && <p className={`passage-where${source.status === "failed" ? " error-text" : ""}`}>{status}</p>}
      {source.summary && (
        <section aria-label="Summary" className="source-summary">
          <h3 className="small muted">Summary</h3>
          <p>{source.summary}</p>
        </section>
      )}
      <dl className="detail-list" style={{ marginTop: status ? 0 : "1rem" }}>
        <dt>Type</dt>
        <dd>{KIND_LABEL[source.kind]}</dd>
        {source.author && (<><dt>Author</dt><dd>{source.author}</dd></>)}
        {pages != null && (<><dt>Pages</dt><dd>{pages}</dd></>)}
        {duration && (<><dt>Length</dt><dd>{duration}</dd></>)}
        {source.original_filename && (<><dt>File</dt><dd>{source.original_filename}</dd></>)}
        <dt>Added</dt>
        <dd>{formatDate(source.created_at)}</dd>
      </dl>
      <div className="actions">
        {source.origin_url && (
          <a className="button" href={source.origin_url} target="_blank" rel="noreferrer noopener">Open the original page</a>
        )}
        {original && (
          <a className="button" href={original.url} target="_blank" rel="noreferrer noopener">Download the file</a>
        )}
        {(source.status === "failed" || source.status === "needs_review") && (
          <button type="button" className="button" onClick={retry}>Try again</button>
        )}
        <button type="button" className="button button-quiet" onClick={remove}>Remove from collection</button>
      </div>
    </article>
  );
}
