# Freelance Finder

Remote freelance and contract jobs you can do from anywhere, paid in USD, matched against a Java / Spring / microservices / AI stack.
Netflix-style browsing: hero billboard, carousels, detail modal, My List. Free public sources only, no accounts and no API keys.

Live: https://freelance.mahidhardurga.in

## How it works
- `app.py` fetches Remotive, Jobicy, Himalayas, We Work Remotely and the Hacker News "Who is hiring?" and
  "Freelancer? Seeking freelancer?" threads, scores each job against the stack in `STACK`, and detects contract wording,
  USD pay and "work from anywhere" with plain keyword rules (no AI involved).
- **Locally:** `python app.py` (or `start.bat`) serves the app at http://127.0.0.1:8765 and fetches live. Standard library only.
- **On GitHub Pages:** `.github/workflows/deploy.yml` runs `python app.py --export` every 15 minutes, publishes
  `data/jobs.json` together with `index.html`. Remotive is only re-fetched every 6 hours (its docs ask for gentle use), and if a
  source fails its last good data is kept.

## Apply links
Each job is tagged by what Apply does: **direct** (straight to the employer or a contact: Hacker News, Remotive), **sign-up wall**
(We Work Remotely, Jobicy) or **unverified** (Himalayas). The page shows direct-apply jobs by default. RemoteOK is deliberately not used,
because its Apply button leads to a paid Premium page.

## Tuning
Edit the `STACK` block at the top of the scoring section in `app.py` (skills and weights) and `MIN_CORE` for stricter matching.

## Data
Job data belongs to the sources above and is shown with a link back to each original posting.
