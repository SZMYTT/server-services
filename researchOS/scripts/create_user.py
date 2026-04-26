"""Set a password for a researchOS user. Run once during setup."""
import sys
import getpass
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from web.auth import set_password

username = sys.argv[1] if len(sys.argv) > 1 else "daniel"
password = getpass.getpass(f"Set password for '{username}': ")
confirm  = getpass.getpass("Confirm password: ")
if password != confirm:
    print("Passwords do not match.")
    sys.exit(1)
set_password(username, password)
print(f"Password set for '{username}'.")
