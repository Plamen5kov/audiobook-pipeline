import { audioUrl } from '../api';

interface Props {
  filename: string;
}

export function AudioPlayer({ filename }: Props) {
  const src = audioUrl(filename);

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
      <audio className="audio-player" controls preload="metadata" src={src} />
      <button className="download-btn" onClick={handleDownload}>
        ↓ Download WAV
      </button>
    </div>
  );
}
