import { useState } from 'react';
import { usePipeline } from './hooks/usePipeline';
import { AnalyzeForm } from './components/AnalyzeForm';
import { StatusProgress } from './components/StatusProgress';
import { VoiceCast } from './components/VoiceCast';
import { AudioPlayer } from './components/AudioPlayer';
import { PostProduction } from './components/PostProduction';
import { ServiceHealth } from './components/ServiceHealth';
import { PipelineMap } from './components/PipelineMap';
import { VoiceManager } from './components/VoiceManager';
import { WorkspaceBrowser } from './components/WorkspaceBrowser';

export default function App() {
  const pipeline = usePipeline();
  const [workspaceOpen, setWorkspaceOpen] = useState(false);

  const showProgress  = pipeline.phase !== 'idle';
  const showVoiceCast = pipeline.segments.length > 0 && pipeline.phase !== 'idle' && pipeline.phase !== 'analyzing';
  const showResult    = pipeline.phase === 'done';

  return (
    <>
      <header className="app-header">
        <h1>Audiobook <span>Generator</span></h1>
        <p>Paste your chapter text and generate a fully narrated audiobook with distinct character voices.</p>
        <button className="vm-trigger-btn" onClick={() => pipeline.setVoiceManagerOpen(true)}>
          Manage Voices
        </button>
        <button className="vm-trigger-btn" onClick={() => setWorkspaceOpen(true)}>
          Browse Runs
        </button>
      </header>

      <VoiceManager
        open={pipeline.voiceManagerOpen}
        onClose={() => pipeline.setVoiceManagerOpen(false)}
        onVoicesChanged={pipeline.setVoices}
      />

      <WorkspaceBrowser open={workspaceOpen} onClose={() => setWorkspaceOpen(false)} />

      <ServiceHealth />

      <PipelineMap nodes={pipeline.nodes} jobId={pipeline.activeJobId} />

      <AnalyzeForm
        onAnalyze={pipeline.handleAnalyze}
        disabled={pipeline.phase !== 'idle' && pipeline.phase !== 'done'}
        error={pipeline.error}
      />

      {showProgress && (
        <StatusProgress
          analyzing={pipeline.phases.analyzing}
          synthesizing={pipeline.phases.synthesizing}
          assembling={pipeline.phases.assembling}
        />
      )}

      {showVoiceCast && (
        <VoiceCast
          segments={pipeline.segments}
          voices={pipeline.voices}
          onGenerate={pipeline.handleGenerate}
          disabled={pipeline.phase === 'synthesizing'}
        />
      )}

      {showResult && pipeline.outputFile && (
        <AudioPlayer filename={pipeline.outputFile} version={pipeline.audioVersion} />
      )}

      {showResult && pipeline.clips.length > 0 && (
        <PostProduction
          segments={pipeline.segments}
          clips={pipeline.clips}
          voiceMapping={pipeline.voiceMapping}
          engineMapping={pipeline.engineMapping}
          voices={pipeline.voices}
          outputFile={pipeline.outputFile}
          onClipsChange={pipeline.setClips}
          onOutputFileChange={(f: string) => { pipeline.setOutputFile(f); pipeline.setAudioVersion((v: number) => v + 1); }}
        />
      )}
    </>
  );
}
