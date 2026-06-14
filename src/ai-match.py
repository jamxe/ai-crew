#!/usr/bin/env python3
"""Match a query against CodeWhale sessions from stdin (codewhale sessions output).
Format: [*] session_id | title... | N msgs | YYYY-MM-DD HH:MM UTC (X ago)
Prints the best matching session ID to stdout, match info to stderr.
Exit 0 on match, 1 on no match.
"""
import sys, re

def main():
    if len(sys.argv) < 2:
        print("Usage: ai-match.py <query>", file=sys.stderr)
        sys.exit(1)

    q = sys.argv[1].lower()
    # Strip noise words (from SeaShell v2.1.2 learnings)
    noise = r'\b(called|named|the|our|my|session|project|repo|coding|continue|with|work|on)\b'
    q_clean = re.sub(noise, '', q).strip()
    q_words = [w for w in q_clean.split() if len(w) >= 3]

    scored = []
    for line in sys.stdin:
        m = re.match(
            r'\s*\*?\s*([a-f0-9-]+)\s*\|\s*(.+?)\s*\|\s*\d+\s*msgs?\s*\|',
            line.strip()
        )
        if not m:
            continue
        sid, title = m.group(1), m.group(2).strip().lower()
        score = 0

        # Exact substring
        if q in title:
            score += 100
        if q_clean and q_clean in title:
            score += 90
        # ID match
        if q in sid:
            score += 50
        # Token-level: meaningful words appearing in title
        title_words = set(title.split())
        for w in q_words:
            if w in title_words:
                score += 30
            elif any(tw.startswith(w) for tw in title_words):
                score += 15

        if score > 0:
            scored.append((score, sid, title[:80]))

    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        best = scored[0]
        print(best[1])
        print(f"  Match: {best[2]} (score={best[0]})", file=sys.stderr)
        sys.exit(0)
    sys.exit(1)

if __name__ == "__main__":
    main()
