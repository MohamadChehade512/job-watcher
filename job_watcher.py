#!/usr/bin/env python3
"""
Job Watcher — pulls software intern/co-op postings from a curated list of
companies, ranks them against your resume, tags new-since-last-run, and
emails a report grouped by country.

Usage:
    python job_watcher.py                 # full run: fetch, rank, email, save state
    python job_watcher.py --dry-run        # everything except sending email; writes output/preview_email.html
    python job_watcher.py --test-sources   # just checks every configured source resolves, prints results
"""
import os
import re
import sys
import json
import time
import argparse
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "companies.json")
STATE_PATH = os.path.join(BASE_DIR, "state", "seen_jobs.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

UA = {"User-Agent": "Mozilla/5.0 (compatible; job-watcher/1.0; personal use)"}
REQUEST_TIMEOUT = 15
MAX_SEEN_IDS = 2000

CANADA_HINTS = [
    "canada", "ontario", "quebec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", " on, ", " qc, ", " bc, ", " ab, ",
    "toronto", "vancouver", "montreal", "ottawa", "waterloo", "calgary",
    "burlington", "mississauga", "kitchener", "edmonton", "winnipeg", "halifax",
]
US_HINTS = [
    "united states", "usa", "u.s.", " ny, ", " ca, us", " ca, u.s", " tx, ",
    " wa, ", " il, ", " ma, ", " fl, ", "new york", "san francisco", "seattle",
    "austin", "chicago", "boston", "los angeles", "miami", "atlanta", "denver",
    "remote - us", "remote (us)",
]


# --------------------------------------------------------------------------
# Config / state
# --------------------------------------------------------------------------
def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_resume(cfg):
    path = os.path.join(BASE_DIR, cfg.get("resume_file", "resume.txt"))
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def load_seen_ids():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(all_current_ids, previously_seen):
    merged = list(previously_seen | set(all_current_ids))
    merged = merged[-MAX_SEEN_IDS:]
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)


# --------------------------------------------------------------------------
# ATS fetchers (clean public JSON APIs)
# --------------------------------------------------------------------------
def fetch_greenhouse(name, token):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    r = requests.get(url, headers=UA, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    jobs = []
    for j in r.json().get("jobs", []):
        jobs.append({
            "id": f"greenhouse:{token}:{j['id']}",
            "company": name,
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "posted_at": j.get("updated_at", ""),
            "description": re.sub("<[^<]+?>", " ", j.get("content", "") or ""),
        })
    return jobs


def fetch_lever(name, token):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    r = requests.get(url, headers=UA, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    jobs = []
    for j in r.json():
        cats = j.get("categories", {}) or {}
        jobs.append({
            "id": f"lever:{token}:{j['id']}",
            "company": name,
            "title": j.get("text", ""),
            "location": cats.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "posted_at": str(j.get("createdAt", "")),
            "description": re.sub("<[^<]+?>", " ", j.get("descriptionPlain", "") or j.get("description", "") or ""),
        })
    return jobs


def fetch_ashby(name, token):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    r = requests.get(url, headers=UA, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    jobs = []
    for j in r.json().get("jobs", []):
        jobs.append({
            "id": f"ashby:{token}:{j.get('id')}",
            "company": name,
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("jobUrl", ""),
            "posted_at": j.get("publishedAt", ""),
            "description": re.sub("<[^<]+?>", " ", j.get("descriptionHtml", "") or ""),
        })
    return jobs


def fetch_smartrecruiters(name, token):
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    r = requests.get(url, headers=UA, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    jobs = []
    for j in r.json().get("content", []):
        loc = j.get("location", {}) or {}
        jobs.append({
            "id": f"smartrecruiters:{token}:{j.get('id')}",
            "company": name,
            "title": j.get("name", ""),
            "location": f"{loc.get('city', '')}, {loc.get('region', '')}, {loc.get('country', '')}",
            "url": j.get("ref", ""),
            "posted_at": j.get("releasedDate", ""),
            "description": "",
        })
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
}


def fetch_all_api_companies(cfg):
    jobs, failed = [], []
    for c in cfg["api_companies"]:
        fn = FETCHERS.get(c["source"])
        if not fn:
            continue
        try:
            jobs.extend(fn(c["name"], c["token"]))
        except Exception as e:
            failed.append({"name": c["name"], "reason": str(e)[:120]})
        time.sleep(0.3)  # be polite
    return jobs, failed


# --------------------------------------------------------------------------
# Hard-tier: fetch raw page text, extract structured jobs via Claude
# --------------------------------------------------------------------------
def fetch_page_text(url, max_chars=15000):
    r = requests.get(url, headers=UA, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    text = re.sub("<script.*?</script>", " ", r.text, flags=re.S | re.I)
    text = re.sub("<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub("<[^<]+?>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def extract_jobs_with_claude(client, name, url, page_text):
    if not page_text or len(page_text) < 200:
        return []
    prompt = f"""You are extracting job postings from a careers page's raw text for "{name}".
Find postings that are software/tech-related internships or co-ops. Return ONLY a JSON array
(no prose, no markdown fences) of objects with keys: title, location, posted_hint (any date/season
text you see near the posting, or ""). If you cannot find any matching postings, or the text does
not look like a list of jobs, return [].

PAGE TEXT:
{page_text}"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        raw = raw.strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
        items = json.loads(raw) if raw else []
    except Exception:
        return []
    jobs = []
    for i, it in enumerate(items):
        title = it.get("title", "").strip()
        if not title:
            continue
        jobs.append({
            "id": f"scrape:{name}:{hash(title) & 0xffffffff}:{i}",
            "company": name,
            "title": title,
            "location": it.get("location", ""),
            "url": url,
            "posted_at": it.get("posted_hint", ""),
            "description": "",
        })
    return jobs


def fetch_all_scrape_targets(cfg, client):
    jobs, failed = [], []
    if client is None:
        return jobs, [{"name": t["name"], "reason": "no ANTHROPIC_API_KEY set", "url": t["url"]} for t in cfg["scrape_targets"]]
    for t in cfg["scrape_targets"]:
        try:
            text = fetch_page_text(t["url"])
            extracted = extract_jobs_with_claude(client, t["name"], t["url"], text)
            if extracted:
                jobs.extend(extracted)
            else:
                failed.append({"name": t["name"], "reason": "no matches found / page blocked scraping", "url": t["url"]})
        except Exception as e:
            failed.append({"name": t["name"], "reason": str(e)[:120], "url": t["url"]})
        time.sleep(0.5)
    return jobs, failed


# --------------------------------------------------------------------------
# Filtering + country classification
# --------------------------------------------------------------------------
def classify_country(location):
    loc = f" {location.lower()}, "
    if any(h in loc for h in CANADA_HINTS):
        return "CA"
    if any(h in loc for h in US_HINTS):
        return "US"
    return "OTHER"


def passes_filters(job, filters):
    title = job.get("title", "").lower()
    blob = f"{title} {job.get('description', '')}".lower()

    if not any(k in title for k in filters["title_must_include_any"]):
        return False
    if any(k in title for k in filters["exclude_title_keywords"]):
        return False
    if not any(k in blob for k in filters["role_keywords_any"]):
        return False
    return True


def filter_jobs(jobs, filters):
    out = []
    seen_ids = set()
    for j in jobs:
        if j["id"] in seen_ids:
            continue
        seen_ids.add(j["id"])
        if not passes_filters(j, filters):
            continue
        j["country"] = classify_country(j.get("location", ""))
        if j["country"] not in filters["countries_allowed"]:
            continue
        out.append(j)
    return out


# --------------------------------------------------------------------------
# Resume-based ranking (single batched Claude call, chunked)
# --------------------------------------------------------------------------
def rank_with_resume(jobs, resume_text, client, chunk_size=20):
    if not jobs:
        return jobs
    if client is None or not resume_text:
        for j in jobs:
            j["score"], j["blurb"] = 50, ""
        return jobs

    for start in range(0, len(jobs), chunk_size):
        chunk = jobs[start:start + chunk_size]
        listing = [{"id": j["id"], "company": j["company"], "title": j["title"],
                    "location": j["location"], "description": j.get("description", "")[:600]}
                   for j in chunk]
        prompt = f"""Here is a student's resume, then a list of job postings as JSON.
Score how well each posting fits the resume from 0-100 (skills/experience overlap, stated
interests, career relevance for a software/tech internship or co-op). Write a one-sentence,
specific, first-principles reason for each score referencing something concrete from the resume.

Return ONLY a JSON array (no prose, no markdown fences) of objects: {{"id": ..., "score": ..., "blurb": ...}}

RESUME:
{resume_text}

POSTINGS:
{json.dumps(listing, indent=2)}"""
        try:
            resp = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
            results = {r["id"]: r for r in json.loads(raw)}
        except Exception:
            results = {}
        for j in chunk:
            r = results.get(j["id"], {})
            j["score"] = r.get("score", 40)
            j["blurb"] = r.get("blurb", "")
    return jobs


# --------------------------------------------------------------------------
# Email rendering
# --------------------------------------------------------------------------
COUNTRY_LABEL = {"CA": "🇨🇦 Canada", "US": "🇺🇸 United States"}


def render_email_html(jobs, failed_api, failed_scrape, run_time):
    jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)
    new_count = sum(1 for j in jobs if j.get("is_new"))

    by_country = {"CA": [], "US": []}
    for j in jobs:
        by_country.setdefault(j["country"], []).append(j)

    def job_row(j):
        new_badge = (
            '<span style="background:#16a34a;color:#fff;font-size:11px;font-weight:700;'
            'padding:2px 7px;border-radius:10px;margin-right:6px;">NEW</span>'
        ) if j.get("is_new") else ""
        blurb = f'<div style="color:#555;font-size:13px;margin-top:4px;">{j.get("blurb", "")}</div>' if j.get("blurb") else ""
        score = j.get("score", 0)
        return f"""
        <tr>
          <td style="padding:14px 16px;border-bottom:1px solid #eee;">
            <div style="font-size:15px;font-weight:600;color:#111;">
              {new_badge}{j['title']}
            </div>
            <div style="font-size:13px;color:#666;margin-top:2px;">
              {j['company']} &middot; {j.get('location','') or 'Location n/a'} &middot;
              <span style="color:#999;">relevance {score}/100</span>
            </div>
            {blurb}
            <div style="margin-top:8px;">
              <a href="{j['url']}" style="font-size:13px;color:#2563eb;text-decoration:none;font-weight:600;">
                View posting &rarr;
              </a>
            </div>
          </td>
        </tr>"""

    sections = ""
    for cc in ["CA", "US"]:
        group = by_country.get(cc, [])
        if not group:
            continue
        sections += f"""
        <tr><td style="padding:22px 16px 8px 16px;font-size:16px;font-weight:700;color:#111;">
          {COUNTRY_LABEL[cc]} <span style="color:#999;font-weight:400;">({len(group)})</span>
        </td></tr>"""
        sections += "".join(job_row(j) for j in group)

    fallback_rows = ""
    all_failed = failed_api + failed_scrape
    if all_failed:
        items = "".join(
            f'<li style="margin-bottom:4px;">{f["name"]}'
            + (f' — <a href="{f["url"]}" style="color:#2563eb;">check manually</a>' if f.get("url") else "")
            + f' <span style="color:#999;">({f["reason"]})</span></li>'
            for f in all_failed
        )
        fallback_rows = f"""
        <tr><td style="padding:20px 16px;background:#fafafa;border-top:1px solid #eee;">
          <div style="font-size:13px;font-weight:700;color:#555;margin-bottom:6px;">
            Couldn't auto-check these — worth a manual look:
          </div>
          <ul style="font-size:13px;color:#555;padding-left:18px;margin:0;">{items}</ul>
        </td></tr>"""

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;overflow:hidden;border:1px solid #eee;">
  <tr><td style="padding:24px 16px 8px 16px;">
    <div style="font-size:20px;font-weight:800;color:#111;">Job Watcher</div>
    <div style="font-size:13px;color:#888;margin-top:2px;">{run_time} &middot; {len(jobs)} open roles match your filters
      {f'&middot; <span style="color:#16a34a;font-weight:700;">{new_count} new</span>' if new_count else ''}
    </div>
  </td></tr>
  {sections if jobs else '<tr><td style="padding:24px 16px;color:#888;">No matching postings right now.</td></tr>'}
  {fallback_rows}
  <tr><td style="padding:18px 16px;color:#aaa;font-size:12px;border-top:1px solid #eee;">
    Ranked against your resume &middot; ordered by relevance &middot; runs daily via GitHub Actions
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def send_email(html, subject):
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587))
    user, pw = os.environ["SMTP_USER"], os.environ["SMTP_PASS"]
    to_addr = os.environ["EMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pw)
        s.sendmail(user, [to_addr], msg.as_string())


# --------------------------------------------------------------------------
# Source testing
# --------------------------------------------------------------------------
def test_sources(cfg):
    print("Testing api_companies...\n")
    for c in cfg["api_companies"]:
        fn = FETCHERS.get(c["source"])
        try:
            jobs = fn(c["name"], c["token"])
            print(f"  OK    {c['name']:<15} ({c['source']}) — {len(jobs)} postings")
        except Exception as e:
            print(f"  FAIL  {c['name']:<15} ({c['source']}) — {str(e)[:100]}")
    print("\nscrape_targets are not auto-tested here (they need ANTHROPIC_API_KEY) — "
          "run a normal --dry-run to exercise those.")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="skip sending email; write output/preview_email.html")
    parser.add_argument("--test-sources", action="store_true", help="just check each api_company source resolves")
    args = parser.parse_args()

    cfg = load_config()

    if args.test_sources:
        test_sources(cfg)
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = None
    if api_key:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

    print("Fetching API companies...")
    api_jobs, failed_api = fetch_all_api_companies(cfg)
    print(f"  {len(api_jobs)} raw postings from {len(cfg['api_companies'])} companies")

    print("Fetching + extracting hard-tier targets...")
    scrape_jobs, failed_scrape = fetch_all_scrape_targets(cfg, client)
    print(f"  {len(scrape_jobs)} raw postings extracted")

    all_jobs = api_jobs + scrape_jobs
    filtered = filter_jobs(all_jobs, cfg["filters"])
    print(f"  {len(filtered)} postings after filtering")

    resume_text = load_resume(cfg)
    ranked = rank_with_resume(filtered, resume_text, client)

    seen_ids = load_seen_ids()
    for j in ranked:
        j["is_new"] = j["id"] not in seen_ids

    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = render_email_html(ranked, failed_api, failed_scrape, run_time)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    preview_path = os.path.join(OUTPUT_DIR, "preview_email.html")
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {preview_path}")

    if not args.dry_run:
        new_count = sum(1 for j in ranked if j["is_new"])
        subject = f"{cfg['email']['subject_prefix']}: {len(ranked)} roles ({new_count} new)"
        send_email(html, subject)
        print("Email sent.")

    save_seen_ids([j["id"] for j in ranked], seen_ids)
    print("State saved.")


if __name__ == "__main__":
    main()
