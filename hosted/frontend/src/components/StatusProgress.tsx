export type PhaseState = {
  state: 'pending' | 'running' | 'done' | 'error';
  detail: string;
};

interface Props {
  analyzing:    PhaseState;
  synthesizing: PhaseState;
  assembling:   PhaseState;
}

function PhaseIcon({ state, num }: { state: PhaseState['state']; num: number }) {
  return (
    <div className={`phase-icon ${state}`}>
      {state === 'running' ? <span className="spinner" /> :
       state === 'done'    ? '✓' :
       state === 'error'   ? '✗' :
       num}
    </div>
  );
}

export function StatusProgress({ analyzing, synthesizing, assembling }: Props) {
  return (
    <div className="card">
      <h2>Progress</h2>
      <ul className="phase-list">
        <li className="phase-item">
          <PhaseIcon state={analyzing.state} num={1} />
          <div className="phase-label">
            <div className="name">Analyzing text</div>
            <div className="detail">{analyzing.detail}</div>
          </div>
        </li>
        <li className="phase-item">
          <PhaseIcon state={synthesizing.state} num={2} />
          <div className="phase-label">
            <div className="name">Synthesizing audio</div>
            <div className="detail">{synthesizing.detail}</div>
          </div>
        </li>
        <li className="phase-item">
          <PhaseIcon state={assembling.state} num={3} />
          <div className="phase-label">
            <div className="name">Assembling final file</div>
            <div className="detail">{assembling.detail}</div>
          </div>
        </li>
      </ul>
    </div>
  );
}
