import { audioUrl } from '../api';

interface Props {
  filename: string;
}

export function AudioPlayer({ filename }: Props) {
  const src = audioUrl(filename);
  return (
    <div className="card">
      <h2>Your Audiobook</h2>
      <audio className="audio-player" controls preload="metadata" src={src} />
      <a className="download-btn" href={src} download={filename}>
        ↓ Download WAV
      </a>
    </div>
  );
}
