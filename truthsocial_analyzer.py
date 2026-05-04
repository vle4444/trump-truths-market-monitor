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
from datetime import datetime
from typing import List, Dict, Optional
import re
import os
from dotenv import load_dotenv
from anthropic import Anthropic

CLAUDE_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an expert financial markets analyst.
Analyze the following social media post for potential market impact across major asset classes.

You will receive a `post_kind` hint along with the post body. Use it to calibrate severity:
- `post_kind=original`  — Trump's own first-person words. Severity is uncapped; calibrate to actual content.
- `post_kind=link_share` — Trump shared a media headline / URL with little or no original commentary. The information is already public and most likely already priced in. **Severity must not exceed `low`.** Use `none` unless the underlying news represents an imminent, concrete policy commitment that markets have not yet absorbed.

Respond with ONLY a valid JSON object — no markdown fences, no preamble — matching this exact schema:

{
  "has_market_impact": <boolean>,
  "severity": "none" | "low" | "medium" | "high",
  "summary": "<1-2 sentence high-level take>",
  "equities":    {"direction": "positive|negative|neutral|uncertain", "sectors":    [<string>], "reasoning": "<string>"},
  "commodities": {"direction": "positive|negative|neutral|uncertain", "items":      [<string>], "reasoning": "<string>"},
  "fx":          {"direction": "positive|negative|neutral|uncertain", "currencies": [<string>], "reasoning": "<string>"},
  "bonds":       {"direction": "positive|negative|neutral|uncertain", "reasoning":  "<string>"},
  "crypto":      {"direction": "positive|negative|neutral|uncertain", "reasoning":  "<string>"}
}

Severity calibration:
- "none"   — no market signal at all (personal grievance, social commentary, reshared old news).
- "low"    — vague rhetoric or already-priced-in information.
- "medium" — concrete first-person policy direction with plausible near-term impact, but lacking specifics on timing/magnitude.
- "high"   — clear, original, near-term, large-magnitude policy / trade / regulatory commitment. Reserved for first-person announcements with specific actions ("I am ordering X", "Effective Monday Y will happen", concrete tariff %, named sanctions, troop movements with dates, etc.). Reshared headlines never qualify.

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

    def analyze_post_with_claude(self, post_content: str) -> Optional[Dict]:
        """Analyze post using Claude. Returns parsed JSON dict or None on failure."""
        if not self.claude_client:
            logger.error("Claude client not initialized")
            return None

        post_kind = self.classify_post_kind(post_content)
        user_message = f"post_kind={post_kind}\n\n{post_content}"

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
            logger.info(f"Claude analysis completed (severity={parsed.get('severity', '?')})")
            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"Claude returned non-JSON response: {e}")
            logger.error(f"Raw response: {text[:500]}")
            return None
        except Exception as e:
            logger.error(f"Claude analysis failed: {e}")
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
        """Handle a new post - analyze and display"""
        print(f"\n{'='*80}")
        print(f"🆕 NEW POST FROM @realDonaldTrump")
        print(f"{'='*80}")
        print(f"📅 Time: {post.get('timestamp', 'Unknown')}")
        print(f"🆔 Post ID: {post['id']}")
        print(f"🔗 URL: {post['url']}")
        print(f"⏰ Scraped: {post['scraped_at']}")
        print(f"{'='*80}")
        print(f"📝 CONTENT:")
        print(f"{'='*80}")
        print(post['text'])
        print(f"{'='*80}")
        
        # Analyze with Claude
        analysis = self.analyze_post_with_claude(post['text'])

        if analysis:
            print(f"🤖 CLAUDE MARKET ANALYSIS:")
            print(f"{'='*80}")
            print(json.dumps(analysis, indent=2))
            print(f"{'='*80}")

            # Save analysis (Phase 3 schema)
            analysis_data = {
                'post_id': post['id'],
                'timestamp': post.get('timestamp'),
                'url': post.get('url'),
                'content': post['text'],
                'analyzed_at': datetime.now().isoformat(),
                'model': self.model,
                'analysis': analysis,
            }
            self.analyses.append(analysis_data)
            self.save_analyses()
        else:
            print(f"❌ Analysis failed")
        
        print(f"📊 Total posts seen: {len(self.seen_posts)}")
        print(f"📊 Total analyses: {len(self.analyses)}")
        print(f"{'='*80}\n")
        
        # Log the new post
        logger.info(f"New post processed - ID: {post['id']}, Length: {len(post['text'])} chars")
    
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
