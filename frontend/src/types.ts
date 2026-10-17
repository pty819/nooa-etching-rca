export type Evt = {
  id: number;
  task_id: string;
  agent_id?: string;
  parent_agent_id?: string;
  type: string;
  data: Record<string, any>;
  time: number;
};
export type Session = {
  id: string;
  title: string;
  active: boolean;
  task_id: string | null;
  targets: number;
  controls: number;
  updated: number;
};
export type Msg = {
  id: string;
  role: string;
  markdown: string;
  provisional?: boolean;
  legacy?: boolean;
  time?: number;
  agent_id?: string;
  publication?: string;
  review_status?: string;
};
export type ModelCall = {
  id: string;
  agent_id?: string;
  thinking: string;
  content: string;
  ended: boolean;
};
export type Tool = {
  id: string;
  name: string;
  agent_id?: string;
  code: string;
  output: string;
  state: "running" | "ok" | "error";
  elapsed?: number;
};
export type Artifact = {
  path: string;
  size: number;
  mime: string;
  agent_id?: string;
};
export type Branch = {
  id: string;
  parent_id: string;
  mission: string;
  title: string;
  task: string;
  state: string;
  review_status?: string;
  error?: string;
};
