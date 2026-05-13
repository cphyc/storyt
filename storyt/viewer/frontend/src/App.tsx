import { useState, useEffect, Fragment } from "react";
import { Hierarchy, TreeNode, Instance } from "./types";
import { cachedFetch } from "./fetchCache";
import { AssetListView } from "./components/AssetListView";

interface NavFrame {
  label: string;
  urlPath: string | null;
  treeNode: TreeNode;
}

export function App() {
  const [hierarchy, setHierarchy] = useState<Hierarchy | null>(null);
  const [navStack, setNavStack] = useState<NavFrame[]>([]);

  useEffect(() => {
    cachedFetch("data/hierarchy.json").then((data) => {
      const h = data as Hierarchy;
      if (h && h.tree) {
        setHierarchy(h);
        setNavStack([{ label: "Home", urlPath: null, treeNode: h.tree }]);
      }
    });
  }, []);

  const currentFrame = navStack.length ? navStack[navStack.length - 1] : null;

  const onNavigate = ({
    instance,
    treeNode,
  }: {
    instance: Instance;
    treeNode: TreeNode;
  }) => {
    const label = instance.url_path
      ? instance.url_path.split("/").pop() || instance.url_path
      : `id=${instance.id}`;
    setNavStack((prev) => [
      ...prev,
      { label, urlPath: instance.url_path, treeNode },
    ]);
  };

  const navigateTo = (index: number) => {
    setNavStack((prev) => prev.slice(0, index + 1));
  };

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
          onNavigate={onNavigate}
        />
      )}
    </>
  );
}
