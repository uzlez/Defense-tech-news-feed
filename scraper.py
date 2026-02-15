#!/usr/bin/env python3
"""
Defense Tech × Capital Signals — News Feed Scraper
Fetches from Google News RSS across multiple broad queries,
deduplicates, scores for relevance, and outputs a static HTML page.
"""

import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import html
import re
import json
import hashlib
from datetime import datetime, timedelta, timezone
from collections import OrderedDict

# ─────────────────────────────────────────────────────────────
# CONFIGURATION — Edit these to tune your feed
# ─────────────────────────────────────────────────────────────

MAX_ITEMS = 15
MAX_AGE_DAYS = 60

# Multiple broad queries to cast a wide net.
# Google News RSS supports OR, quotes, etc.
QUERIES = [
    # ── Defense Tech VC / Investment ──
    '"defense tech" investment',
    '"defense tech" funding OR venture OR VC',
    '"defense startup" funding',
    'defense technology venture capital',
    'military tech startup funding',
    'dual-use technology investment',
    'defense tech Series A OR Series B OR seed',

    # ── UAV / Drones ──
    'military drone OR UAV investment',
    'drone defense company funding',
    'UAV startup OR drone startup funding',
    'agricultural drone AND (military OR defense OR dual-use)',
    'counter-drone OR counter-UAS technology',
    'FPV drone defense',

    # ── Ukraine Defense Tech ──
    'Ukraine defense tech',
    'Ukraine drone technology',
    'Ukraine defense startup',
    'Ukraine military technology investment',
    'Ukraine defense industry',

    # ── EU / European Defense ──
    'European defense investment',
    'EU defense industrial strategy',
    'European defense fund',
    'European defense tech startup',
    'NATO defense technology investment',
    'AUKUS defense technology',
    'UK defense tech',
    'EDIRPA OR EDIP defense',

    # ── Export Controls & Regulations ──
    'ITAR export control defense',
    'defense export regulations',
    'dual-use export control',
    'defense procurement reform',
    'EAR export administration regulations defense',

    # ── Autonomy / AI in Defense ──
    'autonomous weapons technology',
    'AI defense military',
    'defense AI startup',
    'military autonomy technology',

    # ── Space / Satellite Defense ──
    'defense space technology investment',
    'military satellite startup',

    # ── Broader Defense Industry ──
    'defense contractor startup OR scaleup',
    'defense innovation unit',
    'defense accelerator',
    'DARPA startup',
]

# Tags assigned based on keyword matching in title
TAG_RULES = {
    'UAV / Drones': [
        'drone', 'uav', 'uas', 'fpv', 'unmanned', 'counter-drone',
        'counter-uas', 'quadcopter', 'vtol',
    ],
    'Investment': [
        'invest', 'funding', 'series a', 'series b', 'seed', 'raise',
        'venture', 'capital', 'vc ', 'round', 'valuation', 'ipo',
        'spac', 'acquisition', 'merger', 'm&a',
    ],
    'Ukraine': [
        'ukraine', 'ukrainian', 'kyiv', 'kiev', 'zelensk',
    ],
    'EU Policy': [
        'european', 'eu ', 'nato', 'edirpa', 'edip', 'aukus',
        'brussels', 'european commission', 'european defense',
        'european defence',
    ],
    'UK': [
        'uk ', 'united kingdom', 'british', 'mod ', 'dstl',
        'ministry of defence', 'london',
    ],
    'US': [
        'pentagon', 'dod ', 'department of defense', 'darpa',
        'diu ', 'congress', 'us military', 'us defense', 'us defence',
        'american defense',
    ],
    'Regulation': [
        'itar', 'ear ', 'export control', 'regulation', 'procurement',
        'compliance', 'sanction', 'embargo', 'dual-use',
    ],
    'AI / Autonomy': [
        'artificial intelligence', ' ai ', 'autonomous', 'autonomy',
        'machine learning', 'computer vision',
    ],
    'Space': [
        'satellite', 'space', 'orbit', 'launch',
    ],
    'Startup': [
        'startup', 'start-up', 'scaleup', 'scale-up', 'accelerator',
        'incubator',
    ],
}

# Sources to deprioritize (content farms, press release aggregators)
LOW_QUALITY_SOURCES = [
    'yahoo.com', 'msn.com',
]

# ─────────────────────────────────────────────────────────────
# SCRAPER
# ─────────────────────────────────────────────────────────────

def fetch_google_news_rss(query):
    """Fetch articles from Google News RSS for a given query."""
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; DefenseFeedBot/1.0)'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        return data
    except Exception as e:
        print(f"  ⚠ Failed to fetch '{query}': {e}")
        return None


def parse_rss(xml_data):
    """Parse RSS XML and return list of article dicts."""
    if not xml_data:
        return []
    
    articles = []
    try:
        root = ET.fromstring(xml_data)
        for item in root.findall('.//item'):
            title = item.findtext('title', '').strip()
            link = item.findtext('link', '').strip()
            pub_date_str = item.findtext('pubDate', '').strip()
            source = item.findtext('source', '').strip()
            description = item.findtext('description', '').strip()
            
            # Parse date
            pub_date = None
            if pub_date_str:
                try:
                    pub_date = datetime.strptime(
                        pub_date_str, '%a, %d %b %Y %H:%M:%S %Z'
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    try:
                        pub_date = datetime.strptime(
                            pub_date_str, '%a, %d %b %Y %H:%M:%S %z'
                        )
                    except ValueError:
                        pass
            
            if title and link:
                articles.append({
                    'title': html.unescape(title),
                    'link': link,
                    'date': pub_date,
                    'source': html.unescape(source) if source else extract_domain(link),
                    'description': html.unescape(re.sub('<[^<]+?>', '', description)),
                })
    except ET.ParseError as e:
        print(f"  ⚠ XML parse error: {e}")
    
    return articles


def extract_domain(url):
    """Extract a readable domain from a URL."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '')
        return domain
    except Exception:
        return ''


def deduplicate(articles):
    """Remove duplicate articles based on normalized title similarity."""
    seen = OrderedDict()
    for a in articles:
        # Normalize: lowercase, strip punctuation, collapse whitespace
        norm = re.sub(r'[^a-z0-9 ]', '', a['title'].lower())
        norm = re.sub(r'\s+', ' ', norm).strip()
        
        # Use first 60 chars as dedup key (catches reposts with minor diffs)
        key = norm[:60]
        
        if key not in seen:
            seen[key] = a
        else:
            # Keep the one with the better source
            existing = seen[key]
            if is_better_source(a, existing):
                seen[key] = a
    
    return list(seen.values())


def is_better_source(new, existing):
    """Prefer higher-quality sources."""
    for domain in LOW_QUALITY_SOURCES:
        if domain in (existing.get('source', '') + existing.get('link', '')).lower():
            return True
    return False


def assign_tags(article):
    """Assign topic tags based on title keywords."""
    title_lower = article['title'].lower()
    desc_lower = article.get('description', '').lower()
    text = title_lower + ' ' + desc_lower
    
    tags = []
    for tag, keywords in TAG_RULES.items():
        for kw in keywords:
            if kw.lower() in text:
                tags.append(tag)
                break
    
    return tags if tags else ['Defense Tech']


def score_article(article, tags):
    """Score article for relevance ranking. Higher = more relevant."""
    score = 0
    
    # Recency bonus (newer = better)
    if article['date']:
        age_days = (datetime.now(timezone.utc) - article['date']).days
        score += max(0, 60 - age_days)  # up to 60 points for brand new
    
    # Tag diversity bonus
    score += len(tags) * 8
    
    # Title keyword density
    title_lower = article['title'].lower()
    high_value_terms = [
        'defense tech', 'defence tech', 'drone', 'uav', 'ukraine',
        'funding', 'investment', 'series', 'venture', 'startup',
        'itar', 'export', 'european defense', 'eu defense',
        'dual-use', 'autonomous', 'counter-drone',
    ]
    for term in high_value_terms:
        if term in title_lower:
            score += 5
    
    # Source quality bonus
    premium_sources = [
        'reuters', 'bloomberg', 'financial times', 'defense one',
        'defensenews', 'janes', 'breaking defense', 'the war zone',
        'techcrunch', 'sifted', 'pitchbook', 'crunchbase',
        'business wire', 'globenewswire', 'defense post',
    ]
    source_lower = article.get('source', '').lower()
    link_lower = article.get('link', '').lower()
    for ps in premium_sources:
        if ps in source_lower or ps in link_lower:
            score += 10
            break
    
    # Penalize low-quality sources
    for domain in LOW_QUALITY_SOURCES:
        if domain in source_lower or domain in link_lower:
            score -= 15
            break
    
    return score


def filter_by_date(articles, max_age_days):
    """Filter articles to only include those within max_age_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    filtered = []
    for a in articles:
        if a['date'] is None:
            filtered.append(a)  # keep undated ones, they're usually recent
        elif a['date'] >= cutoff:
            filtered.append(a)
    return filtered


def format_date(dt):
    """Format date as relative time or absolute date."""
    if not dt:
        return 'Recently'
    
    now = datetime.now(timezone.utc)
    diff = now - dt
    
    if diff.days == 0:
        hours = diff.seconds // 3600
        if hours == 0:
            return 'Just now'
        return f'{hours}h ago'
    elif diff.days == 1:
        return 'Yesterday'
    elif diff.days < 7:
        return f'{diff.days}d ago'
    elif diff.days < 30:
        weeks = diff.days // 7
        return f'{weeks}w ago'
    else:
        return dt.strftime('%b %d')


def generate_html(articles, generated_at):
    """Generate the static HTML page."""
    
    items_html = ''
    for i, (article, tags, score) in enumerate(articles):
        tags_html = ''.join(
            f'<span class="tag">{t}</span>' for t in tags
        )
        
        date_str = format_date(article['date'])
        source = article.get('source', 'Unknown')
        
        items_html += f'''
        <a href="{html.escape(article['link'])}" target="_blank" rel="noopener" class="item" style="animation-delay: {i * 0.04}s">
            <div class="item-header">
                <span class="source">{html.escape(source)}</span>
                <span class="date">{date_str}</span>
            </div>
            <h3 class="item-title">{html.escape(article['title'])}</h3>
            <div class="tags">{tags_html}</div>
        </a>'''
    
    updated_str = generated_at.strftime('%b %d, %H:%M UTC')
    
    page_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Defense Tech × Capital Signals</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&family=JetBrains+Mono:wght@400;500&display=swap');

  :root {{
    --bg: #0a0a0b;
    --surface: #111113;
    --surface-hover: #18181b;
    --border: #1e1e22;
    --border-hover: #2a2a30;
    --text-primary: #e4e4e7;
    --text-secondary: #71717a;
    --text-muted: #52525b;
    --accent: #c8aa6e;
    --accent-dim: rgba(200, 170, 110, 0.08);
    --accent-border: rgba(200, 170, 110, 0.15);
    --tag-bg: #1a1a1f;
    --tag-text: #8b8b96;
  }}

  * {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
  }}

  body {{
    font-family: 'DM Sans', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text-primary);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }}

  .container {{
    max-width: 680px;
    margin: 0 auto;
    padding: 28px 20px 40px;
  }}

  .header {{
    margin-bottom: 24px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }}

  .header-title {{
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--accent);
    text-transform: uppercase;
    margin-bottom: 8px;
  }}

  .header-meta {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: var(--text-muted);
    letter-spacing: 0.02em;
  }}

  .header-meta span {{
    margin-right: 12px;
  }}

  .geo-tags {{
    display: flex;
    gap: 6px;
    margin-top: 12px;
    flex-wrap: wrap;
  }}

  .geo-tag {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-secondary);
    background: var(--accent-dim);
    border: 1px solid var(--accent-border);
    padding: 3px 8px;
    border-radius: 3px;
  }}

  .feed {{
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}

  .item {{
    display: block;
    text-decoration: none;
    color: inherit;
    padding: 14px 16px;
    border-radius: 8px;
    border: 1px solid transparent;
    transition: all 0.15s ease;
    animation: fadeIn 0.3s ease forwards;
    opacity: 0;
  }}

  .item:hover {{
    background: var(--surface);
    border-color: var(--border);
  }}

  .item:hover .item-title {{
    color: #fff;
  }}

  .item-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }}

  .source {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    font-weight: 500;
    color: var(--accent);
    letter-spacing: 0.02em;
    opacity: 0.85;
  }}

  .date {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    color: var(--text-muted);
    letter-spacing: 0.02em;
  }}

  .item-title {{
    font-size: 14px;
    font-weight: 500;
    line-height: 1.45;
    color: var(--text-primary);
    transition: color 0.15s ease;
    margin-bottom: 8px;
  }}

  .tags {{
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }}

  .tag {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 9.5px;
    font-weight: 400;
    letter-spacing: 0.03em;
    color: var(--tag-text);
    background: var(--tag-bg);
    padding: 2px 7px;
    border-radius: 3px;
    border: 1px solid var(--border);
  }}

  .empty {{
    text-align: center;
    padding: 60px 20px;
    color: var(--text-muted);
    font-size: 13px;
  }}

  @keyframes fadeIn {{
    from {{ opacity: 0; transform: translateY(6px); }}
    to {{ opacity: 1; transform: translateY(0); }}
  }}

  /* Notion embed optimization */
  @media (max-width: 500px) {{
    .container {{ padding: 16px 12px 32px; }}
    .item {{ padding: 12px; }}
    .item-title {{ font-size: 13px; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="header-title">Defense Tech × Capital Signals</div>
    <div class="header-meta">
      <span>Last {MAX_AGE_DAYS} days</span>
      <span>·</span>
      <span>{len(articles)} items</span>
      <span>·</span>
      <span>updated {updated_str}</span>
    </div>
    <div class="geo-tags">
      <span class="geo-tag">UK</span>
      <span class="geo-tag">EU</span>
      <span class="geo-tag">US</span>
      <span class="geo-tag">Ukraine</span>
    </div>
  </div>

  <div class="feed">
    {items_html if items_html else '<div class="empty">No articles found in the last ' + str(MAX_AGE_DAYS) + ' days.</div>'}
  </div>
</div>
</body>
</html>'''
    
    return page_html


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print(f"🔍 Defense Tech × Capital Signals Scraper")
    print(f"   Queries: {len(QUERIES)}")
    print(f"   Max items: {MAX_ITEMS}")
    print(f"   Max age: {MAX_AGE_DAYS} days")
    print()
    
    all_articles = []
    
    for i, query in enumerate(QUERIES):
        print(f"  [{i+1}/{len(QUERIES)}] Fetching: {query}")
        xml_data = fetch_google_news_rss(query)
        articles = parse_rss(xml_data)
        print(f"         → {len(articles)} results")
        all_articles.extend(articles)
    
    print(f"\n📊 Total raw articles: {len(all_articles)}")
    
    # Filter by date
    all_articles = filter_by_date(all_articles, MAX_AGE_DAYS)
    print(f"   After date filter: {len(all_articles)}")
    
    # Deduplicate
    all_articles = deduplicate(all_articles)
    print(f"   After dedup: {len(all_articles)}")
    
    # Score and tag
    scored = []
    for a in all_articles:
        tags = assign_tags(a)
        score = score_article(a, tags)
        scored.append((a, tags, score))
    
    # Sort by score descending
    scored.sort(key=lambda x: x[2], reverse=True)
    
    # Take top N
    top = scored[:MAX_ITEMS]
    
    # Re-sort top items by date (newest first) for display
    top.sort(key=lambda x: x[0]['date'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    
    print(f"   Final items: {len(top)}")
    
    if top:
        print(f"\n📰 Top articles:")
        for a, tags, score in top:
            print(f"   [{score:3d}] {a['title'][:80]}...")
            print(f"         {', '.join(tags)} | {a['source']} | {format_date(a['date'])}")
    
    # Generate HTML
    now = datetime.now(timezone.utc)
    html_content = generate_html(top, now)
    
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"\n✅ Generated index.html ({len(html_content)} bytes)")


if __name__ == '__main__':
    main()
