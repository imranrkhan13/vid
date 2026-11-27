# Third-party notices

## eSpeak NG (text-to-speech, "Add free voice")
- Component: the shared library `libespeak-ng` and its `espeak-ng-data` files, installed unmodified from the PyPI wheel `espeakng-loader` (version >=0.2.4, MIT-licensed loader, https://github.com/thewh1teagle/espeakng-loader).
- License of eSpeak NG: GNU General Public License v3.0 (https://github.com/espeak-ng/espeak-ng/blob/master/COPYING).
- Corresponding source: https://github.com/espeak-ng/espeak-ng. We ship no modified copy and no patches.
- How it is used: VGOH loads the library at runtime through `ctypes` (`backend/app/media/tts.py`) and exchanges text and PCM audio with it. It is not linked at build time.
- Whoever redistributes a build that bundles this library must keep this notice and provide or point to the source above. The GPL-3.0 text applies to eSpeak NG itself. Whether loading it via ctypes makes the rest of VGOH a combined work under the GPL is a legal question this project has not had reviewed. If that matters for your use, remove `espeakng-loader` from `backend/requirements.txt`; the app then reports "local text-to-speech is not available" and everything else keeps working.
- The voice is robotic by design. It is a free baseline, not a neural voice.
