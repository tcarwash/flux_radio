# Sotto GP0 FIR15 on-air field note

## Status

The experimental `SOTTO FIR` GP0 microphone TX module was installed in slot 24 and read-back verified. The existing `SOTTO RX` module remained in slot 28. The on-air test subsequently produced a received audio recording. The operator recognized their own callsign in the recording, confirming that speech made it through the experimental Sotto TX path over RF.

The operator reports that the speech is quiet and muffled with substantial broadband noise. Speaking louder into an external microphone improved the result. This is a successful proof of speech transmission, not evidence that the FIR improves intelligibility or that the microphone-path SNR is solved.

## Received recording

The operator-provided WAV is 2.68 seconds, mono, PCM 16-bit at 8 kHz. It has no full-scale clipping; decoded peak is about 0.307 and RMS is about 0.040 (approximately -28 dBFS RMS). The audio itself is not copied into the repository; these notes preserve the result without checking in the operator's voice/callsign recording.

## Interpretation and next experiment

The result establishes an end-to-end on-air Sotto voice path with the installed experimental module. It does not isolate how much broadband noise originates at the microphone/GP0 input versus sampling, filtering, codec, RF, or receiver playback. The on-air clip is post-decode and cannot be used as a controlled input-side EQ/compression comparison.

An input-side EQ/compressor remains a candidate experiment. EQ may reduce low-frequency rumble and modestly emphasize speech presence. Compression can make quiet speech more audible but cannot improve SNR and may raise the broadband noise floor. Any future comparison should use the same phrase, microphone position, and speaking level, with Wiener-only and Wiener-plus-EQ/compression builds compared blindly where possible. Keep the build experimental until paired listening demonstrates a repeatable intelligibility improvement.
