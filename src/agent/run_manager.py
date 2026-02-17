from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

RUN_MODE_FILE = "run_mode.json"


def get_run_mode(run_folder: Path) -> Optional[str]:
    """Return run mode for a run folder: 'biweekly', 'daily', or None if unset.

    Biweekly runs have run_mode.json with {"mode": "biweekly"}.
    """
    mode_file = run_folder / RUN_MODE_FILE
    if not mode_file.exists():
        return None
    try:
        data = json.loads(mode_file.read_text(encoding="utf-8"))
        return data.get("mode")
    except Exception:
        return None


def get_run_folder(base_dir: Path = Path("data/runs"), create: bool = True) -> Path:
    """Get or create a timestamped run folder.
    
    Args:
        base_dir: Base directory for runs (default: data/runs)
        create: Whether to create the folder if it doesn't exist
        
    Returns:
        Path to the run folder (e.g., data/runs/2025-11-16_14-30-45)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_folder = base_dir / timestamp
    
    if create:
        run_folder.mkdir(parents=True, exist_ok=True)
    
    return run_folder


def find_all_portfolios(runs_dir: Path = Path("data/runs")) -> list[tuple[Path, datetime]]:
    """Find all portfolio.json files in run folders.
    
    Args:
        runs_dir: Base directory containing run folders
        
    Returns:
        List of tuples: (portfolio_path, construction_datetime)
        Sorted by construction date (oldest first)
    """
    portfolios = []
    
    if not runs_dir.exists():
        return portfolios
    
    # Iterate through all run folders
    for run_folder in sorted(runs_dir.iterdir()):
        if not run_folder.is_dir():
            continue
        
        portfolio_file = run_folder / "portfolio.json"
        if not portfolio_file.exists():
            continue
        
        # Try to parse the portfolio to get construction date
        try:
            import json
            from datetime import datetime
            
            portfolio_data = json.loads(portfolio_file.read_text(encoding='utf-8'))
            constructed_at_str = portfolio_data.get("constructed_at")
            
            if constructed_at_str:
                if isinstance(constructed_at_str, str):
                    # Parse ISO format
                    constructed_at = datetime.fromisoformat(constructed_at_str.replace('Z', '+00:00'))
                else:
                    # Fallback to folder timestamp
                    folder_name = run_folder.name
                    constructed_at = datetime.strptime(folder_name, "%Y-%m-%d_%H-%M-%S")
            else:
                # Fallback to folder timestamp
                folder_name = run_folder.name
                constructed_at = datetime.strptime(folder_name, "%Y-%m-%d_%H-%M-%S")
            
            portfolios.append((portfolio_file, constructed_at))
        except Exception:
            # If parsing fails, skip this portfolio
            continue
    
    # Sort by construction date (oldest first)
    portfolios.sort(key=lambda x: x[1])
    
    return portfolios

