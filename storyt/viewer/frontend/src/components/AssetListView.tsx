import { useState, useEffect, useRef } from "react";
import { Instance, TreeNode } from "../types";
import { cachedFetch } from "../fetchCache";
import { InstanceCard } from "./InstanceCard";

interface Props {
  urlPath: string | null;
  treeNode: TreeNode | null;
  onNavigate: (payload: { instance: Instance; treeNode: TreeNode }) => void;
}

export function AssetListView({ urlPath, treeNode, onNavigate }: Props) {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [loading, setLoading] = useState(false);
  const seqRef = useRef(0);

  useEffect(() => {
    const childTypes = treeNode ? treeNode.children || [] : [];
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
          all.push({ ...inst, _childType: childType });
        }
      }
      if (cancelled || seq !== seqRef.current) return;
      setInstances(all);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [urlPath, treeNode]);

  const onCardNavigate = (instance: Instance) => {
    onNavigate({ instance, treeNode: instance._childType! });
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
