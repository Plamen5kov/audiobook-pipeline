import { audioUrl } from '../api';

interface Props {
  filename: string;
  version?: number;
}

export function AudioPlayer({ filename, version }: Props) {
  const base = audioUrl(filename);
  const src = version ? `${base}?v=${version}` : base;

  const handleDownload = async () => {
    const res = await fetch(src);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div className="card">
      <h2>Your Audiobook</h2>
      <audio key={src} className="audio-player" controls preload="metadata" src={src} />
      <button className="download-btn" onClick={handleDownload}>
        ↓ Download WAV
      </button>
    </div>
  );
}
