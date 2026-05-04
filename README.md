# TruthSocial RSS Scraper & Analyzer

A Python bot that automatically monitors new posts from Donald Trump's TruthSocial account using the RSS feed from [trumpstruth.org](https://trumpstruth.org/feed) and analyzes them for market impact using Anthropic Claude.

## 🎯 Features

### RSS Scraper
- ✅ **RSS-based**: Uses the official RSS feed - no authentication required
- ✅ **Real posts**: Gets actual posts from @realDonaldTrump (not ads)
- ✅ **Automatic deduplication**: Tracks seen posts using real TruthSocial post IDs
- ✅ **Configurable interval**: Default 30 seconds, easily adjustable
- ✅ **Comprehensive logging**: File and console logging
- ✅ **Persistent storage**: Remembers seen posts between runs
- ✅ **Error handling**: Robust error handling and recovery
- ✅ **Lightweight**: Only requires `feedparser` library

### Market Analyzer
- 🤖 **Claude Analysis**: Each new post is sent to Anthropic Claude for market impact analysis
- 📊 **Structured JSON output**: Direction, severity, summary, and per-asset reasoning
- 🎯 **Multi-Asset Coverage**: Equities, Commodities, FX, Bonds, Crypto
- 📈 **Severity tiering**: `none` / `low` / `medium` / `high`
- 💾 **Analysis Storage**: All analyses saved to `post_analyses.json` for downstream consumption (e.g. dashboard)

## 🚀 Quick Start

### 1. Setup
```bash
# Activate virtual environment
source venv/bin/activate    # macOS/Linux
venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure APIs
```bash
# Create .env file with your Anthropic API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

### 3. Run the tools
```bash
# Run basic RSS scraper (no analysis)
python rss_scraper.py

# Run analyzer with Claude market analysis
python truthsocial_analyzer.py

# Or use startup scripts
python run_scraper.py          # Basic scraper
python run_analyzer.py         # With Claude analysis
```

### 4. Test first
```bash
# Test the RSS feed
python run_scraper.py --test

# Ping Claude with a sample post and print the parsed JSON
python run_analyzer.py --test
```

## 🖥️ Dashboard

A local web dashboard renders the analyses in real time (severity filters, asset-class filters, detail panel, auto-refresh every 15s).

```bash
# In a second terminal, start the dashboard
uvicorn dashboard.app:app --port 8000 --reload
```

Then open `http://localhost:8000`.

The analyzer must be running separately (in another terminal) to populate `post_analyses.json`. The dashboard reads from that file; the two processes are decoupled.

API endpoints exposed:

- `GET /api/posts` — array of analysis records, newest first
- `GET /api/stats` — totals, severity counts, last-analyzed timestamp

## 📁 Files

- `rss_scraper.py` - **Basic RSS scraper** (posts only)
- `truthsocial_analyzer.py` - **Enhanced analyzer** (posts + Claude analysis)
- `run_scraper.py` - Startup script for basic scraper
- `run_analyzer.py` - Startup script for analyzer
- `requirements.txt` - Python dependencies
- `seen_posts_rss.json` - Persistent storage of seen posts
- `post_analyses.json` - Stored Claude analyses (structured JSON, one record per post)
- `truthsocial_rss_scraper.log` - Basic scraper logs
- `truthsocial_analyzer.log` - Analyzer logs
- `README.md` - This documentation

## 🔧 Usage Options

### Basic RSS Scraper
```bash
python run_scraper.py
python run_scraper.py --interval 60
python run_scraper.py --test
```

### Market Analyzer
```bash
python run_analyzer.py
python run_analyzer.py --interval 60
python run_analyzer.py --test
```

## 📊 How It Works

### RSS Scraper
1. **RSS Feed**: Fetches posts from `https://trumpstruth.org/feed`
2. **Post Extraction**: Parses RSS entries to extract post data
3. **Deduplication**: Uses real TruthSocial post IDs (e.g., 33307, 33306)
4. **Persistence**: Saves seen posts to avoid duplicates
5. **Monitoring**: Checks for new posts every 30 seconds

### Market Analyzer
1. **RSS Monitoring**: Same as basic scraper
2. **Claude Analysis**: Sends each new post to Claude with a structured-JSON system prompt
3. **Storage**: Appends a record to `post_analyses.json` containing the post + parsed analysis dict + model name

## 📝 Sample Output

### Stored record (`post_analyses.json`)
```json
{
  "post_id": "33307",
  "timestamp": "2025-10-14T05:36:58",
  "url": "https://trumpstruth.org/statuses/33307",
  "content": "Time Magazine wrote a relatively good story about me...",
  "analyzed_at": "2025-10-14T18:04:35.123456",
  "model": "claude-sonnet-4-6",
  "analysis": {
    "has_market_impact": false,
    "severity": "none",
    "summary": "Personal grievance about a magazine cover. No market relevance.",
    "equities":    {"direction": "neutral", "sectors": [], "reasoning": "No direct or indirect link."},
    "commodities": {"direction": "neutral", "items": [], "reasoning": "No direct or indirect link."},
    "fx":          {"direction": "neutral", "currencies": [], "reasoning": "No direct or indirect link."},
    "bonds":       {"direction": "neutral", "reasoning": "No direct or indirect link."},
    "crypto":      {"direction": "neutral", "reasoning": "No direct or indirect link."}
  }
}
```

## ⚙️ Customization

### Basic Scraper
Edit `rss_scraper.py`:
1. **Change interval**: Modify `interval` parameter in `run_scraper()`
2. **Custom post handling**: Modify `handle_new_post()` method

### Market Analyzer
Edit `truthsocial_analyzer.py`:
1. **Modify analysis prompt**: Change `SYSTEM_PROMPT` at the top of the module
2. **Change Claude model**: Modify the `CLAUDE_MODEL` constant (see [docs.claude.com/en/docs/about-claude/models/overview](https://docs.claude.com/en/docs/about-claude/models/overview) for current model IDs)
3. **Extend the JSON schema**: Update the prompt and downstream consumers together

## 📋 Requirements

- Python 3.9+
- `feedparser` library
- `anthropic` library (for analyzer)
- `python-dotenv` library (for analyzer)
- Internet connection
- Anthropic API key (for analyzer)

## ⚠️ Important Notes

- **Anthropic API Key**: Required for market analysis - set `ANTHROPIC_API_KEY` in `.env`
- **API Costs**: Each analyzed post is one Claude API call - monitor usage
- **Terms of Service**: Ensure compliance with TruthSocial's terms of service
- **Rate Limiting**: The 30-second interval is respectful
- **Legal Compliance**: Always respect robots.txt and terms of service
- **RSS Source**: Uses the public RSS feed from trumpstruth.org

## 🐛 Troubleshooting

### Basic Scraper Issues
1. **Check internet connection**
2. **Verify RSS feed**: Visit https://trumpstruth.org/feed
3. **Check logs**: Look at `truthsocial_rss_scraper.log`

### Analyzer Issues
1. **Anthropic API Key**: Ensure `ANTHROPIC_API_KEY` is set in `.env`
2. **Model name**: If you see "model not found" errors, confirm the current ID at [docs.claude.com/en/docs/about-claude/models/overview](https://docs.claude.com/en/docs/about-claude/models/overview)
3. **Non-JSON responses**: The analyzer logs the raw response and skips storage; check logs for malformed output
4. **Check logs**: Look at `truthsocial_analyzer.log`

## 🎉 Success!

Monitors Donald Trump's TruthSocial posts every 30 seconds and emits structured market-impact analyses via Claude. 🚀
