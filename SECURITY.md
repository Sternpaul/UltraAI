# Security Policy

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository
(Security tab → "Report a vulnerability") instead of a public issue. You will get a
response within a few days.

## Scope

- **The Studio web app** binds to `127.0.0.1` only. Mutating requests require a per-install
  token (see PR-14; until then the `X-UltraAI` header check) and the server validates the
  `Host` header, so a website you visit cannot drive the API (DNS rebinding).
- **The processing server** is reached over SSH with your own keys; jobs run as your user
  in a CPU-capped systemd slice. There is no network listener besides SSH.
- **Downloads** (yt-dlp from YouTube, LRCLIB, model files from Hugging Face) happen on your
  machines with your network; lyrics/metadata come from third parties and are treated as
  untrusted input by the pipeline (they are never executed).

## Automated checks

- **CodeQL** code scanning (Python + JavaScript) and **secret scanning** (incl. push
  protection) are enabled on this public repository — see `.github/workflows/codeql.yml`.
- **gitleaks** scans every pushed commit in CI (`.github/workflows/gitleaks.yml`).
- **Dependabot** alerts + security updates for pip and GitHub Actions are enabled
  (`.github/dependabot.yml`); CI additionally runs `pip-audit` on every PR
  (`.github/workflows/tests.yml`).

## Out of scope

- Issues in third-party dependencies that we cannot fix here: please still report them if
  they affect the pipeline; we will pin or upgrade.
- The Windows installer is unsigned (SmartScreen warning is expected); releases are built
  by CI from the tag, so verify the release provenance on the releases page.
