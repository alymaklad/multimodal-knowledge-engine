import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { KindIcon } from "../KindIcon";
import { KIND_LABEL, formatDate, isWorking, plural, statusText } from "../format";
import { useToast } from "../toast";
import type { Collection, Source } from "../types";
import { AddSourceDialog } from "./AddSourceDialog";

interface Props {
  collections: Collection[];
  onCollectionsChanged: () => void;
}

/** Everything you have added, and the collections built from it. */
export function LibraryView({ collections, onCollectionsChanged }: Props) {
  const toast = useToast();
  const [sources, setSources] = useState<Source[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");

  const load = useCallback(async () => {
    try {
      setSources(await api.sources());
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "The library couldn't be loaded.", "error");
      setSources([]);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const working = sources?.some(isWorking) ?? false;
  useEffect(() => {
    if (!working) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [working, load]);

  async function createCollection(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    try {
      const created = await api.createCollection(name.trim());
      setName("");
      onCollectionsChanged();
      window.location.hash = `#/collections/${created.id}`;
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "The collection couldn't be created.", "error");
    }
  }

  async function addTo(source: Source, collectionId: number) {
    try {
      const { added } = await api.addToCollection(collectionId, [source.id]);
      const target = collections.find((collection) => collection.id === collectionId)?.name ?? "the collection";
      toast(added.length ? `Added to ${target}.` : `It's already in ${target}.`);
      onCollectionsChanged();
    } catch (caught) {
      toast(caught instanceof ApiError ? caught.message : "It couldn't be added.", "error");
    }
  }

  async function remove(source: Source) {
    if (!window.confirm(`Delete “${source.title}” from your library? It will be removed from every collection.`)) return;
    try {
      await api.deleteSource(source.id);
      toast("Deleted.");
      void load();
      onCollectionsChanged();
    } catch (caught) {
      toast(caught instanceof ApiError ? caught.message : "It couldn't be deleted.", "error");
    }
  }

  return (
    <main className="library">
      <div className="library-inner">
        <div className="library-head">
          <h1>Library</h1>
          <button type="button" className="button button-primary" onClick={() => setAdding(true)}>Add sources</button>
        </div>

        <h2 className="section-title">Collections</h2>
        <div className="collections-list">
          {collections.map((collection) => (
            <a key={collection.id} className="collection-link" href={`#/collections/${collection.id}`}>
              <strong>{collection.name}</strong>
              <span className="muted small">{plural(collection.source_count, "source")}</span>
            </a>
          ))}
          <form onSubmit={createCollection} className="stack" style={{ minWidth: "14rem", alignContent: "start" }}>
            <label className="visually-hidden" htmlFor="new-collection">New collection name</label>
            <input id="new-collection" className="field" placeholder="New collection name" value={name}
              onChange={(event) => setName(event.target.value)} />
            <button type="submit" className="button" disabled={!name.trim()}>Create collection</button>
          </form>
        </div>

        <h2 className="section-title">All sources</h2>
        {sources === null ? (
          <p className="muted">Loading…</p>
        ) : sources.length === 0 ? (
          <p className="muted">Your library is empty. Add files, links, or notes, then group them into collections.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th><span className="visually-hidden">Type</span></th>
                <th>Title</th>
                <th className="hide-narrow">Added</th>
                <th>Add to</th>
                <th><span className="visually-hidden">Delete</span></th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => {
                const status = statusText(source);
                return (
                  <tr key={source.id}>
                    <td><KindIcon kind={source.kind} /></td>
                    <td>
                      <span style={{ overflowWrap: "anywhere" }}>{source.title}</span>
                      <span className={`status${source.status === "failed" ? " status-failed" : ""}${isWorking(source) ? " status-working" : ""}`}>
                        {status ?? KIND_LABEL[source.kind]}
                      </span>
                    </td>
                    <td className="hide-narrow muted small">{formatDate(source.created_at)}</td>
                    <td>
                      <label className="visually-hidden" htmlFor={`add-${source.id}`}>Add {source.title} to a collection</label>
                      <select id={`add-${source.id}`} className="field" style={{ padding: "0.3rem 0.5rem", width: "auto", minWidth: "9rem" }}
                        value="" disabled={collections.length === 0}
                        onChange={(event) => event.target.value && void addTo(source, Number(event.target.value))}>
                        <option value="">{collections.length ? "Choose…" : "No collections"}</option>
                        {collections.map((collection) => (
                          <option key={collection.id} value={collection.id}>{collection.name}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <button type="button" className="button button-quiet" onClick={() => void remove(source)}
                        aria-label={`Delete ${source.title}`}>
                        Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <AddSourceDialog open={adding} onClose={() => setAdding(false)} onAdded={() => { void load(); onCollectionsChanged(); }} />
    </main>
  );
}
