import re
import requests
from bs4 import BeautifulSoup
import json
import os
import time
from datetime import datetime

# ── Configuration ────────────────────────────────────────────────────────────

CONFIG_FILE = "config.txt"
DATA_FILE   = "prices.json"
BASE_URL    = "https://uaro.net/cp/"
VENDOR_URL  = BASE_URL + "?module=merchant&action=vendors&p={page}"

# Seconds to wait between page requests — be polite to their server!
REQUEST_DELAY = 2.0

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_config():
    """Read session cookie from config.txt."""
    config = {}
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"Could not find '{CONFIG_FILE}'. "
            "Make sure it is in the same folder as this script."
        )
    with open(CONFIG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    if "session_cookie" not in config:
        raise ValueError("config.txt must contain a 'session_cookie=' line.")
    return config


def load_existing_data():
    """Load the existing prices.json file, or start fresh."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data):
    """Save the data back to prices.json."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_price(price_str):
    """Convert '3,888,888 z' to integer 3888888."""
    return int(price_str.replace(",", "").replace(" z", "").strip())


def make_item_key(item_name, cards):
    """
    Create a unique key for an item + cards combination.
    e.g. "Tooth of Bat" with no cards → "Tooth of Bat"
         "Porcellio Card" slotted       → "Sword [Porcellio Card]"
    """
    if cards:
        cards_str = ", ".join(sorted(cards))
        return f"{item_name} [{cards_str}]"
    return item_name

# ── Session ──────────────────────────────────────────────────────────────────

def apply_session_cookie(session, cookie_value):
    """Inject the browser session cookie so we skip the login form entirely."""
    print("Applying session cookie...")
    session.cookies.set("fluxSessionData", cookie_value, domain="uaro.net")

    # Verify it works by checking the vendors page for a logout link
    resp = session.get(VENDOR_URL.format(page=1))
    resp.raise_for_status()
    if "Logout" in resp.text or "logged in" in resp.text.lower():
        print("Session valid — logged in successfully!")
        return resp
    else:
        raise RuntimeError(
            "Session cookie did not work. It may have expired.\n"
            "Please log into uaro.net in your browser and update the\n"
            "session_cookie value in config.txt with a fresh one."
        )

# ── Scraping ─────────────────────────────────────────────────────────────────

def get_total_pages(session):
    """Scrape page 1 to find out how many pages exist."""
    resp = session.get(VENDOR_URL.format(page=1))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Look for text like "Found a total of 11687 record(s) across 585 pages"
    for td in soup.find_all("td"):
        text = td.get_text()
        if "across" in text and "record" in text:
            parts = text.split("across")
            pages_part = parts[1].strip().split()[0]
            return int(pages_part)

    # Fallback: look for the highest page number link
    page_links = soup.find_all("a", href=True)
    max_page = 1
    for link in page_links:
        href = link["href"]
        if "&p=" in href:
            try:
                p = int(href.split("&p=")[1].split("&")[0])
                max_page = max(max_page, p)
            except ValueError:
                pass
    return max_page


def parse_page(html):
    """
    Parse one page of vendor results.
    Returns a list of dicts:
      { merchant, shop, map, x, y, item, item_id, amount, price, cards }
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the data table (class="horizontal-table")
    table = soup.find("table", class_="horizontal-table")
    if not table:
        return []

    rows = table.find("tbody").find_all("tr")
    results = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue  # skip incomplete rows

        try:
            merchant = cells[0].get_text(strip=True)
            shop      = cells[1].get_text(strip=True)

            # Position cell: contains map name + X + Y as text, button is noise
            pos_cell  = cells[2]
            # Remove the button text, grab remaining text tokens
            for btn in pos_cell.find_all("button"):
                btn.decompose()
            pos_parts = pos_cell.get_text(separator=" ").split()
            map_name  = pos_parts[0] if len(pos_parts) > 0 else ""
            x         = pos_parts[1] if len(pos_parts) > 1 else ""
            y         = pos_parts[2] if len(pos_parts) > 2 else ""

            # Item: cells[3] is the icon image cell, cells[4] is the name cell
            # The upgrade level (e.g. "+ 7") sits as plain text BEFORE the link,
            # so we need to combine it with the link text.
            item_cell = cells[4]
            item_link = item_cell.find("a")

            if item_link:
                # Grab any text nodes before the link (the upgrade prefix)
                prefix_parts = []
                for node in item_cell.children:
                    if node == item_link:
                        break
                    text = node.get_text(strip=True) if hasattr(node, "get_text") else str(node).strip()
                    if text:
                        prefix_parts.append(text)

                prefix = "".join(prefix_parts).strip()
                # Normalise "+ 7" -> "+7"
                prefix = re.sub(r'\+\s*(\d+)', r'+\1', prefix).strip()

                base_name = item_link.get_text(strip=True)
                item_name = f"{prefix} {base_name}" if prefix else base_name
            else:
                item_name = item_cell.get_text(strip=True)

            # Item ID from the link href (&id=913)
            item_id = None
            if item_link and "id=" in item_link.get("href", ""):
                try:
                    item_id = int(item_link["href"].split("id=")[1].split("&")[0])
                except ValueError:
                    pass

            amount    = cells[5].get_text(strip=True)
            price_str = cells[6].get_text(strip=True)
            price     = parse_price(price_str)

            # Cards: either "None" span or a list of card names
            cards_cell = cells[7]
            none_span  = cards_cell.find("span", class_="not-applicable")
            if none_span:
                cards = []
            else:
                cards = [li.get_text(strip=True) for li in cards_cell.find_all("li")]

            results.append({
                "merchant": merchant,
                "shop":     shop,
                "map":      map_name,
                "x":        x,
                "y":        y,
                "item":     item_name,
                "item_id":  item_id,
                "amount":   amount,
                "price":    price,
                "cards":    cards,
            })

        except Exception as e:
            # Skip malformed rows silently (log if needed)
            print(f"  Warning: skipped a row due to error: {e}")
            continue

    return results


def scrape_all(session):
    """Scrape all pages and return a flat list of all listings."""
    total_pages = get_total_pages(session)
    print(f"Found {total_pages} pages to scrape.")

    all_listings = []

    for page in range(1, total_pages + 1):
        print(f"  Scraping page {page}/{total_pages}...", end="\r")
        resp = session.get(VENDOR_URL.format(page=page))
        resp.raise_for_status()
        listings = parse_page(resp.text)
        all_listings.extend(listings)
        time.sleep(REQUEST_DELAY)

    print(f"\nScraped {len(all_listings)} listings in total.")
    return all_listings

# ── Data storage ─────────────────────────────────────────────────────────────

def update_data(existing_data, listings, timestamp):
    """
    Merge today's listings into the existing data structure.

    Structure of prices.json:
    {
      "Tooth of Bat": {                  ← item key (name + cards)
        "item_id": 913,
        "history": [
          {
            "date": "2025-01-15",
            "entries": [
              { "merchant": "...", "shop": "...", "map": "...",
                "x": "...", "y": "...", "amount": "...", "price": 548 }
            ]
          }
        ]
      }
    }
    """
    date_str = timestamp.strftime("%Y-%m-%d")

    # Group today's listings by item key
    todays_items = {}
    for listing in listings:
        key = make_item_key(listing["item"], listing["cards"])
        if key not in todays_items:
            todays_items[key] = {
                "item_id": listing["item_id"],
                "entries": []
            }
        todays_items[key]["entries"].append({
            "merchant": listing["merchant"],
            "shop":     listing["shop"],
            "map":      listing["map"],
            "x":        listing["x"],
            "y":        listing["y"],
            "amount":   listing["amount"],
            "price":    listing["price"],
        })

    # Merge into existing data
    for key, today in todays_items.items():
        if key not in existing_data:
            existing_data[key] = {
                "item_id": today["item_id"],
                "history": []
            }

        # Check if we already have an entry for today (avoid duplicates on re-run)
        existing_dates = [h["date"] for h in existing_data[key]["history"]]
        if date_str in existing_dates:
            # Update the existing entry for today
            for h in existing_data[key]["history"]:
                if h["date"] == date_str:
                    h["entries"] = today["entries"]
                    break
        else:
            existing_data[key]["history"].append({
                "date":      date_str,
                "timestamp": timestamp.isoformat(),
                "entries":   today["entries"]
            })

        # Keep history sorted by date
        existing_data[key]["history"].sort(key=lambda h: h["date"])

    return existing_data

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  UARO Vending Price Tracker")
    print("=" * 50)

    # Load session cookie
    config = load_config()

    # Load existing price history
    existing_data = load_existing_data()
    print(f"Loaded existing data for {len(existing_data)} items.")

    # Start a session and apply the session cookie
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://uaro.net/cp/?module=merchant&action=vendors",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    apply_session_cookie(session, config["session_cookie"])

    # Scrape all pages
    timestamp = datetime.now()
    listings  = scrape_all(session)

    # Merge into existing data and save
    print("Saving data...")
    updated_data = update_data(existing_data, listings, timestamp)
    save_data(updated_data)

    print(f"Done! Data saved to '{DATA_FILE}'.")
    print(f"Tracking {len(updated_data)} unique item+card combinations.")
    print("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
    input("\nPress Enter to close this window...")
