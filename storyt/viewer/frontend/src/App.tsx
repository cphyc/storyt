import { useState, useEffect, useCallback, Fragment } from "react";
import { Hierarchy, TreeNode, Instance } from "./types";
import { cachedFetch } from "./fetchCache";
import { AssetListView } from "./components/AssetListView";

interface NavFrame {
  label: string;
  urlPath: string | null;
  treeNode: TreeNode;
}

/** Encode the nav stack as a URL hash, e.g. #sim_a/output_00001 */
function stackToHash(stack: NavFrame[]): string {
  // Skip the root "Home" frame (index 0); encode subsequent url_paths
  const parts = stack.slice(1).map((f) => f.urlPath ?? `id=${f.treeNode.id}`);
  return parts.length ? "#" + parts.join("|") : "";
}

/** Attempt to restore a nav stack from the URL hash + the hierarchy tree.
 *  Returns null if the hash can't be resolved (fall back to root). */
function hashToStack(hash: string, rootFrame: NavFrame): NavFrame[] | null {
  const raw = hash.replace(/^#/, "");
  if (!raw) return null;
  const parts = raw.split("|");
  const stack: NavFrame[] = [rootFrame];
  let node = rootFrame.treeNode;
  for (const part of parts) {
    if (part.startsWith("id=")) return null; // can't restore dynamic-only frames
    // Find the child tree node whose pattern matches the end of the url_path,
    // falling back to the first child when no pattern is defined (e.g. root level).
    const childNode =
      node.children.find((c) => {
        if (!c.pattern) return false;
        try {
          // Convert Python-style named groups (?P<name>) to JS (?<name>)
          const jsPattern = c.pattern.replace(/\(\?P</g, "(?<");
          return new RegExp(jsPattern + "$").test(part);
        } catch {
          return false;
        }
      }) ?? node.children[0];
    if (!childNode) return null;
    const label = part.split("/").pop() || part;
    stack.push({ label, urlPath: part, treeNode: childNode });
    node = childNode;
  }
  return stack;
}

export function App() {
  const [hierarchy, setHierarchy] = useState<Hierarchy | null>(null);
  const [navStack, setNavStack] = useState<NavFrame[]>([]);

  // Load hierarchy and restore state from URL hash on mount
  useEffect(() => {
    cachedFetch("data/hierarchy.json").then((data) => {
      const h = data as Hierarchy;
      if (!h?.tree) return;
      setHierarchy(h);
      const rootFrame: NavFrame = {
        label: "Home",
        urlPath: null,
        treeNode: h.tree,
      };
      const restored = hashToStack(window.location.hash, rootFrame);
      setNavStack(restored ?? [rootFrame]);
    });
  }, []);

  // Keep URL hash in sync with navStack
  useEffect(() => {
    if (!navStack.length) return;
    const hash = stackToHash(navStack);
    // Replace so back-button still works naturally
    window.history.replaceState(null, "", hash || window.location.pathname);
  }, [navStack]);

  // Handle browser back/forward
  useEffect(() => {
    const onPop = () => {
      if (!hierarchy) return;
      const rootFrame: NavFrame = {
        label: "Home",
        urlPath: null,
        treeNode: hierarchy.tree,
      };
      const restored = hashToStack(window.location.hash, rootFrame);
      setNavStack(restored ?? [rootFrame]);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [hierarchy]);

  const currentFrame = navStack.length ? navStack[navStack.length - 1] : null;

  const onNavigate = useCallback(
    ({ instance, treeNode }: { instance: Instance; treeNode: TreeNode }) => {
      const label = instance.url_path
        ? instance.url_path.split("/").pop() || instance.url_path
        : `id=${instance.id}`;
      setNavStack((prev) => {
        const next = [...prev, { label, urlPath: instance.url_path, treeNode }];
        // Use pushState so the back button creates a real history entry
        window.history.pushState(null, "", stackToHash(next));
        return next;
      });
    },
    [],
  );

  const navigateTo = useCallback((index: number) => {
    setNavStack((prev) => {
      const next = prev.slice(0, index + 1);
      window.history.pushState(null, "", stackToHash(next));
      return next;
    });
  }, []);

  return (
    <>
      <nav data-testid="breadcrumb" className="breadcrumb">
        {navStack.map((frame, i) => (
          <Fragment key={i}>
            <span className="label" onClick={() => navigateTo(i)}>
              {frame.label}
            </span>
            {i < navStack.length - 1 && <span className="sep"> / </span>}
          </Fragment>
        ))}
      </nav>
      {hierarchy && currentFrame && (
        <AssetListView
          urlPath={currentFrame.urlPath}
          treeNode={currentFrame.treeNode}
          bindings={hierarchy.bindings}
          onNavigate={onNavigate}
        />
      )}
    </>
  );
}
