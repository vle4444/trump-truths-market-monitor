#!/usr/bin/env python3
"""
TruthSocial RSS Scraper with Claude Analysis
Monitors Donald Trump's posts and analyzes them for market impact using Anthropic Claude.
Emits structured JSON analyses for downstream consumption (e.g. dashboard).
"""

import feedparser
import time
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import re
import os
import httpx
from dotenv import load_dotenv
from anthropic import Anthropic

CLAUDE_MODEL = "claude-sonnet-4-6"

# Topic-dedup window: a new post matching an existing topic_signature within
# this many hours gets folded under the existing parent record.
DEDUP_WINDOW_HOURS = 12

# Severity rank — used for clamping link_share severity and for UI sorting.
SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

SYSTEM_PROMPT = """You are an expert financial markets analyst.
Analyze the following social media post for potential market impact across major asset classes.

You will receive a `post_kind` hint along with the post body. Use it to calibrate severity:
- `post_kind=original`  — Trump's own first-person words on TruthSocial. Severity is uncapped; calibrate to actual content. Original first-person commitments are the highest-priority signal in this feed.
- `post_kind=link_share` — Trump shared a media headline / URL with little or no original commentary. You may be given the fetched article body for context. The information is public and likely already priced in. **Severity must not exceed `low`.** Use `none` unless the article reveals an imminent, concrete, not-yet-priced-in policy commitment.

Respond with ONLY a valid JSON object — no markdown fences, no preamble — matching this exact schema:

{
  "has_market_impact": <boolean>,
  "severity": "none" | "low" | "medium" | "high" | "critical",
  "topic_signature": "<short-dash-separated-slug capturing the news cycle, e.g. 'iran-strait-hormuz' or 'china-tariffs-semis'. Use 'none' for posts with no market signal.>",
  "summary": "<1-2 sentence high-level take>",
  "equities":    {"direction": "positive|negative|neutral|uncertain", "sectors":    [<string>], "reasoning": "<string>"},
  "commodities": {"direction": "positive|negative|neutral|uncertain", "items":      [<string>], "reasoning": "<string>"},
  "fx":          {"direction": "positive|negative|neutral|uncertain", "currencies": [<string>], "reasoning": "<string>"},
  "bonds":       {"direction": "positive|negative|neutral|uncertain", "reasoning":  "<string>"},
  "crypto":      {"direction": "positive|negative|neutral|uncertain", "reasoning":  "<string>"}
}

Severity calibration:
- "none"     — no market signal at all (personal grievance, social commentary, reshared old news).
- "low"      — vague rhetoric or already-priced-in information.
- "medium"   — concrete first-person policy direction with plausible near-term impact, but lacking specifics on timing/magnitude.
- "high"     — clear, original, near-term, large-magnitude policy / trade / regulatory commitment with named parties and specific actions.
- "critical" — Trump making a LIVE first-person commitment with specific dollar/percentage figures, named entities, AND a date or "effective immediately" trigger. Reserved for top-of-the-feed material: tariff announcements with rates, sanctions packages with targets, troop deployment orders with timelines, executive orders explicitly committed to. Must be `post_kind=original`. Reshared headlines NEVER qualify.

Topic signature rules:
- Use 3–5 lowercase dash-separated keywords that capture the news cycle, not the specific post wording.
- Example: a post about "Trump orders strikes on Iranian shipping in Hormuz" and a follow-up "Iran's response on Strait shipping" should BOTH produce signature "iran-strait-hormuz".
- Use the same signature when the underlying event is the same, even if the angle differs.
- Use exactly "none" (no dashes) for posts with no market signal — these will not be clustered.

Rules:
- "direction" must be exactly one of the four lowercase values.
- If an asset class is unaffected, use "neutral" with empty arrays and reasoning "No direct or indirect link.".
- Be skeptical: most posts have no real market signal. Default to lower severity when in doubt.
"""

# Load environment variables
load_dotenv('.env')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('truthsocial_analyzer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TruthSocialAnalyzer:
    def __init__(self, rss_url: str = "https://trumpstruth.org/feed"):
        self.rss_url = rss_url
        self.seen_posts = set()  # Track posts we've already seen
        self.posts_file = 'seen_posts_analyzer.json'
        self.analysis_file = 'post_analyses.json'
        
        # Initialize Claude client
        self.claude_client: Optional[Anthropic] = None
        self.model = CLAUDE_MODEL
        self.init_claude()

        # Load existing data
        self.load_seen_posts()
        self.load_analyses()

    def init_claude(self):
        """Initialize Anthropic Claude client (reads ANTHROPIC_API_KEY from env)."""
        if not os.getenv('ANTHROPIC_API_KEY'):
            logger.error("ANTHROPIC_API_KEY not found in environment variables")
            logger.error("Please set ANTHROPIC_API_KEY in your .env file")
            return

        try:
            self.claude_client = Anthropic()
            logger.info(f"Claude client initialized (model={self.model})")
        except Exception as e:
            logger.error(f"Failed to initialize Claude client: {e}")

    def load_seen_posts(self):
        """Load previously seen posts from file"""
        try:
            with open(self.posts_file, 'r') as f:
                data = json.load(f)
                self.seen_posts = set(data.get('seen_posts', []))
                logger.info(f"Loaded {len(self.seen_posts)} previously seen posts")
        except FileNotFoundError:
            logger.info("No previous posts file found, starting fresh")
        except Exception as e:
            logger.error(f"Error loading seen posts: {e}")
    
    def save_seen_posts(self):
        """Save seen posts to file"""
        try:
            data = {
                'seen_posts': list(self.seen_posts),
                'last_updated': datetime.now().isoformat()
            }
            with open(self.posts_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving seen posts: {e}")
    
    def load_analyses(self):
        """Load previous analyses from file"""
        try:
            with open(self.analysis_file, 'r') as f:
                self.analyses = json.load(f)
                logger.info(f"Loaded {len(self.analyses)} previous analyses")
        except FileNotFoundError:
            self.analyses = []
            logger.info("No previous analyses file found, starting fresh")
        except Exception as e:
            logger.error(f"Error loading analyses: {e}")
            self.analyses = []
    
    def save_analyses(self):
        """Save analyses to file"""
        try:
            with open(self.analysis_file, 'w') as f:
                json.dump(self.analyses, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving analyses: {e}")
    
    def fetch_rss_feed(self) -> Optional[feedparser.FeedParserDict]:
        """Fetch and parse the RSS feed"""
        try:
            logger.info(f"Fetching RSS feed from {self.rss_url}")
            feed = feedparser.parse(self.rss_url)
            
            if feed.bozo:
                logger.warning(f"RSS feed has parsing issues: {feed.bozo_exception}")
            
            logger.info(f"Successfully fetched RSS feed with {len(feed.entries)} entries")
            return feed
            
        except Exception as e:
            logger.error(f"Error fetching RSS feed: {e}")
            return None
    
    def extract_post_data(self, entry) -> Optional[Dict]:
        """Extract data from an RSS entry"""
        try:
            # Extract post ID from the link
            post_id = None
            if hasattr(entry, 'link'):
                # Extract ID from URL like https://trumpstruth.org/statuses/33307
                match = re.search(r'/statuses/(\d+)', entry.link)
                if match:
                    post_id = match.group(1)
                else:
                    # Fallback to hash of content
                    post_id = str(hash(entry.title + entry.description))
            
            # Extract post text
            post_text = ""
            if hasattr(entry, 'description'):
                # Clean up the description (remove CDATA tags and HTML)
                post_text = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', entry.description)
                post_text = re.sub(r'<[^>]+>', '', post_text)  # Remove HTML tags
                post_text = post_text.strip()
            
            # Extract timestamp
            timestamp = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                timestamp = datetime(*entry.published_parsed[:6]).isoformat()
            elif hasattr(entry, 'published'):
                timestamp = entry.published
            
            # Extract URL
            post_url = entry.link if hasattr(entry, 'link') else None
            
            if post_text and len(post_text) > 10:
                return {
                    'id': post_id,
                    'text': post_text,
                    'timestamp': timestamp,
                    'url': post_url,
                    'scraped_at': datetime.now().isoformat()
                }
        
        except Exception as e:
            logger.error(f"Error extracting post data: {e}")
        
        return None
    
    @staticmethod
    def classify_post_kind(post_content: str) -> str:
        """Classify a TruthSocial post as original / link_share.

        link_share: post contains a URL AND the non-URL text is short enough
                    (<= 25 words) that it looks like a media headline reshare
                    rather than original commentary.
        original:   everything else, including short punchy first-person posts
                    (Trump's policy declarations are often terse).
        """
        text = post_content.strip()
        url_match = re.search(r'https?://\S+', text)
        text_without_urls = re.sub(r'https?://\S+', '', text).strip()
        word_count = len(text_without_urls.split())

        if url_match and word_count <= 25:
            return "link_share"
        return "original"

    @staticmethod
    def extract_first_url(post_content: str) -> Optional[str]:
        m = re.search(r'https?://\S+', post_content)
        return m.group(0).rstrip('.,);]') if m else None

    @staticmethod
    def fetch_article_content(url: str, max_chars: int = 4000) -> Optional[str]:
        """Best-effort fetch + strip of an article body. Returns text or None.

        Polite UA, 5s timeout, naive HTML strip. Truncates to max_chars to
        keep token cost bounded. Never raises — failure returns None.
        """
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; TrumpTruths-Analyzer/1.0)"}
            with httpx.Client(timeout=5.0, follow_redirects=True, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            logger.warning(f"Article fetch failed for {url}: {e}")
            return None

        # Strip <script>/<style> blocks first
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Strip remaining tags
        text = re.sub(r'<[^>]+>', ' ', html)
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            return None
        return text[:max_chars]

    def analyze_post_with_claude(self, post_content: str) -> Optional[Dict]:
        """Analyze post using Claude. Returns parsed JSON dict or None on failure."""
        if not self.claude_client:
            logger.error("Claude client not initialized")
            return None

        post_kind = self.classify_post_kind(post_content)

        # For link_share posts, fetch the article body so Claude has context
        # beyond just the headline.
        article_block = ""
        if post_kind == "link_share":
            url = self.extract_first_url(post_content)
            if url:
                article = self.fetch_article_content(url)
                if article:
                    article_block = f"\n\n--- Fetched article body (truncated to 4k chars) ---\n{article}\n--- end article ---"
                    logger.info(f"Fetched article body ({len(article)} chars) from {url}")

        user_message = f"post_kind={post_kind}\n\n{post_content}{article_block}"

        try:
            logger.info(f"Analyzing post with Claude (kind={post_kind})...")
            response = self.claude_client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text.strip()

            # Defensive: strip accidental code fences if the model adds them
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            parsed = json.loads(text)

            # Clamp link_share severity to "low" defensively even if Claude
            # ignored the prompt. Original posts are uncapped.
            if post_kind == "link_share":
                sev = parsed.get("severity", "none")
                if SEVERITY_RANK.get(sev, 0) > SEVERITY_RANK["low"]:
                    logger.info(f"Clamping link_share severity {sev} -> low")
                    parsed["severity"] = "low"

            parsed["post_kind"] = post_kind
            logger.info(
                f"Claude analysis completed (severity={parsed.get('severity', '?')}, "
                f"topic={parsed.get('topic_signature', '?')})"
            )
            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"Claude returned non-JSON response: {e}")
            logger.error(f"Raw response: {text[:500]}")
            return None
        except Exception as e:
            logger.error(f"Claude analysis failed: {e}")
            return None

    @staticmethod
    def normalize_topic(sig: Optional[str]) -> Optional[str]:
        """Normalize a topic signature for matching: lowercase, sorted tokens."""
        if not sig or sig == "none":
            return None
        tokens = sorted(t for t in re.split(r'[-_\s]+', sig.lower()) if t)
        return "-".join(tokens) if tokens else None

    def find_existing_cluster(self, topic_sig: Optional[str]) -> Optional[int]:
        """Find the index of an existing analysis with the same topic within
        the dedup window. Returns the index or None."""
        norm_new = self.normalize_topic(topic_sig)
        if not norm_new:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)

        for i, record in enumerate(self.analyses):
            existing_sig = (record.get("analysis") or {}).get("topic_signature")
            if self.normalize_topic(existing_sig) != norm_new:
                continue
            # Check recency
            try:
                analyzed_at = record.get("analyzed_at", "")
                # Tolerate both naive and tz-aware ISO strings
                ts = datetime.fromisoformat(analyzed_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    return i
            except (ValueError, TypeError):
                continue
        return None
    
    def check_for_new_posts(self) -> List[Dict]:
        """Check for new posts and return any new ones found"""
        feed = self.fetch_rss_feed()
        if not feed:
            return []
        
        new_posts = []
        
        for entry in feed.entries:
            post_data = self.extract_post_data(entry)
            if post_data and post_data['id'] not in self.seen_posts:
                new_posts.append(post_data)
                self.seen_posts.add(post_data['id'])
                logger.info(f"New post found: {post_data['text'][:100]}...")
        
        if new_posts:
            self.save_seen_posts()
        
        return new_posts
    
    def run_analyzer(self, interval: int = 30):
        """Run the analyzer continuously"""
        logger.info(f"Starting TruthSocial Analyzer")
        logger.info(f"RSS Feed: {self.rss_url}")
        logger.info(f"Checking for new posts every {interval} seconds")
        
        try:
            while True:
                new_posts = self.check_for_new_posts()
                
                if new_posts:
                    logger.info(f"Found {len(new_posts)} new post(s)")
                    for post in new_posts:
                        self.handle_new_post(post)
                else:
                    logger.info("No new posts found")
                
                logger.info(f"Waiting {interval} seconds before next check...")
                time.sleep(interval)
                
        except KeyboardInterrupt:
            logger.info("Analyzer stopped by user")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            self.save_seen_posts()
            self.save_analyses()
    
    def handle_new_post(self, post: Dict):
        """Handle a new post - analyze, cluster by topic, persist."""
        print(f"\n{'='*80}")
        print(f"NEW POST FROM @realDonaldTrump")
        print(f"{'='*80}")
        print(f"Time:    {post.get('timestamp', 'Unknown')}")
        print(f"Post ID: {post['id']}")
        print(f"URL:     {post['url']}")
        print(f"{'='*80}")
        print(post['text'][:500] + ("..." if len(post['text']) > 500 else ""))
        print(f"{'='*80}")

        analysis = self.analyze_post_with_claude(post['text'])

        if not analysis:
            print(f"Analysis failed")
            logger.info(f"New post processed (analysis failed) - ID: {post['id']}")
            return

        topic_sig = analysis.get("topic_signature")
        cluster_idx = self.find_existing_cluster(topic_sig)
        analyzed_at = datetime.now(timezone.utc).isoformat()

        if cluster_idx is not None:
            # Fold this post into an existing cluster.
            parent = self.analyses[cluster_idx]
            related = parent.setdefault("related_posts", [])
            related.append({
                "post_id": post["id"],
                "timestamp": post.get("timestamp"),
                "url": post.get("url"),
                "content": post["text"],
                "analyzed_at": analyzed_at,
                "analysis": analysis,
            })
            # Bump parent's analyzed_at so it floats to top of feed.
            parent["analyzed_at"] = analyzed_at
            # Escalate severity if the new post is more severe than parent's.
            new_sev = analysis.get("severity", "none")
            old_sev = (parent.get("analysis") or {}).get("severity", "none")
            if SEVERITY_RANK.get(new_sev, 0) > SEVERITY_RANK.get(old_sev, 0):
                parent.setdefault("analysis", {})["severity"] = new_sev
                logger.info(f"Cluster severity escalated {old_sev} -> {new_sev} (post {post['id']})")
            print(f"Folded into existing cluster topic={topic_sig} ({len(related)} related)")
            logger.info(
                f"Post {post['id']} merged into cluster topic={topic_sig} "
                f"(parent post_id={parent.get('post_id')}, related count={len(related)})"
            )
        else:
            # New cluster (or non-clusterable post — topic_signature='none').
            record = {
                "post_id": post["id"],
                "timestamp": post.get("timestamp"),
                "url": post.get("url"),
                "content": post["text"],
                "analyzed_at": analyzed_at,
                "model": self.model,
                "analysis": analysis,
                "related_posts": [],
            }
            self.analyses.append(record)
            print(f"New record stored (severity={analysis.get('severity')}, topic={topic_sig})")
            logger.info(f"Post {post['id']} stored as new record topic={topic_sig}")

        self.save_analyses()
        print(f"Total posts seen: {len(self.seen_posts)} | clusters: {len(self.analyses)}")
        print(f"{'='*80}\n")
    
def main():
    """Main function to run the analyzer"""
    analyzer = TruthSocialAnalyzer()

    print("\n" + "="*50)
    print("Starting continuous monitoring...")
    print("Press Ctrl+C to stop")
    print("="*50)

    analyzer.run_analyzer(interval=30)

if __name__ == "__main__":
    main()
