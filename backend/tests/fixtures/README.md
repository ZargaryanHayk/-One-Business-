`task.wav` is a locally generated synthetic voice saying:

> Remember to review the project plan tomorrow.

It is a mono PCM WAV fixture for deterministic transport tests and the optional real local
Whisper test. Telegram download is replaced with a fixture copy; Redis, Celery, PostgreSQL,
audio decoding, and (with `-m transcription`) speech recognition run for real.

The recognizer is checked for the word `review`, allowing harmless punctuation differences.
