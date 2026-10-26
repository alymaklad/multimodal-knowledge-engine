import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import { LibraryView } from "./components/LibraryView";
import { Workspace } from "./components/Workspace";
import { useToast } from "./toast";
import type { Collection } from "./types";

type Route = { name: "library" } | { name: "collection"; id: number } | { name: "home" };

function parseRoute(hash: string): Route {
  if (hash.startsWith("#/library")) return { name: "library" };
  const match = /^#\/collections\/(\d+)/.exec(hash);
  return match ? { name: "collection", id: Number(match[1]) } : { name: "home" };
}

export function App() {
  const toast = useToast();
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [collections, setCollections] = useState<Collection[] | null>(null);

  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadCollections = useCallback(async () => {
    try {
      setCollections(await api.collections());
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "Collections couldn't be loaded.", "error");
      setCollections((current) => current ?? []);
    }
  }, [toast]);

  useEffect(() => {
    void loadCollections();
  }, [loadCollections]);

  // The home route opens the most recent collection, so returning picks up where you left off.
  useEffect(() => {
    if (route.name === "home" && collections && collections.length) {
      window.location.replace(`#/collections/${collections[0].id}`);
    }
  }, [route, collections]);

  const current = route.name === "collection" ? collections?.find((collection) => collection.id === route.id) : undefined;

  return (
    <div className="shell">
      <header className="topbar">
        <a className="wordmark" href="#/">Knowledge Engine</a>
        <nav className="topnav" aria-label="Main">
          {collections && collections.length > 0 && (
            <>
              <label className="visually-hidden" htmlFor="collection-picker">Collection</label>
              <select id="collection-picker" className="notebook-picker" value={current?.id ?? ""}
                onChange={(event) => { if (event.target.value) window.location.hash = `#/collections/${event.target.value}`; }}>
                {!current && <option value="">Open a collection</option>}
                {collections.map((collection) => (
                  <option key={collection.id} value={collection.id}>{collection.name}</option>
                ))}
              </select>
            </>
          )}
          <a href="#/library" aria-current={route.name === "library" ? "page" : undefined}>Library</a>
        </nav>
      </header>

      {collections === null ? (
        <main className="library"><p className="muted">Loading…</p></main>
      ) : route.name === "library" ? (
        <LibraryView collections={collections} onCollectionsChanged={loadCollections} />
      ) : current ? (
        <Workspace key={current.id} collection={current} onCollectionsChanged={loadCollections} />
      ) : collections.length === 0 ? (
        <FirstCollection onCreated={loadCollections} />
      ) : route.name === "collection" ? (
        <main className="library">
          <div className="empty">
            <h2>That collection doesn't exist</h2>
            <p>It may have been deleted. Choose another from the menu above, or open your library.</p>
            <a className="button" href="#/library">Open the library</a>
          </div>
        </main>
      ) : null}
    </div>
  );
}

function FirstCollection({ onCreated }: { onCreated: () => Promise<void> }) {
  const toast = useToast();
  const [name, setName] = useState("");

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      const created = await api.createCollection(name.trim());
      await onCreated();
      window.location.hash = `#/collections/${created.id}`;
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "The collection couldn't be created.", "error");
    }
  }

  return (
    <main className="library">
      <div className="empty">
        <h2>Make your first collection</h2>
        <p>A collection groups the sources you want to ask about together: a research topic, a course, a project.</p>
        <form onSubmit={create} className="stack">
          <label htmlFor="first-collection" className="visually-hidden">Collection name</label>
          <input id="first-collection" className="field" placeholder="For example, Retrieval research" value={name}
            onChange={(event) => setName(event.target.value)} autoFocus />
          <div>
            <button type="submit" className="button button-primary" disabled={!name.trim()}>Create collection</button>
          </div>
        </form>
      </div>
    </main>
  );
}
