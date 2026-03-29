#!/usr/bin/env python3
"""
One-time OAuth setup for Gmail access.
Run this on your Mac — NOT in Docker.

Usage:
    python setup_gmail_oauth.py

After running, transfer the token files to the NAS:
    ssh nas 'cat > /volume1/docker/syncro-todoist-assistant/gmail_token_personal.json' < gmail_token_personal.json
    ssh nas 'cat > /volume1/docker/syncro-todoist-assistant/gmail_token_biz.json' < gmail_token_biz.json
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = "gcal_credentials.json"

ACCOUNTS = [
    ("personal", "jlh1825@gmail.com", "gmail_token_personal.json"),
    ("business", "jason.holland@hollandit.biz", "gmail_token_biz.json"),
]

for name, email, token_file in ACCOUNTS:
    print(f"\n{'='*50}")
    print(f"Authorizing {name} account: {email}")
    print(f"{'='*50}")
    print("A browser window will open. Log in as:", email)
    input("Press Enter to open browser...")

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(token_file, "w") as f:
        f.write(creds.to_json())

    print(f"✓ Saved {token_file}")

print("\n" + "="*50)
print("Done! Now transfer token files to the NAS:")
print("="*50)
for _, _, token_file in ACCOUNTS:
    print(f"  ssh nas 'cat > /volume1/docker/syncro-todoist-assistant/{token_file}' < {token_file}")
print()
