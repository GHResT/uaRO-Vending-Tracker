"""
get-session.py
──────────────
Opens a real browser window so you can log into uaro.net manually
(including solving the CAPTCHA). Once you're logged in, this script
automatically grabs your session cookie and saves it to config.txt.

Run this whenever your session expires:
    python get-session.py
"""

import os
import re
from playwright.sync_api import sync_playwright

CONFIG_FILE  = "config.txt"
LOGIN_URL    = "https://uaro.net/cp/?module=account&action=login"
VENDORS_URL  = "https://uaro.net/cp/?module=merchant&action=vendors"

def save_cookie(cookie_value):
    """Write or update session_cookie= in config.txt."""
    new_line = f"session_cookie={cookie_value}\n"

    # If config.txt exists, replace the session_cookie line (or append it)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith("session_cookie="):
                lines[i] = new_line
                updated = True
                break

        if not updated:
            lines.append(new_line)

        with open(CONFIG_FILE, "w") as f:
            f.writelines(lines)
    else:
        # Create a fresh config.txt
        with open(CONFIG_FILE, "w") as f:
            f.write(new_line)


def main():
    print("=" * 50)
    print("  UARO Session Cookie Helper")
    print("=" * 50)
    print()
    print("A browser window will open on the login page.")
    print("Log in normally (including the CAPTCHA).")
    print("This script will detect when you're logged in")
    print("and save your session cookie automatically.")
    print()
    print("Do NOT close the browser window yourself —")
    print("it will close on its own once the cookie is saved.")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context()
        page    = context.new_page()

        # Open the login page
        page.goto(LOGIN_URL)

        print("Waiting for you to log in...")

        # Wait until the page contains "Logout" — meaning login succeeded.
        # Timeout of 5 minutes — plenty of time to fill in the form + CAPTCHA.
        try:
            page.wait_for_selector("text=Logout", timeout=300_000)
        except Exception:
            print()
            print("ERROR: Timed out waiting for login.")
            print("Please try again and make sure you log in within 5 minutes.")
            browser.close()
            return

        # Grab the session cookie
        cookies = context.cookies("https://uaro.net")
        session = next(
            (c for c in cookies if c["name"] == "fluxSessionData"), None
        )

        if not session:
            print()
            print("ERROR: Could not find the session cookie after login.")
            print("Please try again or grab it manually from F12 > Application > Cookies.")
            browser.close()
            return

        cookie_value = session["value"]
        save_cookie(cookie_value)
        browser.close()

    print()
    print("✅ Session cookie saved to config.txt!")
    print(f"   Cookie: {cookie_value[:12]}... (truncated for safety)")
    print()
    print("You can now run the scraper:")
    print("    python scraper.py")
    print("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
    input("\nPress Enter to close this window...")
