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

export interface FormulaStudy {
  id: string;
  owner: string;
  revision: number;
  selection_id: string | null;
  limits: string;
  plan: {
    title: string; labels: string[]; metric: string; folds: number;
    input_sha256: string; train: string[]; dev: string[];
    model_params: Record<string, Record<string, unknown>>;
  };
  formulas: {ref: string; expression: string; description: string; change_reason?: string; sensors: string[]; steps: number[]; unit: string; params: Record<string, unknown>; sha256: string}[];
  sets: {id: string; formulas: string[]; excluded: null | {reason: string}}[];
  trials: {id: string; set_id: string; formulas: string[]; model: string; status: string; dev_score?: number; cv_mean?: number; cv_std?: number; error?: string}[];
  replays?: {id: string; status: string; error?: string}[];
  selections: {id: string; winner: string; ranking: string[]; replay: {status: string}}[];
}
