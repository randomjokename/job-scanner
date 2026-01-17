#!/usr/bin/env python3
"""
Job Scanner - Searches for phlebotomist positions in Oakland, CA
Tracks seen jobs and generates an HTML report of new postings.
"""

import json
import os
from datetime import datetime, timedelta
from html import escape

import requests

import config


SEEN_HOURS = 48  # Jobs reappear after this many hours


def load_seen_jobs():
    """Load seen job IDs from JSON, filtering out jobs older than SEEN_HOURS."""
    if os.path.exists(config.SEEN_JOBS_FILE):
        with open(config.SEEN_JOBS_FILE, "r") as f:
            data = json.load(f)
            seen_jobs = data.get("seen_jobs", {})

            # Filter to only jobs seen within the last SEEN_HOURS
            cutoff = datetime.now() - timedelta(hours=SEEN_HOURS)
            recent_seen = {}
            for job_id, timestamp in seen_jobs.items():
                try:
                    seen_time = datetime.fromisoformat(timestamp)
                    if seen_time > cutoff:
                        recent_seen[job_id] = timestamp
                except ValueError:
                    continue

            return recent_seen
    return {}


def save_seen_jobs(seen_jobs, new_count):
    """Save seen job IDs with timestamps to JSON file."""
    data = {
        "seen_jobs": seen_jobs,
        "last_checked": datetime.now().isoformat(),
        "jobs_found_last_run": new_count
    }
    with open(config.SEEN_JOBS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_jobs_adzuna():
    """Fetch job listings from Adzuna API."""
    url = "https://api.adzuna.com/v1/api/jobs/us/search/1"

    params = {
        "app_id": config.ADZUNA_APP_ID,
        "app_key": config.ADZUNA_APP_KEY,
        "what": config.SEARCH_QUERY,
        "where": config.SEARCH_LOCATION,
        "results_per_page": config.RESULTS_PER_PAGE,
        "sort_by": "date",
        "content-type": "application/json"
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("results", []), "adzuna"
    except requests.RequestException as e:
        print(f"Error fetching jobs from Adzuna: {e}")
        return [], None


def fetch_jobs_jooble():
    """Fetch job listings from Jooble API (backup source)."""
    url = f"https://jooble.org/api/{config.JOOBLE_API_KEY}"

    payload = {
        "keywords": config.SEARCH_QUERY,
        "location": config.SEARCH_LOCATION,
        "page": 1
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        jobs = data.get("jobs", [])
        # Normalize Jooble format to match Adzuna structure
        normalized = []
        for job in jobs:
            normalized.append({
                "id": f"jooble_{job.get('id', hash(job.get('link', '')))}",
                "title": job.get("title", "Unknown Title"),
                "company": {"display_name": job.get("company", "Unknown Company")},
                "location": {"display_name": job.get("location", "Oakland, CA")},
                "description": job.get("snippet", ""),
                "redirect_url": job.get("link", "#"),
                "created": job.get("updated", ""),
                "salary_min": None,
                "salary_max": None,
                "source": "jooble"
            })
        return normalized, "jooble"
    except requests.RequestException as e:
        print(f"Error fetching jobs from Jooble: {e}")
        return [], None


def filter_new_jobs(jobs, seen_ids):
    """Filter out jobs that have already been seen."""
    new_jobs = []
    for job in jobs:
        job_id = job.get("id")
        if job_id and str(job_id) not in seen_ids:
            new_jobs.append(job)
    return new_jobs


def format_salary(job):
    """Format salary information if available."""
    min_sal = job.get("salary_min")
    max_sal = job.get("salary_max")

    if min_sal and max_sal:
        return f"${min_sal:,.0f} - ${max_sal:,.0f}"
    elif min_sal:
        return f"From ${min_sal:,.0f}"
    elif max_sal:
        return f"Up to ${max_sal:,.0f}"
    return "Not specified"


def generate_html_report(jobs):
    """Generate an HTML report of job listings."""
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Phlebotomist Jobs - Oakland, CA</title>
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
            color: #333;
        }}
        header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
        }}
        header h1 {{
            margin: 0 0 10px 0;
        }}
        header p {{
            margin: 0;
            opacity: 0.9;
        }}
        .stats {{
            background: white;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .job-card {{
            background: white;
            border-radius: 10px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .job-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
        .job-title {{
            font-size: 1.3em;
            margin: 0 0 10px 0;
        }}
        .job-title a {{
            color: #667eea;
            text-decoration: none;
        }}
        .job-title a:hover {{
            text-decoration: underline;
        }}
        .job-company {{
            font-size: 1.1em;
            color: #555;
            margin-bottom: 10px;
        }}
        .job-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            margin-bottom: 15px;
            font-size: 0.9em;
            color: #666;
        }}
        .job-meta span {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        .job-description {{
            color: #555;
            font-size: 0.95em;
            border-top: 1px solid #eee;
            padding-top: 15px;
            margin-top: 10px;
        }}
        .no-jobs {{
            text-align: center;
            padding: 50px;
            background: white;
            border-radius: 10px;
            color: #666;
        }}
        .salary {{
            color: #2e7d32;
            font-weight: 500;
        }}
    </style>
</head>
<body>
    <header>
        <h1>Phlebotomist Jobs</h1>
        <p>Oakland, CA Area - Updated {timestamp}</p>
    </header>

    <div class="stats">
        <strong>{len(jobs)}</strong> new job{'' if len(jobs) == 1 else 's'} found
    </div>
"""

    if not jobs:
        html += """
    <div class="no-jobs">
        <h2>No New Jobs Found</h2>
        <p>Check back later for new postings!</p>
    </div>
"""
    else:
        for job in jobs:
            title = escape(job.get("title", "Unknown Title"))
            company = escape(job.get("company", {}).get("display_name", "Unknown Company"))
            location = escape(job.get("location", {}).get("display_name", "Oakland, CA"))
            url = job.get("redirect_url", "#")
            description = escape(job.get("description", "No description available.")[:300])
            if len(job.get("description", "")) > 300:
                description += "..."
            salary = format_salary(job)
            created = job.get("created", "")
            if created:
                try:
                    date_obj = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    posted = date_obj.strftime("%b %d, %Y")
                except ValueError:
                    posted = "Recently"
            else:
                posted = "Recently"

            html += f"""
    <div class="job-card">
        <h2 class="job-title"><a href="{url}" target="_blank">{title}</a></h2>
        <div class="job-company">{company}</div>
        <div class="job-meta">
            <span>📍 {location}</span>
            <span class="salary">💰 {salary}</span>
            <span>📅 Posted: {posted}</span>
        </div>
        <div class="job-description">{description}</div>
    </div>
"""

    html += """
</body>
</html>
"""

    with open(config.HTML_REPORT_FILE, "w") as f:
        f.write(html)

    return config.HTML_REPORT_FILE


def main():
    print(f"Job Scanner - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Searching for: {config.SEARCH_QUERY} in {config.SEARCH_LOCATION}")
    print("-" * 50)

    # Check for placeholder API credentials
    if config.ADZUNA_APP_ID == "your_app_id_here":
        print("ERROR: Please configure your Adzuna API credentials in config.py")
        print("Sign up for free at: https://developer.adzuna.com/")
        return

    # Load previously seen jobs
    seen_ids = load_seen_jobs()
    print(f"Previously seen jobs: {len(seen_ids)}")

    # Fetch current jobs from APIs
    print("Fetching jobs from Adzuna...")
    all_jobs, source = fetch_jobs_adzuna()
    print(f"Jobs from Adzuna: {len(all_jobs)}")

    # Try Jooble as backup if Adzuna fails or returns no results
    jooble_configured = hasattr(config, 'JOOBLE_API_KEY') and config.JOOBLE_API_KEY != "your_jooble_api_key_here"
    if len(all_jobs) == 0 and jooble_configured:
        print("Trying Jooble as backup...")
        jooble_jobs, jooble_source = fetch_jobs_jooble()
        print(f"Jobs from Jooble: {len(jooble_jobs)}")
        all_jobs.extend(jooble_jobs)
    elif jooble_configured:
        # Also fetch from Jooble to get more results
        print("Also fetching from Jooble...")
        jooble_jobs, _ = fetch_jobs_jooble()
        print(f"Jobs from Jooble: {len(jooble_jobs)}")
        all_jobs.extend(jooble_jobs)

    print(f"Total jobs from all sources: {len(all_jobs)}")

    # Filter to only new jobs
    new_jobs = filter_new_jobs(all_jobs, seen_ids)
    print(f"New jobs found: {len(new_jobs)}")

    # Update seen jobs with timestamps for all current job IDs
    now = datetime.now().isoformat()
    for job in all_jobs:
        job_id = job.get("id")
        if job_id:
            seen_ids[str(job_id)] = now

    save_seen_jobs(seen_ids, len(new_jobs))

    # Generate HTML report
    report_path = generate_html_report(new_jobs)
    print(f"\nHTML report saved to: {report_path}")

    # Print summary of new jobs to console
    if new_jobs:
        print(f"\n{'='*50}")
        print("NEW JOB POSTINGS:")
        print('='*50)
        for job in new_jobs:
            print(f"\n• {job.get('title', 'Unknown')}")
            print(f"  Company: {job.get('company', {}).get('display_name', 'Unknown')}")
            print(f"  Location: {job.get('location', {}).get('display_name', 'N/A')}")
    else:
        print("\nNo new jobs since last check.")


if __name__ == "__main__":
    main()
