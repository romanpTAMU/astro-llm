# Daily Portfolio Automation Guide

This guide explains how to set up automated daily portfolio generation and submission.

## Overview

The automation runs the full pipeline every trading day before market open (8:30 AM CST):
1. Generate universe
2. Fetch stock data
3. Score candidates
4. Build portfolio
5. Submit to MAYS AI competition

## Option 1: Windows Task Scheduler (Recommended for Windows)

### Step 1: Create the Batch Script

The batch script `scripts/schedule_daily.bat` is already created. Make sure it's executable.

### Step 2: Create Logs Directory

```powershell
mkdir logs
```

### Step 3: Set Up Task Scheduler

1. Open **Task Scheduler** (search for "Task Scheduler" in Windows)
2. Click **Create Basic Task** in the right panel
3. Name: `MAYS AI Daily Portfolio Submission`
4. Description: `Automated daily portfolio generation and submission`
5. Trigger: **Daily**
   - Start date: Today
   - Time: **7:00 AM** (or earlier, to ensure completion before 8:30 AM market open)
   - Recur every: 1 day
6. Action: **Start a program**
   - Program/script: `C:\Users\Roman\Documents\ASTRO\astro-llm\scripts\schedule_daily.bat`
   - Start in: `C:\Users\Roman\Documents\ASTRO\astro-llm`
7. Conditions:
   - ✅ Uncheck "Start the task only if the computer is on AC power"
   - ✅ Check "Wake the computer to run this task" (if you want it to wake from sleep)
8. Settings:
   - ✅ Run task as soon as possible after a scheduled start is missed
   - ✅ If the task fails, restart every: 10 minutes (up to 3 times)
   - ✅ Stop the task if it runs longer than: 2 hours

### Step 4: Test the Task

1. Right-click the task → **Run**
2. Check the logs in `logs/daily_submit.log`
3. Verify the portfolio was created in `data/runs/`

## Option 2: Python Scheduler (Cross-platform)

For a Python-based scheduler that runs continuously:

```python
# scripts/scheduler_service.py
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import subprocess
from pathlib import Path

scheduler = BlockingScheduler()

def run_daily_submission():
    """Run the daily submission script."""
    script_path = Path(__file__).parent / "daily_submit.py"
    subprocess.run([sys.executable, str(script_path)])

# Schedule for 7:00 AM CST (12:00 UTC) every weekday
scheduler.add_job(
    run_daily_submission,
    trigger=CronTrigger(hour=12, minute=0, day_of_week='mon-fri'),
    id='daily_portfolio_submission',
    name='Daily Portfolio Submission',
    replace_existing=True
)

print("Scheduler started. Press Ctrl+C to exit.")
scheduler.start()
```

Run with:
```bash
python scripts/scheduler_service.py
```

## Option 3: GitHub Actions (Cloud-based)

If you want to run this in the cloud:

```yaml
# .github/workflows/daily_submit.yml
name: Daily Portfolio Submission

on:
  schedule:
    # Run at 7:00 AM CST (12:00 UTC) on weekdays
    - cron: '0 12 * * 1-5'
  workflow_dispatch: # Allow manual trigger

jobs:
  submit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install
      - name: Run daily submission
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
          FMP_API_KEY: ${{ secrets.FMP_API_KEY }}
        run: python scripts/daily_submit.py
```

## Manual Testing

Test the script manually:

```bash
# Test without submission
python scripts/daily_submit.py --skip-submission

# Test with submission
python scripts/daily_submit.py

# Force run on non-trading day
python scripts/daily_submit.py --force

# Submit existing portfolio
python scripts/daily_submit.py --portfolio-file data/runs/2025-11-16_17-58-08/portfolio.json
```

## Logging

Logs are written to:
- `logs/daily_submit.log` - Batch script execution log
- Console output - Full pipeline output
- Run folders - Each run saves all intermediate files

## Troubleshooting

### Task doesn't run
- Check Task Scheduler history for errors
- Verify the batch script path is correct
- Ensure virtual environment is activated correctly

### Submission fails
- Check internet connection
- Verify API keys are set in environment
- Check Playwright browser is installed: `playwright install`

### Portfolio generation fails
- Check API rate limits haven't been exceeded
- Verify data sources are accessible
- Check logs for specific error messages

## Trading Day Detection

The script includes basic trading day detection (excludes weekends and major holidays). For more accurate detection, consider:

1. **pandas_market_calendars**:
   ```bash
   pip install pandas-market-calendars
   ```

2. Update `is_trading_day()` in `scripts/daily_submit.py` to use:
   ```python
   import pandas_market_calendars as mcal
   nyse = mcal.get_calendar('NYSE')
   return nyse.valid_days(start_date=check_date, end_date=check_date).size > 0
   ```

## Notifications (Optional)

Add email notifications on failure:

```python
import smtplib
from email.mime.text import MIMEText

def send_notification(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = 'your-email@gmail.com'
    msg['To'] = 'your-email@gmail.com'
    
    # Use Gmail SMTP or your email provider
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login('your-email@gmail.com', 'your-app-password')
    server.send_message(msg)
    server.quit()
```

## Best Practices

1. **Run early**: Schedule for 7:00 AM or earlier to ensure completion before 8:30 AM market open
2. **Monitor logs**: Check logs daily for the first week
3. **Test first**: Run manually a few times before enabling automation
4. **Backup**: Keep run folders for historical reference
5. **Alerts**: Set up notifications for failures

