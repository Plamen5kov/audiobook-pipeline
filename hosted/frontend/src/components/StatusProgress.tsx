import { PhaseState } from '../types/pipeline';
import './StatusProgress.css';

// Re-export for backward compatibility
export type { PhaseState };

interface Props {
  analyzing:    PhaseState;
  synthesizing: PhaseState;
  assembling:   PhaseState;
}

function PhaseIcon({ state, num }: { state: PhaseState['state']; num: number }) {
  return (
    <div className={`phase-icon ${state}`}>
      {state === 'running' ? <span className="spinner" /> :
       state === 'done'    ? '\u2713' :
       state === 'error'   ? '\u2717' :
       num}
    </div>
  );
}

const PHASE_LABELS: { key: keyof Props; label: string }[] = [
  { key: 'analyzing',    label: 'Analyzing text' },
  { key: 'synthesizing', label: 'Synthesizing audio' },
  { key: 'assembling',   label: 'Assembling final file' },
];

export function StatusProgress(props: Props) {
  return (
    <div className="card">
      <h2>Progress</h2>
      <ul className="phase-list">
        {PHASE_LABELS.map(({ key, label }, i) => (
          <li key={key} className="phase-item">
            <PhaseIcon state={props[key].state} num={i + 1} />
            <div className="phase-label">
              <div className="name">{label}</div>
              <div className="detail">{props[key].detail}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
