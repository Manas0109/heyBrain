# Spec: Audio capture and transcription (issue #5)

## Purpose

Turns a spoken thought into a transcript, entirely locally. Captures microphone
audio to a short-lived WAV file and runs it through a local `faster-whisper`
model to produce text. No audio is ever persisted outside `$HEYBRAIN_HOME/tmp/`,
and none survives the call that consumes it — this is the mic/STT layer that
`brain think --voice` (issue #7) and `brain doctor` (issue #6) build on.
Fully independent of Bedrock and SQLite.

## Public API

- **`heybrain.audio.record.record_until_enter() -> Path`**
  Records from the default microphone, printing `🎙 Listening… [Enter to
  stop]`, and stops when the user presses Enter. Returns the path to a
  16kHz mono WAV file written under `$HEYBRAIN_HOME/tmp/`.

- **`heybrain.transcription.whisper.transcribe(path: Path) -> str`**
  Runs the cached `faster-whisper` model over the WAV at `path` and returns
  the transcript text. Always deletes `path` before returning or raising.

- **`heybrain.transcription.whisper.warm_model() -> None`**
  Loads (and downloads, if not already cached) the configured whisper model
  without transcribing anything, so `brain doctor` can pay that cost up
  front instead of on the user's first recording.

## Key constraints another agent must respect

- **Format is fixed at 16kHz mono, 16-bit PCM** (`audio/record.py`'s
  `SAMPLE_RATE`/`CHANNELS`/`DTYPE`) — this is what whisper wants natively,
  so nothing resamples between capture and transcription.
- **The temp WAV is always deleted**, including on exception.
  `transcribe()` wraps the model call in `try/finally` and calls
  `path.unlink(missing_ok=True)` in the `finally` block unconditionally —
  a raised `TranscriptionError` or any other exception from the model still
  cleans up the file. Callers can rely on `$HEYBRAIN_HOME/tmp/` being empty
  again once `transcribe()` returns or raises.
- **Empty or whitespace-only transcripts raise `TranscriptionError`**
  (`core/errors.py`, issue #1) instead of returning `""` — silence never
  flows downstream as an empty string.
- **Mic permission failures raise a clear, actionable `TranscriptionError`.**
  If opening the input stream raises `sounddevice.PortAudioError`,
  `record_until_enter()` re-raises it as `TranscriptionError` with a
  message naming **System Settings → Privacy & Security → Microphone** as
  the fix.
- **Model caching**: the whisper model name comes from
  `Settings.whisper_model` (default `base.en`) and is cached under
  `$HEYBRAIN_HOME/models/` (`Settings.models_dir`) via `faster-whisper`'s
  `download_root`. The loaded `WhisperModel` instance itself is memoized
  in-process (`functools.lru_cache`), so repeated `transcribe()`/
  `warm_model()` calls in the same run don't reload it.
- **Platform-neutral signature, macOS-first implementation.**
  `record_until_enter()` takes no platform argument and returns just a
  `Path`; the `sounddevice`/PortAudio capture and the mic-permission
  wording are macOS-specific, but callers don't need to know that.

## In-flight change to be aware of

A separate, not-yet-merged PR is changing `audio/record.py`'s interaction
model from auto-start/Enter-to-stop (the behavior documented above, current
on `main` as of this writing) to a two-step toggle — Enter to start, Enter to
stop — plus a timeout/watchdog around the recording cycle to fix a reported
hang. The public signature (`record_until_enter() -> Path`) and the
constraints above (format, cleanup, error types) are expected to hold either
way, but the exact interaction/prompt text may already be stale by the time
you read this — check `audio/record.py` directly if it matters for your work.
