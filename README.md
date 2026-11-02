\# Python Instant Messaging



A Discord-like chat app with DMs, groups, voice/video calls, and screen sharing.



\## Status

Early revival. Architecture being rebuilt from the old IP-based `/send` beta.



\## Architecture (planned)

\- \*\*Backend\*\*: FastAPI + WebSocket relay, deployed on Render/Fly.io

\- \*\*Frontend\*\*: Web (HTML/JS) — WebRTC for voice/video/screen share

\- \*\*Signaling\*\*: WebSocket (same connection as text chat)

\- \*\*TURN\*\*: required for cross-network calls



\## Roadmap

\- \[ ] Project skeleton

\- \[ ] Text chat over WebSocket (DMs + groups)

\- \[ ] Username login

\- \[ ] Deploy server

\- \[ ] WebRTC voice call

\- \[ ] WebRTC video call

\- \[ ] Screen sharing

\- \[ ] TURN server

