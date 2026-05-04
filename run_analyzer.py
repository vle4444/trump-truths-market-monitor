#!/usr/bin/env python3
"""
Startup script for TruthSocial Analyzer
Easy way to run the analyzer with different options
"""

import sys
import os
import json
import argparse
from dotenv import load_dotenv

# Load environment variables
load_dotenv('.env')

SAMPLE_POST = (
    "Time Magazine wrote a relatively good story about me, but the picture may be "
    "the Worst of All Time. They \"disappeared\" my hair, and then had something "
    "floating on top of my head that looked like a floating crown, but an extremely "
    "small one. Really weird! I never liked taking pictures from underneath angles, "
    "but this is a super bad picture, and deserves to be called out. What are they "
    "doing, and why?"
)


def main():
    parser = argparse.ArgumentParser(description='TruthSocial Analyzer')
    parser.add_argument('--interval', type=int, default=30,
                       help='Analysis interval in seconds (default: 30)')
    parser.add_argument('--test', action='store_true',
                       help='Run a single Claude analysis on a sample post and exit')

    args = parser.parse_args()

    print("TruthSocial Analyzer")
    print("=" * 50)
    print(f"Interval: {args.interval} seconds")
    print(f"Mode: {'Test' if args.test else 'Continuous'}")
    print("=" * 50)

    if not os.getenv('ANTHROPIC_API_KEY'):
        print("❌ ERROR: ANTHROPIC_API_KEY not found in environment")
        print("Please set ANTHROPIC_API_KEY in your .env file")
        return

    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from truthsocial_analyzer import TruthSocialAnalyzer

    analyzer = TruthSocialAnalyzer()

    if args.test:
        print("\n🧪 Pinging Claude with sample post...")
        print(f"Post: {SAMPLE_POST[:100]}...")
        result = analyzer.analyze_post_with_claude(SAMPLE_POST)
        if result is None:
            print("❌ Claude analysis failed — see logs above")
            sys.exit(1)
        print("\n✅ Parsed JSON response:")
        print(json.dumps(result, indent=2))
        return

    analyzer.run_analyzer(interval=args.interval)


if __name__ == "__main__":
    main()
