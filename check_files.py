import os
from pathlib import Path

paths = [
    "/home/jonas/Documents/projects/review-app/.venv/lib/python3.13/site-packages/nicegui/templates/index.html",
    "/home/jonas/Documents/projects/review-app/.venv/lib/python3.13/site-packages/nicegui/static/favicon.ico"
]

for p in paths:
    path = Path(p)
    print(f"Path: {p}")
    print(f"  Exists: {path.exists()}")
    if path.exists():
        print(f"  Is file: {path.is_file()}")
        print(f"  Readable: {os.access(p, os.R_OK)}")
        try:
            with open(p, 'rb') as f:
                f.read(10)
            print("  Read: Success")
        except Exception as e:
            print(f"  Read: Failed ({e})")
    else:
        print("  Parent exists:", path.parent.exists())
        if path.parent.exists():
            print("  Contents of parent:", os.listdir(path.parent))
