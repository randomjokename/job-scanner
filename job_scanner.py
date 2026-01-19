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


CACHE_DAYS = 30  # Keep job data for this many days


def load_cached_jobs():
    """Load cached job data from JSON, filtering out jobs older than CACHE_DAYS."""
    if os.path.exists(config.SEEN_JOBS_FILE):
        with open(config.SEEN_JOBS_FILE, "r") as f:
            data = json.load(f)
            # Support new format (cached_jobs with full data) or start fresh
            # Old format (seen_jobs with just timestamps) is ignored - we'll rebuild
            cached_jobs = data.get("cached_jobs", {})

            # Filter to only jobs from the last CACHE_DAYS
            cutoff = datetime.now() - timedelta(days=CACHE_DAYS)
            recent_jobs = {}
            for job_id, job_data in cached_jobs.items():
                try:
                    # Check the job's created date or when we first saw it
                    created = job_data.get("created", "")
                    if created:
                        created = created.replace("Z", "+00:00")
                        job_date = datetime.fromisoformat(created).replace(tzinfo=None)
                    else:
                        # Fall back to when we first cached it
                        first_seen = job_data.get("_first_seen", "")
                        if first_seen:
                            job_date = datetime.fromisoformat(first_seen)
                        else:
                            job_date = datetime.now()  # Keep if no date info

                    if job_date >= cutoff:
                        recent_jobs[job_id] = job_data
                except ValueError:
                    recent_jobs[job_id] = job_data  # Keep on parse error
            return recent_jobs
    return {}


def save_cached_jobs(cached_jobs, new_count):
    """Save cached job data to JSON file."""
    data = {
        "cached_jobs": cached_jobs,
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


def fetch_jobs_usajobs():
    """Fetch job listings from USAJobs API (federal/government jobs)."""
    url = "https://data.usajobs.gov/api/search"

    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": config.USAJOBS_EMAIL,
        "Authorization-Key": config.USAJOBS_API_KEY
    }

    params = {
        "Keyword": config.SEARCH_QUERY,
        "LocationName": config.SEARCH_LOCATION,
        "ResultsPerPage": 50
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        items = data.get("SearchResult", {}).get("SearchResultItems", [])

        # Normalize USAJobs format to match Adzuna structure
        normalized = []
        for item in items:
            job = item.get("MatchedObjectDescriptor", {})
            position = job.get("PositionLocation", [{}])[0] if job.get("PositionLocation") else {}
            salary_min = None
            salary_max = None
            salary_range = job.get("PositionRemuneration", [{}])[0] if job.get("PositionRemuneration") else {}
            if salary_range:
                try:
                    salary_min = float(salary_range.get("MinimumRange", 0))
                    salary_max = float(salary_range.get("MaximumRange", 0))
                except (ValueError, TypeError):
                    pass

            normalized.append({
                "id": f"usajobs_{job.get('PositionID', '')}",
                "title": job.get("PositionTitle", "Unknown Title"),
                "company": {"display_name": job.get("OrganizationName", "Federal Government")},
                "location": {"display_name": position.get("LocationName", "Oakland, CA")},
                "description": job.get("UserArea", {}).get("Details", {}).get("JobSummary", ""),
                "redirect_url": job.get("PositionURI", "#"),
                "created": job.get("PublicationStartDate", ""),
                "salary_min": salary_min,
                "salary_max": salary_max,
                "source": "usajobs"
            })
        return normalized, "usajobs"
    except requests.RequestException as e:
        print(f"Error fetching jobs from USAJobs: {e}")
        return [], None


def filter_recent_jobs(jobs, days=30):
    """Filter to only jobs posted within the last N days."""
    cutoff = datetime.now() - timedelta(days=days)
    recent_jobs = []
    for job in jobs:
        created = job.get("created", "")
        if not created:
            recent_jobs.append(job)  # Keep jobs without dates
            continue
        try:
            # Handle various date formats
            created = created.replace("Z", "+00:00")
            job_date = datetime.fromisoformat(created).replace(tzinfo=None)
            if job_date >= cutoff:
                recent_jobs.append(job)
        except ValueError:
            recent_jobs.append(job)  # Keep jobs with unparseable dates
    return recent_jobs


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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: 'Roboto', -apple-system, BlinkMacSystemFont, sans-serif;
            line-height: 1.6;
            background-color: #f8f9fa;
            color: #333;
        }}
        .top-bar {{
            background: #003c71;
            padding: 8px 0;
        }}
        .top-bar-content {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 0 24px;
            text-align: right;
        }}
        .top-bar span {{
            color: #fff;
            font-size: 13px;
            opacity: 0.9;
        }}
        header {{
            background: #fff;
            border-bottom: 1px solid #e0e0e0;
            padding: 20px 0;
        }}
        .header-content {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 0 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .logo {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .logo-icon {{
            width: 44px;
            height: 44px;
            background: linear-gradient(135deg, #0077b6 0%, #00a8e8 100%);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .logo-icon svg {{
            width: 26px;
            height: 26px;
            fill: #fff;
        }}
        .logo h1 {{
            font-size: 22px;
            font-weight: 500;
            color: #003c71;
        }}
        .logo h1 span {{
            color: #0077b6;
        }}
        .header-info {{
            text-align: right;
            color: #666;
            font-size: 14px;
        }}
        .hero {{
            background: linear-gradient(135deg, #0077b6 0%, #003c71 100%);
            padding: 48px 0;
            color: #fff;
        }}
        .hero-content {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 0 24px;
        }}
        .hero h2 {{
            font-size: 32px;
            font-weight: 300;
            margin-bottom: 8px;
        }}
        .hero p {{
            font-size: 16px;
            opacity: 0.9;
        }}
        .stats-bar {{
            background: #fff;
            border-bottom: 1px solid #e0e0e0;
            padding: 16px 0;
        }}
        .stats-content {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 0 24px;
            display: flex;
            gap: 32px;
        }}
        .stat {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .stat-number {{
            font-size: 28px;
            font-weight: 700;
            color: #0077b6;
        }}
        .stat-label {{
            font-size: 14px;
            color: #666;
        }}
        main {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 32px 24px;
        }}
        .job-list {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}
        .job-card {{
            background: #fff;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 24px;
            transition: box-shadow 0.2s, border-color 0.2s;
        }}
        .job-card:hover {{
            border-color: #0077b6;
            box-shadow: 0 4px 12px rgba(0,119,182,0.1);
        }}
        .job-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 12px;
        }}
        .job-title {{
            font-size: 18px;
            font-weight: 500;
            margin: 0;
        }}
        .job-title a {{
            color: #003c71;
            text-decoration: none;
        }}
        .job-title a:hover {{
            color: #0077b6;
            text-decoration: underline;
        }}
        .job-date {{
            font-size: 13px;
            color: #666;
            white-space: nowrap;
        }}
        .job-company {{
            font-size: 15px;
            color: #0077b6;
            font-weight: 500;
            margin-bottom: 12px;
        }}
        .job-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            margin-bottom: 16px;
        }}
        .job-meta-item {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 14px;
            color: #555;
        }}
        .job-meta-item svg {{
            width: 16px;
            height: 16px;
            fill: #666;
        }}
        .job-meta-item.salary {{
            color: #2e7d32;
            font-weight: 500;
        }}
        .job-meta-item.salary svg {{
            fill: #2e7d32;
        }}
        .job-description {{
            font-size: 14px;
            color: #666;
            line-height: 1.7;
            padding-top: 16px;
            border-top: 1px solid #f0f0f0;
        }}
        .apply-btn {{
            display: inline-block;
            margin-top: 16px;
            padding: 10px 24px;
            background: #0077b6;
            color: #fff;
            text-decoration: none;
            border-radius: 4px;
            font-size: 14px;
            font-weight: 500;
            transition: background 0.2s;
        }}
        .apply-btn:hover {{
            background: #005f8f;
        }}
        .no-jobs {{
            text-align: center;
            padding: 64px 24px;
            background: #fff;
            border-radius: 8px;
            border: 1px solid #e0e0e0;
        }}
        .no-jobs h3 {{
            font-size: 20px;
            color: #333;
            margin-bottom: 8px;
        }}
        .no-jobs p {{
            color: #666;
        }}
        footer {{
            background: #003c71;
            color: #fff;
            padding: 32px 0;
            margin-top: 48px;
        }}
        .footer-content {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 0 24px;
            text-align: center;
            font-size: 14px;
            opacity: 0.8;
        }}
        @media (max-width: 640px) {{
            .header-content {{
                flex-direction: column;
                gap: 12px;
                text-align: center;
            }}
            .header-info {{
                text-align: center;
            }}
            .hero h2 {{
                font-size: 24px;
            }}
            .stats-content {{
                flex-direction: column;
                gap: 16px;
            }}
            .job-header {{
                flex-direction: column;
                gap: 8px;
            }}
        }}
    </style>
</head>
<body>
    <div class="top-bar">
        <div class="top-bar-content">
            <span>Oakland, CA Area Healthcare Careers</span>
        </div>
    </div>

    <header>
        <div class="header-content">
            <div class="logo">
                <div class="logo-icon">
                    <svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 3c1.93 0 3.5 1.57 3.5 3.5S13.93 13 12 13s-3.5-1.57-3.5-3.5S10.07 6 12 6zm7 13H5v-.23c0-.62.28-1.2.76-1.58C7.47 15.82 9.64 15 12 15s4.53.82 6.24 2.19c.48.38.76.97.76 1.58V19z"/></svg>
                </div>
                <h1>Phlebotomy<span>Careers</span></h1>
            </div>
            <div class="header-info">
                Updated {timestamp}
            </div>
        </div>
    </header>

    <div class="hero">
        <div class="hero-content">
            <h2>Phlebotomist Positions</h2>
            <p>Find your next opportunity in healthcare</p>
        </div>
    </div>

    <div class="stats-bar">
        <div class="stats-content">
            <div class="stat">
                <span class="stat-number">{len(jobs)}</span>
                <span class="stat-label">Available<br>Positions</span>
            </div>
            <div class="stat">
                <span class="stat-number">30</span>
                <span class="stat-label">Day<br>Listings</span>
            </div>
        </div>
    </div>

    <main>
        <div class="job-list">
"""

    if not jobs:
        html += """
            <div class="no-jobs">
                <h3>No Positions Available</h3>
                <p>Check back soon for new opportunities in your area.</p>
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
                <div class="job-header">
                    <h3 class="job-title"><a href="{url}" target="_blank">{title}</a></h3>
                    <span class="job-date">{posted}</span>
                </div>
                <div class="job-company">{company}</div>
                <div class="job-meta">
                    <span class="job-meta-item">
                        <svg viewBox="0 0 24 24"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>
                        {location}
                    </span>
                    <span class="job-meta-item salary">
                        <svg viewBox="0 0 24 24"><path d="M11.8 10.9c-2.27-.59-3-1.2-3-2.15 0-1.09 1.01-1.85 2.7-1.85 1.78 0 2.44.85 2.5 2.1h2.21c-.07-1.72-1.12-3.3-3.21-3.81V3h-3v2.16c-1.94.42-3.5 1.68-3.5 3.61 0 2.31 1.91 3.46 4.7 4.13 2.5.6 3 1.48 3 2.41 0 .69-.49 1.79-2.7 1.79-2.06 0-2.87-.92-2.98-2.1h-2.2c.12 2.19 1.76 3.42 3.68 3.83V21h3v-2.15c1.95-.37 3.5-1.5 3.5-3.55 0-2.84-2.43-3.81-4.7-4.4z"/></svg>
                        {salary}
                    </span>
                </div>
                <div class="job-description">{description}</div>
                <a href="{url}" target="_blank" class="apply-btn">View Position</a>
            </div>
"""

    html += """
        </div>
    </main>

    <footer>
        <div class="footer-content">
            Job listings refreshed daily. Data sourced from multiple job boards.
        </div>
    </footer>
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

    # Load previously cached jobs (full job data, not just IDs)
    cached_jobs = load_cached_jobs()
    print(f"Previously cached jobs: {len(cached_jobs)}")

    # Fetch current jobs from APIs
    print("Fetching jobs from Adzuna...")
    api_jobs, source = fetch_jobs_adzuna()
    print(f"Jobs from Adzuna: {len(api_jobs)}")

    # Try Jooble if configured
    jooble_configured = hasattr(config, 'JOOBLE_API_KEY') and config.JOOBLE_API_KEY != "your_jooble_api_key_here"
    if jooble_configured:
        print("Fetching from Jooble...")
        jooble_jobs, _ = fetch_jobs_jooble()
        print(f"Jobs from Jooble: {len(jooble_jobs)}")
        api_jobs.extend(jooble_jobs)

    # Fetch from USAJobs if configured (federal/government jobs)
    usajobs_configured = (hasattr(config, 'USAJOBS_API_KEY') and
                          config.USAJOBS_API_KEY != "your_usajobs_api_key_here")
    if usajobs_configured:
        print("Fetching from USAJobs (federal)...")
        usajobs_jobs, _ = fetch_jobs_usajobs()
        print(f"Jobs from USAJobs: {len(usajobs_jobs)}")
        api_jobs.extend(usajobs_jobs)

    print(f"Total jobs from APIs: {len(api_jobs)}")

    # Track which jobs are new (for console output)
    new_job_count = 0
    now = datetime.now().isoformat()

    # Merge API jobs into cache (new jobs get added, existing jobs get updated)
    for job in api_jobs:
        job_id = job.get("id")
        if job_id:
            job_id_str = str(job_id)
            if job_id_str not in cached_jobs:
                new_job_count += 1
                job["_first_seen"] = now  # Track when we first saw this job
            cached_jobs[job_id_str] = job

    print(f"New jobs found: {new_job_count}")

    # Save the updated cache
    save_cached_jobs(cached_jobs, new_job_count)

    # Convert cache dict back to list for display, filter to last 30 days
    all_jobs = list(cached_jobs.values())
    all_jobs = filter_recent_jobs(all_jobs, days=30)
    print(f"Total jobs to display (last 30 days): {len(all_jobs)}")

    # Sort by date (newest first)
    def get_job_date(job):
        created = job.get("created", "")
        if not created:
            return datetime.min
        try:
            created = created.replace("Z", "+00:00")
            return datetime.fromisoformat(created).replace(tzinfo=None)
        except ValueError:
            return datetime.min

    all_jobs.sort(key=get_job_date, reverse=True)

    # Generate HTML report with ALL cached jobs from last 30 days
    report_path = generate_html_report(all_jobs)
    print(f"\nHTML report saved to: {report_path}")

    # Print summary of new jobs to console
    if new_job_count > 0:
        print(f"\n{'='*50}")
        print(f"NEW JOB POSTINGS: {new_job_count}")
        print('='*50)
        for job in api_jobs:
            job_id = str(job.get("id", ""))
            if job.get("_first_seen") == now:
                print(f"\n• {job.get('title', 'Unknown')}")
                print(f"  Company: {job.get('company', {}).get('display_name', 'Unknown')}")
                print(f"  Location: {job.get('location', {}).get('display_name', 'N/A')}")
    else:
        print("\nNo new jobs since last check.")


if __name__ == "__main__":
    main()
