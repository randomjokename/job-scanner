#!/bin/bash
# Run the job scanner and push updates to GitHub

cd "$(dirname "$0")"

echo "Running job scanner..."
python3 job_scanner.py

echo ""
echo "Pushing to GitHub..."
git add index.html seen_jobs.json
git commit -m "Update job listings $(date '+%Y-%m-%d %H:%M')"
git push

echo ""
echo "Done! Site will update in ~1 minute."
