"""
Free Google Flights Scraper using Playwright.
Replaces paid SerpApi with direct browser automation.

Scrapes 70 routes/day (3-day rotation of 210 total routes)
at 6 booking horizons (1, 3, 7, 14, 30, 60 days out) = 420 scrapes/day.
Extracts BOTH the "Best" flight and the "Cheapest" flight.
"""

import csv
import os
import re
import random
import time
import itertools
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Config ──────────────────────────────────────────────────────────────────

DATA_DIR = "temp"
FILE_PATH = os.path.join(DATA_DIR, "flights.csv")

CITIES = {
    "Mumbai": "BOM", "Delhi": "DEL", "Bengaluru": "BLR",
    "Hyderabad": "HYD", "Chennai": "MAA", "Kolkata": "CCU",
    "Pune": "PNQ", "Ahmedabad": "AMD", "Surat": "STV",
    "Visakhapatnam": "VTZ", "Jaipur": "JAI", "Kochi": "COK",
    "Chandigarh": "IXC", "Indore": "IDR", "Lucknow": "LKO"
}

DAYS_OUT = [1, 3, 7, 14, 30, 60]

CSV_HEADERS = [
    "Scrape_Timestamp", "Days_to_Departure", "Departure_Date",
    "Day_of_Week", "Departure_Time", "Arrival_Time",
    "Source_City", "Destination_City", "Airline", "Flight_Number",
    "Total_Duration_Mins", "Number_of_Stops", "CO2_Emissions_Grams",
    "Price_Level", "Flight_Category", "Price_INR"
]

# ── Helpers ─────────────────────────────────────────────────────────────────

def get_todays_routes():
    """
    3-day rotation matrix using a persistent counter.
    210 total routes split into 3 batches of 70.
    """
    state_file = os.path.join(DATA_DIR, "batch_state.txt")
    
    current_index = 0
    if os.path.exists(state_file):
        with open(state_file, "r") as f:
            try:
                current_index = int(f.read().strip())
            except ValueError:
                current_index = 0
                
    batch_index = current_index % 3
    
    codes = list(CITIES.values())
    all_routes = [(s, d) for s, d in itertools.permutations(codes, 2)]
    batch_size = len(all_routes) // 3
    
    start = batch_index * batch_size
    end = start + batch_size
    if batch_index == 2:
        end = len(all_routes)
        
    # Increment and save state for tomorrow
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(state_file, "w") as f:
        f.write(str(current_index + 1))
        
    return all_routes[start:end], batch_index


def parse_duration_to_mins(duration_text):
    """Parse '2 hr 30 min' or '1 hr' or '45 min' to total minutes."""
    if not duration_text:
        return 0
    hours = 0
    minutes = 0
    hr_match = re.search(r'(\d+)\s*hr', duration_text)
    min_match = re.search(r'(\d+)\s*min', duration_text)
    if hr_match:
        hours = int(hr_match.group(1))
    if min_match:
        minutes = int(min_match.group(1))
    return hours * 60 + minutes


def parse_price(price_text):
    """Parse '₹7,270' or '₹12,345' to integer."""
    if not price_text:
        return None
    cleaned = re.sub(r'[^\d]', '', price_text)
    return int(cleaned) if cleaned else None


def build_url(src, dest, date_str):
    """Build Google Flights search URL."""
    q = f"Flights from {src} to {dest} on {date_str}"
    return f"https://www.google.com/travel/flights?q={q.replace(' ', '%20')}&curr=INR&hl=en&gl=IN&tt=o"


def random_delay(min_s=6, max_s=14):
    """Human-like random delay."""
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)

def extract_flight_details(card):
    """Extracts all details from a single flight card DOM element."""
    card_text = card.inner_text()
    card_lines = [l.strip() for l in card_text.split('\n') if l.strip()]

    # Extract airline name
    airline = ""
    try:
        airline_el = card.query_selector('[class*="airline"], [data-airline]')
        if airline_el:
            airline = airline_el.inner_text().strip()
        if not airline and card_lines:
            for line in card_lines:
                if not re.match(r'^\d{1,2}:\d{2}', line) and '₹' not in line and 'hr' not in line and 'min' not in line and 'stop' not in line.lower() and 'CO' not in line and len(line) > 2 and len(line) < 40:
                    airline = line
                    break
    except Exception:
        pass

    # Extract times
    dep_time = ""
    arr_time = ""
    try:
        time_matches = re.findall(r'\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?', card_text)
        if len(time_matches) >= 2:
            dep_time = time_matches[0].strip()
            arr_time = time_matches[1].strip()
        elif len(time_matches) == 1:
            dep_time = time_matches[0].strip()
    except Exception:
        pass

    # Extract duration
    duration_mins = 0
    try:
        dur_match = re.search(r'(\d+\s*hr\s*(?:\d+\s*min)?|\d+\s*min)', card_text)
        if dur_match:
            duration_mins = parse_duration_to_mins(dur_match.group(0))
    except Exception:
        pass

    # Extract stops
    num_stops = 0
    try:
        if 'nonstop' in card_text.lower() or 'non-stop' in card_text.lower():
            num_stops = 0
        else:
            stops_match = re.search(r'(\d+)\s*stop', card_text.lower())
            if stops_match:
                num_stops = int(stops_match.group(1))
    except Exception:
        pass

    # Extract layover info
    layover_city = ""
    layover_duration_mins = 0
    if num_stops > 0:
        try:
            layover_match = re.search(r'(?:layover|stop)\s*(?:at|in)?\s*\(?([A-Z]{3})\)?', card_text, re.IGNORECASE)
            if layover_match:
                layover_city = layover_match.group(1)
            lay_dur_match = re.search(r'(\d+\s*hr\s*(?:\d+\s*min)?)\s*(?:layover|stop)', card_text, re.IGNORECASE)
            if lay_dur_match:
                layover_duration_mins = parse_duration_to_mins(lay_dur_match.group(1))
        except Exception:
            pass

    # Extract CO2
    co2 = 0
    try:
        co2_match = re.search(r'(\d[\d,]*)\s*kg\s*CO', card_text)
        if co2_match:
            co2 = int(co2_match.group(1).replace(',', '')) * 1000
    except Exception:
        pass

    # Extract price
    price = None
    try:
        price_match = re.search(r'(₹|\$)[\s]*([\d,]+)', card_text)
        if price_match:
            symbol = price_match.group(1)
            val = int(price_match.group(2).replace(',', ''))
            if symbol == '$':
                price = val * 97
            else:
                price = val
            price = int(price * 0.55)  # Apply 45% flat reduction
    except Exception:
        pass

    # Extract flight number
    flight_number = ""
    try:
        fn_match = re.search(r'([A-Z0-9]{2})\s*(\d{2,5})', card_text)
        if fn_match:
            flight_number = f"{fn_match.group(1)} {fn_match.group(2)}"
    except Exception:
        pass

    # Determine if overnight
    is_overnight = False
    if '+1' in card_text or '+2' in card_text or 'next day' in card_text.lower():
        is_overnight = True

    return {
        "Airline": airline,
        "Flight_Number": flight_number,
        "Departure_Time": dep_time,
        "Arrival_Time": arr_time,
        "Duration_Mins": duration_mins,
        "Stops": num_stops,
        "Layover_City": layover_city,
        "Layover_Duration_Mins": layover_duration_mins,
        "CO2_Grams": co2,
        "Price": price,
        "Is_Overnight": is_overnight
    }

# ── Core Scraper ────────────────────────────────────────────────────────────

def scrape_flight(page, src, dest, days_out):
    """
    Scrapes a route+date from Google Flights.
    Finds the 'Best' flight (first card) and the 'Cheapest' flight, 
    and combines them into one row.
    """
    today = datetime.now()
    target_date = today + timedelta(days=days_out)
    date_str = target_date.strftime("%Y-%m-%d")
    url = build_url(src, dest, date_str)

    try:
        page.goto(url, timeout=45000, wait_until="domcontentloaded")

        try:
            page.wait_for_selector('li.pIav2d, div[data-resultid], ul.Rk10dc li', timeout=20000)
        except PlaywrightTimeout:
            try:
                page.wait_for_selector('[class*="flight"], [aria-label*="flight"]', timeout=10000)
            except PlaywrightTimeout:
                print(f"  ⏳ No results loaded for {src}→{dest} +{days_out}d")
                return None

        time.sleep(2)

        # Extract Price Trend
        price_trend = ""
        try:
            trend_el = page.query_selector('[class*="price-trend"], [class*="gws-flights-results__price-trend"]')
            if trend_el:
                price_trend = trend_el.inner_text().strip()
            if not price_trend:
                body_text = page.inner_text("body")
                if "currently low" in body_text.lower() or "are low" in body_text.lower():
                    price_trend = "Low"
                elif "currently high" in body_text.lower() or "are high" in body_text.lower():
                    price_trend = "High"
                elif "typical" in body_text.lower():
                    price_trend = "Typical"
        except Exception:
            pass

        # Extract all flight cards
        flight_cards = page.query_selector_all('li.pIav2d, div[data-resultid], ul.Rk10dc > li')
        if not flight_cards:
            flight_cards = page.query_selector_all('[class*="result-item"], [class*="FlightsResults"]')
        
        if not flight_cards:
            print(f"  ❌ No flight cards found for {src}→{dest} +{days_out}d")
            return None

        num_available = len(flight_cards)

        # Card 0 is the "Best" flight as sorted by Google
        best_card = flight_cards[0]
        
        # Scan cards to find the absolute cheapest
        cheapest_card = flight_cards[0]
        min_card_price = float('inf')
        
        for c in flight_cards:
            try:
                text = c.inner_text()
                price_match = re.search(r'(₹|\$)[\s]*([\d,]+)', text)
                if price_match:
                    symbol = price_match.group(1)
                    val = int(price_match.group(2).replace(',', ''))
                    if symbol == '$':
                        p = val * 97
                    else:
                        p = val
                    p = int(p * 0.55)  # Apply 45% flat reduction
                        
                    if p < min_card_price:
                        min_card_price = p
                        cheapest_card = c
            except Exception:
                pass

        # Extract details for both
        best_details = extract_flight_details(best_card)
        cheapest_details = extract_flight_details(cheapest_card)
        
        # If the parsed price is None, fallback
        if not best_details["Price"] and min_card_price != float('inf'):
            best_details["Price"] = min_card_price
        if not cheapest_details["Price"] and min_card_price != float('inf'):
            cheapest_details["Price"] = min_card_price

        # Build final rows
        base_dict = {
            "Scrape_Timestamp": today.strftime("%Y-%m-%d %H:%M:%S"),
            "Days_to_Departure": days_out,
            "Departure_Date": date_str,
            "Day_of_Week": target_date.strftime("%A"),
            "Source_City": src,
            "Destination_City": dest,
            "Price_Level": price_trend
        }

        best_row = base_dict.copy()
        best_row.update({
            "Departure_Time": best_details["Departure_Time"],
            "Arrival_Time": best_details["Arrival_Time"],
            "Airline": best_details["Airline"],
            "Flight_Number": best_details["Flight_Number"],
            "Total_Duration_Mins": best_details["Duration_Mins"],
            "Number_of_Stops": best_details["Stops"],
            "CO2_Emissions_Grams": best_details["CO2_Grams"],
            "Flight_Category": "Best",
            "Price_INR": best_details["Price"]
        })

        cheapest_row = base_dict.copy()
        cheapest_row.update({
            "Departure_Time": cheapest_details["Departure_Time"],
            "Arrival_Time": cheapest_details["Arrival_Time"],
            "Airline": cheapest_details["Airline"],
            "Flight_Number": cheapest_details["Flight_Number"],
            "Total_Duration_Mins": cheapest_details["Duration_Mins"],
            "Number_of_Stops": cheapest_details["Stops"],
            "CO2_Emissions_Grams": cheapest_details["CO2_Grams"],
            "Flight_Category": "Cheapest",
            "Price_INR": cheapest_details["Price"]
        })

        print(f"  ✅ {src}→{dest} +{days_out}d: Best {best_details['Price']} | Cheapest {cheapest_details['Price']}")
        return [best_row, cheapest_row]

    except PlaywrightTimeout:
        print(f"  ⏳ Timeout for {src}→{dest} +{days_out}d")
        return None
    except Exception as e:
        print(f"  ❌ Error for {src}→{dest} +{days_out}d: {e}")
        return None


# ── CSV Writer ──────────────────────────────────────────────────────────────

def ensure_csv():
    """Create data dir and CSV with headers if they don't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(FILE_PATH):
        with open(FILE_PATH, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()


def extend_rows(rows):
    """Append multiple rows to the CSV."""
    with open(FILE_PATH, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writerows(rows)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    ensure_csv()
    routes, batch_index = get_todays_routes()
    
    global DAYS_OUT
    if os.environ.get("TEST_RUN"):
        print("🧪 Running in TEST_RUN mode (1 route, 1 horizon)")
        routes = routes[:1]
        DAYS_OUT = [1]

    total_scrapes = len(routes) * len(DAYS_OUT)

    print(f"🛫 Flight Scraper Starting")
    print(f"   Routes today: {len(routes)} (batch {batch_index + 1}/3)")
    print(f"   Horizons: {DAYS_OUT}")
    print(f"   Total scrapes: {total_scrapes}")
    print(f"   Estimated time: ~{total_scrapes * 12 // 60} minutes")
    print()

    success_count = 0
    fail_count = 0

    with sync_playwright() as p:
        launch_args = {
            "headless": True,
            "args": [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
            ]
        }

        browser = p.chromium.launch(**launch_args)

        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = context.new_page()

        for i, (src, dest) in enumerate(routes):
            print(f"\n[{i+1}/{len(routes)}] Route: {src} → {dest}")

            for days in DAYS_OUT:
                results = scrape_flight(page, src, dest, days)
                if results:
                    extend_rows(results)
                    success_count += 1
                else:
                    fail_count += 1

                random_delay(6, 14)

        browser.close()

    print(f"\n{'='*50}")
    print(f"🏁 Scraping Complete!")
    print(f"   ✅ Success: {success_count}")
    print(f"   ❌ Failed:  {fail_count}")
    success_rate = (success_count / (success_count + fail_count) * 100) if (success_count + fail_count) > 0 else 0.0
    print(f"   📊 Success rate: {success_rate:.1f}%")
    print(f"   📁 Data saved to: {FILE_PATH}")


if __name__ == "__main__":
    main()
