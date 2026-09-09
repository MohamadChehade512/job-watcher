"""One-off: renders a sample email with realistic mock data through the real
render_email_html() function, so the design can be reviewed without needing
live API calls or a real Claude key. Not part of the production pipeline."""
from job_watcher import render_email_html

MOCK_JOBS = [
    dict(id="1", company="Wealthsimple", title="Software Engineer Intern (Full Stack)",
         location="Toronto, ON", country="CA", url="https://jobs.ashbyhq.com/wealthsimple/example",
         score=94, is_new=True,
         blurb="Fintech + full-stack TypeScript/React lines up directly with your Bell Canada e-commerce work and your stated interest in software at the intersection of engineering and financial services."),
    dict(id="2", company="Shopify", title="Backend Developer Intern",
         location="Ottawa, ON", country="CA", url="https://www.shopify.com/careers/example",
         score=90, is_new=False,
         blurb="Commerce-platform backend work is a close match to your checkout-flow ownership and API redesign at Bell."),
    dict(id="3", company="Citadel", title="Software Engineer Intern",
         location="New York, NY", country="US", url="https://jobs.citadel.com/example",
         score=83, is_new=True,
         blurb="Strong technical bar and your AWS/backend experience apply well, though it's less finance-product-specific than Wealthsimple."),
    dict(id="4", company="Anthropic", title="Software Engineering Intern",
         location="San Francisco, CA", country="US", url="https://job-boards.greenhouse.io/anthropic/example",
         score=79, is_new=False,
         blurb="You already use Claude Code day-to-day at Bell and hold a Generative AI Leader cert — relevant signal even outside a direct skills match."),
    dict(id="5", company="RBC", title="Technology Co-op Student, Digital Banking",
         location="Toronto, ON", country="CA", url="https://jobs.rbc.com/example",
         score=76, is_new=True,
         blurb="Banking + software matches your explicit interest in fintech, and your Salesforce/API integration work at Bell is directly transferable."),
    dict(id="6", company="Stripe", title="Software Engineer Intern, Payments Infrastructure",
         location="San Francisco, CA", country="US", url="https://job-boards.greenhouse.io/stripe/example",
         score=71, is_new=False,
         blurb="Payments/API infra overlaps with your checkout-system work, though the role skews more backend-systems than your recent frontend-heavy experience."),
    dict(id="7", company="TD Bank", title="Summer Student, Software Engineering",
         location="Toronto, ON", country="CA", url="https://jobs.td.com/example",
         score=68, is_new=False, blurb="Solid fintech fit; less detail available on the specific tech stack."),
    dict(id="8", company="DoorDash", title="Software Engineer Intern",
         location="Toronto, ON", country="CA", url="https://job-boards.greenhouse.io/doordash/example",
         score=58, is_new=False, blurb="General full-stack overlap, but no particular alignment with your fintech interest."),
]

FAILED_API = [{"name": "GitLab", "reason": "404 — board token likely renamed"}]
FAILED_SCRAPE = [
    {"name": "Google", "reason": "page blocked automated extraction", "url": "https://www.google.com/about/careers/applications/jobs/results?target_level=INTERN_OR_APPRENTICE"},
    {"name": "Questrade", "reason": "no matches found on page", "url": "https://www.questrade.com/careers"},
]

html = render_email_html(MOCK_JOBS, FAILED_API, FAILED_SCRAPE, "2026-09-09 12:00 UTC")
with open("output/preview_email.html", "w", encoding="utf-8") as f:
    f.write(html)
print("wrote output/preview_email.html")
