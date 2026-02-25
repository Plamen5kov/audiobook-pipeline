import { useState, useEffect } from 'react';
import { NodeStatus } from '../api';

const NODE_ORDER = ['text-analyzer', 'script-adapter', 'tts-router', 'audio-assembly'] as const;

const NODE_LABELS: Record<string, string> = {
  'text-analyzer':  'text-analyzer',
  'script-adapter': 'script-adapter',
  'tts-router':     'tts-router',
  'audio-assembly': 'audio-assembly',
};

function NodeBlock({ name, node, now }: { name: string; node: NodeStatus | undefined; now: number }) {
  const st = node?.status ?? 'pending';

  let stateClass = 'node-state-pending';
  let indicator: string;

  if (st === 'running') {
    stateClass = 'node-state-running';
    const elapsed = node?.started ? `${now - node.started}s` : '';
    if (name === 'tts-router' && node?.completed !== undefined && node?.total) {
      indicator = elapsed ? `${node.completed}/${node.total} ${elapsed}` : `${node.completed}/${node.total}`;
    } else {
      indicator = elapsed ? `⟳ ${elapsed}` : '⟳';
    }
  } else if (st === 'done') {
    stateClass = 'node-state-done';
    const d = node?.started && node?.finished ? `${node.finished - node.started}s` : '';
    indicator = d ? `✓ ${d}` : '✓';
  } else if (st === 'error') {
    stateClass = 'node-state-error';
    indicator = '✗';
  } else {
    indicator = '○';
  }

  return (
    <div className={`pipeline-node ${stateClass}`}>
      <span className="pipeline-node-name">{NODE_LABELS[name] ?? name}</span>
      <span className="pipeline-node-indicator">{indicator}</span>
    </div>
  );
}

interface Props {
  nodes: Record<string, NodeStatus> | undefined;
  jobId?: string;
}

export function PipelineMap({ nodes, jobId }: Props) {
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));

  useEffect(() => {
    const id = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  if (!nodes) return null;

  return (
    <div className="pipeline-map">
      {jobId && <span className="pipeline-job-id">Job: {jobId.slice(0, 8)}…</span>}
      <div className="pipeline-row">
        {NODE_ORDER.map((name, i) => (
          <span key={name} className="pipeline-item">
            <NodeBlock name={name} node={nodes[name]} now={now} />
            {i < NODE_ORDER.length - 1 && <span className="pipeline-arrow">→</span>}
          </span>
        ))}
      </div>
    </div>
  );
}
