"""
Freelance Finder - remote freelance/contract jobs you can do from anywhere, paid in USD.

Run:  python app.py        then open http://127.0.0.1:8000
No accounts, no API keys, no third-party packages. Data comes from free public feeds.
"""
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
CACHE_SECONDS = 30 * 60
UA = {"User-Agent": "freelance-finder/1.0 (personal job search)"}

# --------------------------------------------------------------------------- text helpers
FREELANCE_RE = re.compile(r"\b(contract(or)?|freelanc\w*|part[- ]?time|consult(ant|ing)?|1099|fixed[- ]term|gig)\b", re.I)
ANYWHERE_RE = re.compile(r"\b(worldwide|anywhere|global(ly)?|international|any (country|timezone|time zone)|work from anywhere|all countries)\b", re.I)
RESTRICTED_RE = re.compile(
    r"(\b(us|usa|u\.s\.a?\.?|united states)[- ]?(only|based|residents?|citizens?)\b"
    r"|\bremote\s*[\(\[,/-]\s*(us|usa|u\.s\.|united states)\b"
    r"|[\(\[]\s*(us|usa|u\.s\.|united states)\b|\b(us|usa)\s*/\s*remote\b|\bremote\s*/\s*(us|usa)\b|\bus (timezones?|time zones?|hours)\b"
    r"|\b(est|pst|cst|et|pt)\s*(hours|time ?zone|overlap)\b|\b(uk|eu|european)\s+(timezones?|residents?|based)\b"
    r"|\b(must|need to|have to)\s+(be\s+)?(located|based|live|reside|living|residing)\b"
    r"|work authori[sz]ation|authori[sz]ed to work|\bcitizens?\b|security clearance|\bw-?2\b"
    r"|\b(europe|eu|uk|canada|latam|emea)[- ]only\b|\bno (visa|sponsorship)\b|\bon-?site( only)?\b)", re.I)
# Hard blockers: jobs you cannot take from India. These are checked against the FULL posting text and hide the job.
BLOCK_ALWAYS = [
    ("US citizenship required", re.compile(
        r"\b(us|u\.s\.a?\.?|united states|american)\s+citizen(s|ship)?\b|\bcitizen(ship)?\s+(is\s+)?(required|mandatory)\b|\bgreen card\b", re.I)),
    ("Security clearance", re.compile(
        r"\b(security|government|secret|top[- ]secret|public trust|dod)\s+clearance\b|\bts\s*/\s*sci\b|\bclearance\s+(required|eligib\w+)\b|\bobtain\s+(a\s+)?clearance\b", re.I)),
    ("US work authorization", re.compile(
        r"work authori[sz]ation|(legally\s+)?authori[sz]ed to work in the (us|u\.s\.a?\.?|united states)|"
        r"eligib\w+ to work in the (us|u\.s\.a?\.?|united states)|\bw-?2\s+(employee|only|position)\b|\bno (c2c|corp[- ]to[- ]corp)\b", re.I)),
]
BLOCK_LOCATION = [  # skipped when the posting mentions India, since "based in India" would be a good sign
    ("US-only", re.compile(
        r"\b(us|usa|u\.s\.a?\.?|united states)[- ]?(only|residents?|resident only)\b|\bonly\s+(open\s+to\s+)?(us|usa|united states)\b"
        r"|\b(must|need to|have to)\s+(be\s+)?(located|based|live|reside|living|residing)\s+(in|within)\s+(the\s+)?(us|usa|u\.s\.a?\.?|united states)\b"
        r"|\bremote\s*[\(\[,/-]\s*(us|usa|u\.s\.a?\.?|united states)\b|[\(\[]\s*(us|usa|u\.s\.a?\.?|united states)\s*[/,)\]-]"
        r"|\b(us|usa)\s*/\s*remote\b|\bus[- ]based (candidates|applicants|engineers|contractors|developers|talent)\b", re.I)),
    ("Region-locked", re.compile(
        r"\b(europe|eu|uk|canada|latam|emea|australia|north america)[- ]?(only|residents?)\b"
        r"|\b(must|need to|have to)\s+(be\s+)?(located|based|live|reside|living|residing)\s+(in|within)\s+(the\s+)?(uk|united kingdom|eu|europe|european union|canada|australia|germany|france|spain|netherlands|brazil|mexico|latam)\b", re.I)),
]
INDIA_RE = re.compile(r"\bindia\b|\bapac\b|\basia\b", re.I)


def find_blocker(text: str, location: str = "") -> str | None:
    blob = f"{location} {text}"
    for reason, rx in BLOCK_ALWAYS:
        if rx.search(blob):
            return reason
    if not INDIA_RE.search(blob):
        for reason, rx in BLOCK_LOCATION:
            if rx.search(blob):
                return reason
    return None


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
OBFUSCATED_EMAIL_RE = re.compile(r"([\w.+-]+)\s*[\[\(]\s*at\s*[\]\)]\s*([\w-]+(?:\s*[\[\(]?\s*dot\s*[\]\)]?\s*[\w-]+)+)", re.I)
# --------------------------------------------------------------------------- your tech stack
# Edit these to tune matching. Each entry: label shown on the card -> (regex, weight).
# A term found in the TITLE counts 3x its weight; found in the description it counts 1x (once per term).
STACK = {
    # core: Java / Spring / microservices
    "Java":              (r"\bjava\b(?!\s*script)", 3),
    "Spring Boot":       (r"\bspring[\s-]?boot\b", 4),
    "Spring":            (r"\bspring\b(?![\s-]?(boot\b|sale|season|break))", 3),
    "Microservices":     (r"\bmicro[\s-]?services?\b", 4),
    "JVM":               (r"\b(jvm|jakarta ee|j2ee|hibernate|jpa|kotlin)\b", 2),
    # core: AI work
    "AI agents":         (r"\b(ai|llm|autonomous)[\s-]?agents?\b|\bagentic\b|\bagent (framework|orchestration)\b|\bmulti-agent\b", 4),
    "LLM":               (r"\b(llms?|large language models?|generative ai|gen ?ai|gpt|openai|anthropic|claude|gemini)\b", 3),
    "RAG":               (r"\brag\b|\bretrieval[\s-]augmented\b|\bvector (db|database|search|store)s?\b|\bembeddings?\b", 4),
    "Prompt engineering": (r"\bprompt (engineering|design)\b|\blangchain\b|\bllamaindex\b|\bmcp\b|\bmodel context protocol\b", 3),
    "AI/ML":             (r"\b(ai|a\.i\.|machine learning|ml|nlp|chatbots?)\b", 1),
    # supporting skills from your profile
    "REST APIs":         (r"\b(rest(ful)?[\s-]?apis?|web services|api (development|integration|design))\b", 2),
    "AWS":               (r"\b(aws|amazon web services|ec2|lambda)\b", 2),
    "Docker/K8s":        (r"\b(docker|kubernetes|k8s|helm)\b", 1),
    "DevOps/CI-CD":      (r"\b(devops|ci/?cd|jenkins|terraform|gitlab ci)\b", 1),
    "Databases":         (r"\b(postgres(ql)?|mysql|db2|sql)\b", 1),
    "Backend":           (r"\b(back-?end|server-side|distributed systems?)\b", 1),
    "Python/FastAPI":    (r"\b(fastapi|python)\b", 1),
    "Kafka/queues":      (r"\b(kafka|rabbitmq|event-driven)\b", 1),
}
STACK_RE = {label: (re.compile(rx, re.I), w) for label, (rx, w) in STACK.items()}
# A job counts as "matching your stack" when its CORE terms score at least this much. In the title a core
# term scores 3x its weight (always enough). In the description one passing mention scores half its weight
# (not enough alone), and two or more mentions score the full weight (e.g. "Java" twice = 3, "Spring Boot" twice = 4).
MIN_CORE = 3
# Roles that are clearly a different stack. Dropped when the TITLE has one and no core term is in the title.
OFF_STACK_TITLE_RE = re.compile(
    r"(\b(shopify|wordpress|woocommerce|php|laravel|ruby|rails|salesforce|swift|ios|android|unity|unreal|"
    r"react native|flutter|wix|webflow|magento|drupal|sap|dynamics|oracle)\b|(?<!\w)\.net\b|\bc#)", re.I)
# Not engineering work at all. Always dropped, even if the title says "AI".
NOT_ENGINEERING_TITLE_RE = re.compile(
    r"\b(annotat\w*|data entry|marketing|sales|copywriter|writer|designer|recruiter|accountant|customer|support|"
    r"co-?founder|ceo|cto|coo|legal|finance|manager|sourcing|director|vp|"
    r"trainer|tutor|instructor|teacher)\b", re.I)
CORE_LABELS = {"Java", "Spring Boot", "Spring", "Microservices", "AI agents", "LLM", "RAG", "Prompt engineering", "AI/ML"}


def score_fit(title: str, text: str) -> tuple[int, list[str], int]:
    """Return (total score, matched labels best-first, core score)."""
    body = text[:4000]
    hits, title_core = [], False
    for label, (rx, w) in STACK_RE.items():
        if rx.search(title):
            pts = 3 * w
            title_core = title_core or label in CORE_LABELS
        else:
            n = len(rx.findall(body))
            # one mention in a long job ad is often boilerplate; in a short post (e.g. Hacker News) it is signal
            pts = 0 if n == 0 else (w if n >= 2 or len(body) < 1500 else max(1, w // 2))
        if pts:
            hits.append((pts, label))
    hits.sort(reverse=True)
    if NOT_ENGINEERING_TITLE_RE.search(title) or (OFF_STACK_TITLE_RE.search(title) and not title_core):
        return 0, [], 0
    core = sum(p for p, lab in hits if lab in CORE_LABELS)
    return sum(p for p, _ in hits), [lab for _, lab in hits][:6], core


USD_RE = re.compile(r"(\$\s?\d|\busd\b|us\$|\bdollars?\b)", re.I)


def strip_html(s: str) -> str:
    s = re.sub(r"<\s*(br|/p|/li|/div)\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def find_email(text: str) -> str | None:
    m = EMAIL_RE.search(text or "")
    if m:
        return m.group(0).rstrip(".")
    m = OBFUSCATED_EMAIL_RE.search(text or "")
    if m:
        domain = re.sub(r"\s*[\[\(]?\s*dot\s*[\]\)]?\s*", ".", m.group(2), flags=re.I)
        return f"{m.group(1)}@{domain}"
    return None


def to_epoch(v) -> int | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(v / 1000) if v > 1e11 else int(v)
    s = str(v).strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def money(n) -> str:
    n = float(n)
    return f"{n/1000:g}k" if n >= 1000 else f"{n:g}"


def fix_mojibake(s: str) -> str:
    for enc in ("latin-1", "cp1252"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


def make_job(*, source, title, company, url, posted, text, job_type="", location="", pay="", usd=None,
             email=None, anywhere=None, freelance=None):
    title, company, text = fix_mojibake(title or ""), fix_mojibake(company or ""), fix_mojibake(text or "")
    fit, matched, core = score_fit(title, f"{job_type} {text}")
    blocked = find_blocker(f"{title} {text}", location)  # reason string, or None when nothing stops you applying
    if usd is None:
        usd = bool(USD_RE.search(pay or "")) or (not pay and bool(USD_RE.search(text)))
    if freelance is None:
        freelance = bool(FREELANCE_RE.search(f"{title} {job_type}")) or bool(FREELANCE_RE.search(text[:600]))
    if anywhere is None:
        loc_ok = not location or ANYWHERE_RE.search(location) is not None
        anywhere = loc_ok and not RESTRICTED_RE.search(f"{location} {text[:1500]}")
    return {
        "id": url, "source": source, "title": title.strip()[:160], "company": (company or "").strip()[:80],
        "url": url, "posted": posted, "type": job_type or ("Contract / freelance" if freelance else ""),
        "location": location, "pay": pay, "usd": bool(usd), "anywhere": bool(anywhere),
        "freelance": bool(freelance), "dev": core >= MIN_CORE, "fit": fit, "matched": matched, "blocked": blocked,
        "email": email or find_email(text),
        "snippet": re.sub(r"\s+", " ", text)[:260],
        "desc": re.sub(r"\n{3,}", "\n\n", text).strip()[:2400],
    }


def get(url: str, timeout: int = 25) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def get_json(url: str):
    return json.loads(get(url))


# --------------------------------------------------------------------------- sources
# RemoteOK was removed on purpose: its Apply button leads to a paid "Premium" page, which is exactly what this app avoids.
def src_remotive():
    out = []
    for j in get_json("https://remotive.com/api/remote-jobs?category=software-dev&limit=100").get("jobs", []):
        jt = (j.get("job_type") or "").replace("_", " ")
        out.append(make_job(
            source="Remotive", title=j.get("title", ""), company=j.get("company_name"), url=j.get("url"),
            posted=to_epoch(j.get("publication_date")), text=strip_html(j.get("description")),
            job_type=jt.title(), location=j.get("candidate_required_location") or "", pay=j.get("salary") or "",
            freelance=bool(re.search(r"contract|freelance|part", jt, re.I)) or None))
    return out


def src_jobicy():
    out = []
    for j in get_json("https://jobicy.com/api/v2/remote-jobs?count=100").get("jobs", []):
        jt = j.get("jobType") or []
        jt = ", ".join(jt) if isinstance(jt, list) else str(jt)
        lo, hi, cur = j.get("salaryMin") or j.get("annualSalaryMin"), j.get("salaryMax") or j.get("annualSalaryMax"), j.get("salaryCurrency")
        pay = f"{cur or ''} {money(lo)}-{money(hi)}".strip() if lo and hi else ""
        geo = j.get("jobGeo") or ""
        out.append(make_job(
            source="Jobicy", title=j.get("jobTitle", ""), company=j.get("companyName"), url=j.get("url"),
            posted=to_epoch(j.get("pubDate")), text=strip_html(j.get("jobDescription") or j.get("jobExcerpt")),
            job_type=jt, location=geo, pay=pay, usd=(cur == "USD") if pay else None))
    return out


def src_himalayas():
    out, cursor = [], None
    for _ in range(5):
        data = get_json("https://himalayas.app/jobs/api?limit=20" + (f"&cursor={urllib.parse.quote(str(cursor))}" if cursor else ""))
        for j in data.get("jobs", []):
            lo, hi, cur = j.get("minSalary"), j.get("maxSalary"), j.get("currency")
            pay = f"{cur} {money(lo)}-{money(hi)}/{(j.get('salaryPeriod') or 'yr')[:2]}" if lo and hi else ""
            restr = j.get("locationRestrictions") or []
            out.append(make_job(
                source="Himalayas", title=j.get("title", ""), company=j.get("companyName"),
                url=j.get("applicationLink") or j.get("guid"), posted=to_epoch(j.get("pubDate")),
                text=strip_html(j.get("description") or j.get("excerpt")), job_type=j.get("employmentType") or "",
                location=", ".join(restr) if restr else "Worldwide", pay=pay, usd=(cur == "USD") if pay else None,
                anywhere=not restr))
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return out


def src_weworkremotely():
    out = []
    for cat in ("remote-back-end-programming-jobs", "remote-devops-sysadmin-jobs", "remote-full-stack-programming-jobs"):
        root = ET.fromstring(get(f"https://weworkremotely.com/categories/{cat}.rss"))
        for it in root.iter("item"):
            raw_title = it.findtext("title") or ""
            company, _, title = raw_title.partition(":")
            region = it.findtext("region") or ""
            text = strip_html(it.findtext("description"))
            out.append(make_job(
                source="We Work Remotely", title=(title or raw_title), company=company if title else "",
                url=it.findtext("link"), posted=to_epoch(it.findtext("pubDate")), text=text,
                job_type=it.findtext("type") or "", location=region))
    return out


def _hn_latest(query, title_re):
    hits = get_json("https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=20&query=" + urllib.parse.quote(query))["hits"]
    for h in hits:
        if re.search(title_re, h["title"]):
            return h["objectID"]
    return None


def src_hn():
    """Hacker News monthly threads: 'Who is hiring?' (remote + contract) and 'Seeking freelancer'."""
    out = []
    for query, title_re, freelancer_thread in (
        ("Ask HN: Who is hiring?", r"^Ask HN: Who is hiring\? \(\w+ 20\d\d\)", False),
        ("Ask HN: Freelancer? Seeking freelancer?", r"^Ask HN: Freelancer\? Seeking freelancer\? \(\w+ 20\d\d\)", True),
    ):
        thread = _hn_latest(query, title_re)
        if not thread:
            continue
        for c in get_json(f"https://hn.algolia.com/api/v1/items/{thread}").get("children", []):
            text = strip_html(c.get("text"))
            if not text:
                continue
            head = text.split("\n")[0]
            if freelancer_thread and not re.search(r"seeking\s+freelancer", head, re.I):
                continue  # skip freelancers advertising themselves
            if not freelancer_thread and not re.search(r"remote", text[:600], re.I):
                continue
            parts = [p.strip() for p in head.split("|")]
            company = parts[0][:80]
            role = next((p for p in parts if re.search(
                r"engineer|developer|architect|\blead\b|\bhead\b|scientist|programmer|\bsre\b|devops|consultant|freelanc|contract", p, re.I)), None)
            title = role or (" | ".join(parts[1:3]) if len(parts) > 1 else head)
            pay = ""
            m = re.search(r"\$\s?\d[\d,\.]*\s?[kK]?(?:\s?[-–to]+\s?\$?\s?\d[\d,\.]*\s?[kK]?)?(?:\s?(?:/|per)\s?(?:hr|hour|h|yr|year|month))?", text)
            if m:
                pay = m.group(0).strip()
            out.append(make_job(
                source="HN Freelancer" if freelancer_thread else "HN Who's Hiring", title=title, company=company,
                url=f"https://news.ycombinator.com/item?id={c['id']}", posted=c.get("created_at_i"), text=text,
                pay=pay, freelance=True if freelancer_thread else None,
                # HN posts rarely state a location rule, so require positive wording instead of assuming "anywhere"
                anywhere=bool(ANYWHERE_RE.search(text[:1500])) and not RESTRICTED_RE.search(text[:1500])))
    return out


SOURCES = {
    "HN (Who's hiring + Freelancer)": src_hn,
    "We Work Remotely": src_weworkremotely,
    "Remotive": src_remotive,
    "Jobicy": src_jobicy,
    "Himalayas": src_himalayas,
}

# --------------------------------------------------------------------------- aggregation + cache
_cache = {"at": 0.0, "payload": None}


# Which job["source"] labels each source produces (used to reuse a source's previous results).
SOURCE_LABELS = {
    "HN (Who's hiring + Freelancer)": ["HN Who's Hiring", "HN Freelancer"],
    "We Work Remotely": ["We Work Remotely"],
    "Remotive": ["Remotive"],
    "Jobicy": ["Jobicy"],
    "Himalayas": ["Himalayas"],
}
# What happens when you press Apply (checked by following the real Apply buttons, 2026-10-18):
#   direct  - goes straight to the employer / contact details, no account
#   signup  - the board puts a sign-in / sign-up wall in front of Apply (We Work Remotely, Jobicy)
#   unknown - could not be verified (Himalayas sits behind a bot check)
APPLY_MODE = {"HN Who's Hiring": "direct", "HN Freelancer": "direct", "Remotive": "direct",
              "We Work Remotely": "signup", "Jobicy": "signup", "Himalayas": "unknown"}
# Bump when the job fields change, so old published rows are never mixed with new ones.
SCHEMA = 3
# Sources that ask for gentle use are only re-fetched this often (seconds); between fetches the previous results are reused.
SOURCE_TTL = {"Remotive": 6 * 3600}


def fetch_all(prev: dict | None = None) -> dict:
    """Fetch every source in parallel. `prev` is the previous payload (used for per-source timing and as a
    fallback when a source fails, so one outage never empties the page)."""
    prev = prev or {}
    if prev.get("schema") != SCHEMA:
        prev = {}  # data from an older format: refetch everything rather than reuse rows that lack new fields

    def previous_rows(name):
        return [j for j in prev.get("jobs", []) if j.get("source") in SOURCE_LABELS[name]]

    def previous_status(name):
        return next((s for s in prev.get("sources", []) if s.get("source") == name), None)

    def run(item):
        name, fn = item
        t0, ps = time.time(), previous_status(name)
        ttl = SOURCE_TTL.get(name)
        if ttl and ps and not ps.get("error") and t0 - ps.get("fetched_at", 0) < ttl and previous_rows(name):
            rows = previous_rows(name)
            return name, rows, None, 0.0, ps["fetched_at"], True
        try:
            return name, fn(), None, time.time() - t0, int(time.time()), False
        except Exception as e:  # one broken source must not break the page
            rows = previous_rows(name)
            note = f"{type(e).__name__}: {e}" + (" (showing last good data)" if rows else "")
            return name, rows, note, time.time() - t0, (ps or {}).get("fetched_at", 0) if rows else 0, bool(rows)

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
        results = list(ex.map(run, SOURCES.items()))
    jobs, seen, status = [], set(), []
    for name, rows, err, secs, fetched_at, cached in results:
        status.append({"source": name, "count": len(rows), "error": err, "seconds": round(secs, 1),
                       "fetched_at": fetched_at, "cached": cached})
        for j in rows:
            # strip tracking params, but keep the query on HN links: their post id lives in it (item?id=123)
            raw = (j["url"] or "").lower()
            key = raw if "news.ycombinator.com" in raw else raw.split("?")[0]
            key = key or (j["company"] + j["title"]).lower()
            if j["url"] and key not in seen:
                seen.add(key)
                j["apply"] = APPLY_MODE.get(j["source"], "unknown")
                jobs.append(j)
    # The same job is often listed on several boards under different URLs: keep the copy that is easiest to apply to.
    def dupe_key(j):
        c, t = re.sub(r"\W+", "", j["company"].lower()), re.sub(r"\W+", "", j["title"].lower())
        return (c, t) if c and t else None  # posts without a clear company/title are never merged

    order, best = {"direct": 0, "unknown": 1, "signup": 2}, {}
    for j in jobs:
        k = dupe_key(j)
        if k and (k not in best or order[j["apply"]] < order[best[k]["apply"]]):
            best[k] = j
    jobs = [j for j in jobs if dupe_key(j) is None or best[dupe_key(j)] is j]
    jobs.sort(key=lambda j: j["posted"] or 0, reverse=True)
    return {"schema": SCHEMA, "fetched_at": int(time.time()), "jobs": jobs, "sources": status}


def get_payload(force: bool = False) -> dict:
    if force or not _cache["payload"] or time.time() - _cache["at"] > CACHE_SECONDS:
        _cache["payload"], _cache["at"] = fetch_all(), time.time()
    return _cache["payload"]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/jobs":
            body = json.dumps(get_payload("refresh=1" in (url.query or ""))).encode()
            self._send(200, "application/json", body)
        elif url.path == "/":
            self._send(200, "text/html; charset=utf-8", (Path(__file__).parent / "index.html").read_bytes())
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def export(path: str, prev_path: str | None) -> None:
    """Static mode for GitHub Pages: fetch everything once and write it to a JSON file."""
    prev = None
    if prev_path and Path(prev_path).is_file():
        try:
            prev = json.loads(Path(prev_path).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            prev = None  # a corrupt/partial previous file just means a full fetch
    payload = fetch_all(prev)
    ok = [s for s in payload["sources"] if not s["error"]]
    if not payload["jobs"] or not ok:
        raise SystemExit("every source failed - not overwriting the published data")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for s in payload["sources"]:
        print(f"{s['source']}: {s['count']} jobs" + (" (cached)" if s["cached"] and not s["error"] else "") + (f"  ERROR {s['error']}" if s["error"] else ""))
    print(f"wrote {len(payload['jobs'])} jobs to {out}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Freelance Finder")
    ap.add_argument("--export", metavar="FILE", help="write jobs to FILE as JSON and exit (used by the GitHub Action)")
    ap.add_argument("--prev", metavar="FILE", help="previous jobs.json, used for per-source timing and fallbacks")
    args = ap.parse_args()
    if args.export:
        export(args.export, args.prev)
    else:
        print(f"Freelance Finder running at http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
