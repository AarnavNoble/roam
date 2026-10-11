"""
Deploy the current checkout to the Hugging Face Space.

The Space is updated by uploading files through the Hub API (that is how it
was deployed before; it does not share git history with GitHub). The Space
rebuilds its Docker image on every upload.

Run from the repo root, on main, with a clean working tree:

    python scripts/deploy_space.py              # upload code only
    python scripts/deploy_space.py --set-secret # also create JWT_SECRET (first deploy only)

The server refuses to start without JWT_SECRET, so the first deploy of this
version needs --set-secret. Running it again later rotates the secret, which
signs every user out. HF_TOKEN is read from backend/.env.

Nothing is deleted from the Space. Files from older deploys that no longer
exist here stay behind unused.
"""

import argparse
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi

REPO = "AarnavNoble/roam"
ROOT = Path(__file__).resolve().parents[1]

# Never upload local state, secrets, or the mobile app (the Space only serves the API).
IGNORE = [
    ".git/**", ".venv/**", "mobile/**", "data/**", "node_modules/**", ".claude/**",
    "**/__pycache__/**", "*.pyc", ".pytest_cache/**", "**/.env", ".env*", "**/.DS_Store", ".DS_Store",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-secret", action="store_true",
                        help="create a random JWT_SECRET on the Space (rotates it if one exists)")
    args = parser.parse_args()

    load_dotenv(ROOT / "backend" / ".env")
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise SystemExit("HF_TOKEN is not set in backend/.env")
    api = HfApi(token=token)

    if args.set_secret:
        api.add_space_secret(REPO, "JWT_SECRET", secrets.token_urlsafe(48))
        print("JWT_SECRET set on the Space (value not shown)")

    commit = api.upload_folder(
        repo_id=REPO, repo_type="space", folder_path=str(ROOT),
        commit_message="Deploy from GitHub main", ignore_patterns=IGNORE,
    )
    print(f"Uploaded: {commit.commit_url}")

    files = api.list_repo_files(REPO, repo_type="space")
    leaked = [f for f in files if f.endswith(".env")]
    if leaked:
        raise SystemExit(f"STOP: env files are on the Space, remove them and rotate keys: {leaked}")
    print(f"{len(files)} files on the Space. It is rebuilding now:")
    print(f"  https://huggingface.co/spaces/{REPO}")


if __name__ == "__main__":
    main()
