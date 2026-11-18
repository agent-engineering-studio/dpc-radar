# Security

If you find a vulnerability, do not open a public issue: use the private
report of the repository ("Report a vulnerability" in the Security tab), with
the version, the steps to reproduce it and what it allows. You will get an
answer within 7 days.

What the project handles, to judge a report:

- **No personal data and no accounts.** The library reads open data of the
  Dipartimento della Protezione Civile; the demo keeps nothing on disk and
  has no login.
- **No key is needed** to use the library, the command line, the MCP server
  or the demo. `ANTHROPIC_API_KEY` is read only by `benchmarks/vision.py`; it
  lives in `.env` or in the environment, and `.env` is never committed.
- **The demo answers anyone who reaches it.** Its API (`demo/api.py`) reads
  two numbers, a longitude and a latitude, and asks the DPC for frames. Put a
  reverse proxy with HTTPS and a rate limit in front of it before you publish
  it.

Whoever hosts the demo stays responsible for HTTPS, for the tiles of the
background map (`DPC_RADAR_TILES`: by default they come from OpenStreetMap,
which sees the address of every visitor) and for keeping the image up to
date.
