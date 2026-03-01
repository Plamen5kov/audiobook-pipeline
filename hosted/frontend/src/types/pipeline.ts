export type PhaseState = {
  state: 'pending' | 'running' | 'done' | 'error';
  detail: string;
};
