#!/usr/bin/env python3
"""
seed_passwords.py
Set initial bcrypt passwords for PrismaOS users.

Usage:
    python scripts/seed_passwords.py

You will be prompted for a password for each user found in environment.yaml.
Passwords are stored as bcrypt hashes in the `users` table.
Run this once after Phase 5.11 is deployed, or again to reset a password.
"""

import os
import sys
import getpass
import bcrypt
import psycopg2
import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        dbname=os.getenv("POSTGRES_DB", "systemos"),
        user=os.getenv("POSTGRES_USER", "daniel"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=5,
    )


def load_team() -> dict:
    env_path = os.path.join(PROJECT_ROOT, "environment.yaml")
    with open(env_path) as f:
        data = yaml.safe_load(f)
    return data.get("team", {})


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=12)).decode()


def upsert_user(conn, username: str, hashed: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, bcrypt_hash)
            VALUES (%s, %s)
            ON CONFLICT (username) DO UPDATE SET
                bcrypt_hash = EXCLUDED.bcrypt_hash,
                active = true
            """,
            (username, hashed),
        )
    conn.commit()


def main():
    team = load_team()
    if not team:
        print("No team members found in environment.yaml.")
        sys.exit(1)

    print("\nPrismaOS — User Password Setup")
    print("=" * 40)
    print(f"Found {len(team)} team members: {', '.join(team.keys())}")
    print()

    # Allow seeding specific users or all
    if len(sys.argv) > 1:
        usernames = [u for u in sys.argv[1:] if u in team]
        if not usernames:
            print(f"Unknown username(s): {sys.argv[1:]}")
            sys.exit(1)
    else:
        usernames = list(team.keys())

    conn = get_conn()
    seeded = []

    for username in usernames:
        info = team[username]
        role = info.get("role", "workspace_user")
        print(f"  {username} ({role})")

        try:
            pw = getpass.getpass(f"    Password (leave blank to skip): ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            break

        if not pw:
            print(f"    Skipped {username}.")
            continue

        confirm = getpass.getpass(f"    Confirm password: ")
        if pw != confirm:
            print(f"    Passwords do not match — skipped {username}.")
            continue

        hashed = hash_password(pw)
        upsert_user(conn, username, hashed)
        seeded.append(username)
        print(f"    ✓ Password set for {username}.")

    conn.close()

    print()
    if seeded:
        print(f"Done. Passwords set for: {', '.join(seeded)}")
    else:
        print("No passwords were set.")
    print()
    print("You can now log in at http://localhost:3000/login")


if __name__ == "__main__":
    main()
