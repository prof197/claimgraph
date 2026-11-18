import { useEffect, useSyncExternalStore } from "react";
import { legacyRoute, href, parseRoute, type Route } from "../routes";

/** How the reader reached the current address: opened it, followed a link, or went back or
 * forward, which returns them to where they were on that page. */
export type Arrival = "load" | "link" | "history";

export interface Navigation {
  /** Changes with every move through history, even back to the same address. */
  id: number;
  route: Route;
  arrival: Arrival;
  /** Where the page was scrolled when the reader left this history entry, if known. */
  scroll: number | null;
}

interface Entry {
  id: number;
  url: string;
  arrival: Arrival;
  scroll: number | null;
}

const listeners = new Set<() => void>();
let entry: Entry | null = null;
let moves = 0;

function current(): Entry {
  // An address changed outside this module, as when the page opens, reads as a fresh load.
  if (entry?.url !== window.location.href) {
    entry = { id: ++moves, url: window.location.href, arrival: "load", scroll: null };
  }
  return entry;
}

function settle(next: Omit<Entry, "id">) {
  entry = { ...next, id: ++moves };
  for (const listener of listeners) listener();
}

function savedScroll(): number | null {
  const state: unknown = window.history.state;
  if (typeof state === "object" && state !== null && "scroll" in state && typeof state.scroll === "number") {
    return state.scroll;
  }
  return null;
}

function saveScroll() {
  window.history.replaceState({ scroll: window.scrollY }, "");
}

/** Rewrites an address from the hash routes earlier versions used, before the first render. */
export function upgradeLegacyAddress() {
  const route = legacyRoute(window.location.hash);
  if (route !== null) {
    window.history.replaceState(null, "", href(route));
  }
}

/** Follows an in-site link without reloading the page. */
export function navigate(url: string) {
  saveScroll();
  window.history.pushState(null, "", url);
  settle({ url: window.location.href, arrival: "link", scroll: null });
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  const onPop = () => settle({ url: window.location.href, arrival: "history", scroll: savedScroll() });
  window.addEventListener("popstate", onPop);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("popstate", onPop);
  };
}

export function useNavigation(): Navigation {
  const { id, url, arrival, scroll } = useSyncExternalStore(subscribe, current);
  const { pathname, hash } = new URL(url);
  return { id, route: parseRoute(pathname, hash), arrival, scroll };
}

/** A same-site link the page can follow itself: a plain left click on a link under the base
 * path that leads to another address. A link to a fragment of this address stays native. */
function followable(event: MouseEvent): string | null {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  const link = event.target instanceof Element ? event.target.closest("a") : null;
  if (link === null || link.hasAttribute("download") || (link.target !== "" && link.target !== "_self")) {
    return null;
  }
  const url = new URL(link.href, window.location.href);
  const here = new URL(window.location.href);
  if (url.origin !== here.origin || !url.pathname.startsWith(import.meta.env.BASE_URL)) return null;
  if (url.pathname === here.pathname && url.search === here.search && url.hash !== "") return null;
  return url.href;
}

/** Follows in-site links without a reload, and keeps each history entry's scroll position so
 * going back returns the reader to where they were. */
export function useLinkFollowing() {
  useEffect(() => {
    window.history.scrollRestoration = "manual";
    let timer: number | undefined;
    // Saved shortly after scrolling stops: browsers limit how often history state can change.
    const onScroll = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(saveScroll, 150);
    };
    const onClick = (event: MouseEvent) => {
      const url = followable(event);
      if (url === null) return;
      event.preventDefault();
      navigate(url);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("click", onClick);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("click", onClick);
    };
  }, []);
}
