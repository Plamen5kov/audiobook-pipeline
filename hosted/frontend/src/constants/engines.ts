export const QWEN_VOICES = ['Vivian', 'Serena', 'Uncle_Fu', 'Dylan', 'Eric', 'Ryan', 'Aiden', 'Ono_Anna', 'Sohee'] as const;
export const QWEN_DEFAULT = 'Ryan';
export const EMOTIONS = ['neutral', 'happy', 'sad', 'angry', 'fearful', 'excited', 'tense', 'contemplative', 'curious'] as const;
/**
 * The engine that serves the nine named voices.
 *
 * `qwen3-tts` runs the Base checkpoint, which clones from reference audio and
 * has no preset speakers at all; the presets live on the CustomVoice
 * checkpoint, deployed separately as `qwen3-preset`. Sending a preset name to
 * `qwen3-tts` does not fail — it finds a clone for that speaker in the voice
 * bank and quietly uses it instead, which is how choosing Vivian produced the
 * narrator cloned from the audiobook.
 */
export const QWEN_PRESET_ENGINE = 'qwen3-preset';

export type Engine = 'xtts-v2' | 'qwen3-tts' | 'qwen3-preset';
