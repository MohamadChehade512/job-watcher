# Job Watcher

Daily email of open software intern/co-op roles, filtered for Canada + US,
ranked against your resume, with new-since-last-run tagging.

## How it works

- **Clean sources** (`companies.json` -> `api_companies`): pulls directly from
  each company's public Greenhouse / Lever / Ashby / SmartRecruiters API. Fast,
  reliable, no scraping involved.
- **Hard-tier sources** (`companies.json` -> `scrape_targets`): companies with no
  public job API (Google, Citadel, Shopify, Questrade, the big Canadian banks).
  The script fetches the raw page text and asks Claude to pull out matching
  postings. This is inherently less reliable — some of these pages actively
  resist bots (Google especially) — so any source that comes back empty shows
  up in the email as a "check manually" link instead of silently disappearing.
- **Ranking**: every posting that survives the keyword filters gets sent to
  Claude alongside `resume.txt`, which scores it 0-100 for fit and writes a
  one-line reason. The email is sorted by that score, grouped by country.
- **NEW tagging**: `state/seen_jobs.json` keeps a rolling list of posting IDs
  from past runs. Anything not in that list gets a green NEW badge. The
  GitHub Actions workflow commits the updated state file back to the repo
  after every run so this persists between scheduled runs.

## Setup

1. **Create a repo** and push this folder to it (or ask me and I'll walk
   through the git commands with you).

2. **Get an Anthropic API key** at console.anthropic.com/settings/keys — this
   is separate from your claude.ai login and needs a small amount of credit
   loaded (this workload is cheap — well under $1/month at daily frequency).

3. **Set up email sending.** Easiest path is Gmail with an App Password
   (myaccount.google.com/apppasswords, requires 2FA enabled on the account).
   Any SMTP provider works.

4. **Add repo secrets** — GitHub repo -> Settings -> Secrets and variables ->
   Actions -> New repository secret. Add all five:
   `ANTHROPIC_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`

5. **Verify the company list** — board tokens in `companies.json` can go
   stale. Run locally first:
   ```
   pip install -r requirements.txt
   python job_watcher.py --test-sources
   ```
   Fix or delete any company that fails.

6. **Trial run without emailing yourself:**
   ```
   python job_watcher.py --dry-run
   ```
   Writes `output/preview_email.html` — open it in a browser to see exactly
   what the email will look like before it ever gets sent.

7. **Enable the schedule** — the workflow in `.github/workflows/job-watcher.yml`
   runs daily at 12:00 UTC. Change the cron line to adjust timing, or trigger
   a run manually anytime from the repo's Actions tab -> Job Watcher -> Run workflow.

## Tuning

- `companies.json -> filters` controls the keyword/season/country matching —
  add companies, adjust `season_keywords_any` as "next year" rolls forward,
  or widen `role_keywords_any` if you want to catch adjacent roles.
- Add more companies to `api_companies` by finding their Greenhouse/Lever/Ashby
  board token (visible in their careers page URL) — no code changes needed.
- `resume.txt` drives the ranking — update it whenever your resume changes.
