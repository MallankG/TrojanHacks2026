import type { AccountKey, EdgeKey, GraphEdge, GraphNode, NodeKey } from "@/lib/types";

/** Canvas coordinates use a 980 x 420 view box. */
export const GRAPH_VIEWBOX = { width: 980, height: 420 } as const;

export const SEED_ACCOUNT: AccountKey = "B";

export const NODES: Record<NodeKey, GraphNode> = {
  A: { key: "A", id: "ACC-1104", kind: "Business operating account", shape: "square", x: 80, y: 230, role: "Source" },
  B: { key: "B", id: "ACC-2208", kind: "Personal checking", shape: "circle", x: 250, y: 130, role: "Seed (alerted account)" },
  C: { key: "C", id: "ACC-3315", kind: "Personal checking", shape: "circle", x: 420, y: 230, role: "Intermediary" },
  D: { key: "D", id: "ACC-4471", kind: "Prepaid wallet", shape: "diamond", x: 590, y: 130, role: "Intermediary" },
  E: { key: "E", id: "ACC-5590", kind: "Business account, new beneficiary", shape: "square", x: 760, y: 230, role: "Intermediary" },
  F: { key: "F", id: "EXT-CB-77", kind: "Correspondent bank (synthetic metadata)", shape: "open", x: 905, y: 130, role: "Exit, cross-border wire" },
  G: { key: "G", id: "ACC-3391", kind: "Personal checking", shape: "circle", x: 400, y: 370, role: "Shares a device with ACC-2208" },
  T: { key: "T", id: "DV-77", kind: "Device token", shape: "hex", x: 250, y: 330, role: "Shared device token" },
};

export const EDGES: GraphEdge[] = [
  { key: "e1", tx: "TX-88201", from: "A", to: "B", rail: "ACH", amount: 58200, initiated: "Oct 6, 09:14", settled: "Oct 6, 09:40", order: 0, hop: -1, batch: "ach_2026_10_06", row: 1187, gap: "Start of path" },
  { key: "e2", tx: "TX-88214", from: "B", to: "C", rail: "P2P", amount: 48100, initiated: "Oct 6, 11:02", settled: "Oct 6, 11:02", order: 1, hop: 1, batch: "p2p_2026_10_06", row: 2290, gap: "1 h 48 m after previous hop" },
  { key: "e7", tx: "TX-88222", from: "B", to: "G", rail: "P2P", amount: 9600, initiated: "Oct 6, 12:10", settled: "Oct 6, 12:10", order: 2, hop: 1, batch: "p2p_2026_10_06", row: 2311, gap: "1 h 8 m after TX-88214", side: true },
  { key: "e3", tx: "TX-88237", from: "C", to: "D", rail: "ACH", amount: 47650, initiated: "Oct 6, 15:31", settled: "Oct 6, 16:05", order: 3, hop: 2, batch: "ach_2026_10_06_b", row: 1042, gap: "4 h 29 m after previous hop", held: true },
  { key: "e4", tx: "TX-88259", from: "D", to: "E", rail: "Wire", amount: 47100, initiated: "Oct 7, 08:05", settled: "Oct 7, 08:31", order: 4, hop: 3, batch: "wire_2026_10_07", row: 311, gap: "16 h 34 m after previous hop" },
  { key: "e5", tx: "TX-88266", from: "E", to: "F", rail: "Wire", amount: 46800, initiated: "Oct 7, 10:20", settled: "Oct 7, 11:02", order: 5, hop: 4, batch: "wire_2026_10_07", row: 344, gap: "2 h 15 m after previous hop", crossBorder: true },
  { key: "e6", tx: "TX-88290", from: "F", to: "A", rail: "Wire", amount: 12000, initiated: "Oct 9, 12:47", settled: "Oct 9, 13:20", order: 6, hop: 5, batch: "wire_2026_10_09", row: 58, gap: "2 d 2 h 27 m after previous hop", cycle: true },
];

/** Edges that sit downstream of the quarantined hop and cannot be reached until it loads. */
export const UNREACHABLE_UNTIL_FIX: EdgeKey[] = ["e3", "e4", "e5", "e6"];

export const MAIN_PATH: EdgeKey[] = ["e1", "e2", "e3", "e4", "e5"];

export const TIMELINE = ["Oct 6 09:14", "Oct 6 11:02", "Oct 6 12:10", "Oct 6 15:31", "Oct 7 08:05", "Oct 7 10:20", "Oct 9 12:47"] as const;

export const TIMELINE_MAX = TIMELINE.length - 1;

export function edgeByKey(key: EdgeKey): GraphEdge {
  const edge = EDGES.find((e) => e.key === key);
  if (!edge) throw new Error(`Unknown edge ${key}`);
  return edge;
}

export function edgeByTx(tx: string): GraphEdge | undefined {
  return EDGES.find((e) => e.tx === tx);
}

export function nodeKeyForAccount(accountId: string): AccountKey | undefined {
  const entry = (Object.values(NODES) as GraphNode[]).find((n) => n.id === accountId && n.key !== "T");
  return entry?.key as AccountKey | undefined;
}
