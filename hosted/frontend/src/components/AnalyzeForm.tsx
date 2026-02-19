import { useState } from 'react';

interface Props {
  onAnalyze: (title: string, text: string) => void;
  disabled: boolean;
  error: string;
}

export function AnalyzeForm({ onAnalyze, disabled, error }: Props) {
  const [title, setTitle] = useState('Chapter 1: The Awakening');
  const [text, setText]   = useState('');

  function handleSubmit() {
    if (!text.trim()) { alert('Please paste chapter text first.'); return; }
    onAnalyze(title.trim(), text.trim());
  }

  return (
    <div className="card">
      <h2>Chapter Input</h2>
      <div className="field">
        <label htmlFor="title">Chapter Title</label>
        <input
          id="title"
          type="text"
          value={title}
          onChange={e => setTitle(e.target.value)}
          placeholder="e.g. Chapter 1: The Awakening"
          disabled={disabled}
        />
      </div>
      <div className="field">
        <label htmlFor="text">Chapter Text</label>
        <textarea
          id="text"
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="Paste your chapter text here…"
          disabled={disabled}
          rows={8}
        />
      </div>
      <div className="field">
        <button className="btn-primary" onClick={handleSubmit} disabled={disabled}>
          {disabled ? 'Processing…' : 'Analyze Text'}
        </button>
      </div>
      {error && <p className="error-msg">{error}</p>}
    </div>
  );
}
