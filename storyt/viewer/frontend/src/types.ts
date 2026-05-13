export interface TreeNode {
  id: number;
  name: string;
  pattern: string | null;
  is_dynamic: boolean;
  children: TreeNode[];
}

export interface BindingMember {
  asset_id: number;
  key_name: string;
}

export interface Binding {
  id: number;
  members: BindingMember[];
}

export interface Hierarchy {
  tree: TreeNode;
  bindings: Binding[];
}

export interface SiblingEntry {
  id: number | null;
  keys: Record<string, string>;
  path: string | null;
  url_path: string | null;
  properties: string[];
}

export interface Instance {
  id: number;
  keys: Record<string, string>;
  path: string | null;
  url_path: string | null;
  siblings?: Record<string, SiblingEntry>;
  properties?: string[];
  _childType?: TreeNode;
}

export interface PropertyRow {
  sibling: string;
  property: string;
  value: unknown;
}
