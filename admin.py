import os
import getpass

import psycopg
from pwdlib import PasswordHash


DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "melodyguard",
    "user": "postgres",
}


def main():
    db_password = os.environ.get("MELODYGUARD_DB_PASSWORD")

    if not db_password:
        raise RuntimeError(
            "Set MELODYGUARD_DB_PASSWORD before running this script."
        )

    username = input("Choose an admin username: ").strip()

    if len(username) < 3 or len(username) > 50:
        print("Username must contain between 3 and 50 characters.")
        return

    password = getpass.getpass("Choose an admin password: ")

    if len(password) < 12 or len(password) > 128:
        print("Password must contain between 12 and 128 characters.")
        return

    confirmation = getpass.getpass("Confirm admin password: ")

    if password != confirmation:
        print("Passwords do not match.")
        return

    hasher = PasswordHash.recommended()
    hashed_password = hasher.hash(password)

    with psycopg.connect(
        **DB_CONFIG,
        password=db_password,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO admin_users (username, password_hash)
                VALUES (%s, %s)
                ON CONFLICT (username) DO NOTHING
                RETURNING admin_id;
                """,
                (username, hashed_password),
            )

            row = cur.fetchone()

            if row is None:
                print("That username already exists. Choose another.")
                return

            print(f"Admin account created successfully. ID: {row[0]}")


if __name__ == "__main__":
    main()