import { useState, useEffect, useRef } from "react";
import { Binding, Instance, TreeNode } from "../types";
import { cachedFetch } from "../fetchCache";
import { InstanceCard } from "./InstanceCard";

/** From the set of child types at this level, return only the "primary" ones.
 *  When multiple children share a binding (i.e. they are already embedded as
 *  siblings in each other's listing files), keep only the first one so we
 *  don't show duplicate iout=XXXXX cards for every bound child type. */
function primaryChildren(
  children: TreeNode[],
  bindings: Binding[],
): TreeNode[] {
  const childIds = new Set(children.map((c) => c.id));
  const skip = new Set<number>();
  for (const binding of bindings) {
    const bound = binding.members
      .map((m) => m.asset_id)
      .filter((id) => childIds.has(id));
    if (bound.length > 1) {
      // Keep the first in document order; mark the rest to be skipped.
      bound.slice(1).forEach((id) => skip.add(id));
    }
  }
  return children.filter((c) => !skip.has(c.id));
}

interface Props {
  urlPath: string | null;
  treeNode: TreeNode | null;
  bindings: Binding[];
  onNavigate: (payload: { instance: Instance; treeNode: TreeNode }) => void;
}

export function AssetListView({
  urlPath,
  treeNode,
  bindings,
  onNavigate,
}: Props) {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [loading, setLoading] = useState(true);
  const seqRef = useRef(0);

  useEffect(() => {
    const childTypes = primaryChildren(
      treeNode ? treeNode.children || [] : [],
      bindings,
    );
    // Build a name→treeNode map for ALL children (including non-primary / siblings)
    const childTypeByName = Object.fromEntries(
      (treeNode?.children ?? []).map((c) => [c.name, c]),
    );
    const seq = ++seqRef.current;
    let cancelled = false;
    (async () => {
      const all: Instance[] = [];
      for (const childType of childTypes) {
        const url = urlPath
          ? `data/${urlPath}/${childType.name}.json`
          : `data/${childType.name}.json`;
        const data = (await cachedFetch(url)) as Instance[];
        if (cancelled || seq !== seqRef.current) return;
        for (const inst of data || []) {
          // Annotate each sibling with its treeNode so InstanceCard can nav into it
          const enrichedSiblings = Object.fromEntries(
            Object.entries(inst.siblings ?? {}).map(([sibName, sib]) => [
              sibName,
              { ...sib, _treeNode: childTypeByName[sibName] },
            ]),
          );
          all.push({
            ...inst,
            siblings: enrichedSiblings,
            _childType: childType,
          });
        }
      }
      if (cancelled || seq !== seqRef.current) return;
      setInstances(all);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [urlPath, treeNode, bindings]);

  const onCardNavigate = (instance: Instance, overrideTreeNode?: TreeNode) => {
    onNavigate({
      instance,
      treeNode: overrideTreeNode ?? instance._childType!,
    });
  };

  return (
    <div>
      {loading && <span className="loading">Loading…</span>}
      {instances.map((inst) => (
        <InstanceCard
          key={inst.id}
          instance={inst}
          childrenTypes={inst._childType ? inst._childType.children : []}
          parentUrlPath={urlPath}
          onNavigate={onCardNavigate}
        />
      ))}
    </div>
  );
}
