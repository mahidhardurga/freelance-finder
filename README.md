# Freelance Finder

Remote freelance and contract jobs you can do from anywhere, paid in USD, matched against a Java / Spring / microservices / AI stack.
A job-search interface: filters on the left, results in the middle, the full posting on the right; light and dark themes; a saved-jobs
and application tracker with notes and CSV export; email drafts; shareable filter links; keyboard shortcuts (press `?`). Everything the
tracker, notes and profile store stays in your browser. Free public sources only, no accounts and no API keys.

Live: https://freelance.mahidhardurga.in

## How it works
- `app.py` fetches Remotive, Jobicy, Himalayas, We Work Remotely and the Hacker News "Who is hiring?" and
  "Freelancer? Seeking freelancer?" threads, scores each job against the stack in `STACK`, and detects contract wording,
  USD pay and "work from anywhere" with plain keyword rules (no AI involved).
- **Locally:** `python app.py` (or `start.bat`) serves the app at http://127.0.0.1:8765 and fetches live. Standard library only.
- **On GitHub Pages:** `.github/workflows/deploy.yml` runs `python app.py --export` every 15 minutes, publishes
  `data/jobs.json` together with `index.html`. Remotive is only re-fetched every 6 hours (its docs ask for gentle use), and if a
  source fails its last good data is kept.

## India-based jobs and location filters
A second Himalayas source (`Himalayas India`) uses its public search API with the country filter for India, running a short list of
queries (Java, Spring Boot, AI, DevOps, contract, ...). It is fetched at most every 2 hours. Every job also gets `india` (based in or
explicitly for India, from the title, location field and first line only), `city`, `mode` (remote / hybrid / on-site) and `currency`.
On the page: the **India-based** pill, plus a **Location & pay** menu for city, work mode and pay currency. Himalayas jobs are tagged **free to apply**, so they show under the default filter, and each card says how Apply works.
Honest limits: most listings are full-time and some come from staffing firms; Himalayas gives countries, not cities, so few jobs show a city.

## Apply links
Each job is tagged by what Apply does: **direct** (straight to the employer or a contact: Hacker News, Remotive), **sign-up wall**
(We Work Remotely, Jobicy) or **free to apply** (Himalayas: applying goes through a free Himalayas account and the free plan has no payment step; checked signed in on 2026-10-19). The page shows direct and free-to-apply jobs by default. RemoteOK is deliberately not used,
because its Apply button leads to a paid Premium page.

## Tuning
Edit the `STACK` block at the top of the scoring section in `app.py` (skills and weights) and `MIN_CORE` for stricter matching.

## Data
Job data belongs to the sources above and is shown with a link back to each original posting.
