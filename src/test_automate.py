import json
from random import random
import random
import re
import os
from dotenv import load_dotenv
from browser_use_sdk import BrowserUse

import google.generativeai as genai  # Gemini SDK
from pydantic import BaseModel, ValidationError

# Load environment variables from env.local
load_dotenv("env.local")

MAX_RETRIES = 3
# ---------------- Gemini LLM Setup ----------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

BROWSER_USE_API_KEY = os.getenv("BROWSER_USE_API_KEY")
client = BrowserUse(api_key=BROWSER_USE_API_KEY)

class BookingInfo(BaseModel):
    booking_id: str | None
    website: str | None
    required_info: list[str]

def extract_booking_id(user_query: str) -> BookingInfo:
    """Extract booking ID from user query using Gemini"""
    model = genai.GenerativeModel("gemini-1.5-flash")  # Fast & cheap model
    prompt = f"""
    Extract the booking ID and website from this user query. Also extract what are required from the query
    Return the output as following json format:
    {{"booking_id": "string", "website": "string", "required_info": ["string",...]}}
    Example: Given an HMM booking ID 'SINI25432400', retrieve the voyage number and arrival date from seacargotracking.net.
    Answer:
    {{
        "booking_id": "SINI25432400",
        "website": "seacargotracking.net",
        "required_info": ["voyage number", "arrival date"]
    }}

    User Query: {user_query}
    """
    response = model.generate_content(prompt)
    text = response.text.strip()

    # Remove code block markers if present
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        json_text = match.group(0)
        try:
            booking_data = json.loads(json_text)
            # Convert website to www. format if not already present
            website = booking_data.get("website", "")
            if website:
                # Remove any protocol
                website = re.sub(r"^https?://", "", website)
                # Remove any trailing slashes
                website = website.rstrip("/")
                # Add www. if not present
                if not website.startswith("www."):
                    website = "www." + website
                # Optionally, add http:// if you want a protocol (not https)
                website = "http://" + website
                booking_data["website"] = website
            return BookingInfo(**booking_data)
        except (json.JSONDecodeError, ValidationError):
            return BookingInfo(booking_id=None, website=None, required_info=[])
    else:
        return BookingInfo(booking_id=None, website=None, required_info=[])

def get_task_items(website: str, booking_id: str) -> str:
    task_old = ('You are an expert web automation agent. Your task is to retrieve the voyage number and arrival date for a given booking ID.\n'
        f'1. Open the home page: {website}.\n'
        '   - Close any pop-ups if they appear.\n'
        '2. Search for any clickable element containing the substring "HMM" (not exact match).\n'
        '   - If found, click it.\n'
        '   - If a new tab or window opens, switch to it.\n'
        '   - Close any pop-ups if they appear.\n'
        '3. On the HMM page, look for any link or button related to "Track" or "Trace".\n'
        '   - Click the tracking option when found.\n'
        '   - If a new tab or window opens, switch to it.\n'
        '   - Close any pop-ups if they appear.\n'
        '4. Locate the input field for booking ID dynamically.\n'
        '   - If input field is not visible, scroll just half page so that the tabs below learn more open up.\n'
        f'   - Enter the booking ID: {booking_id}.\n'
        '   - Locate and click the corresponding Search button.\n'
        '   - If a new tab or window opens after clicking Search, switch to it.\n'
        '   - Close any pop-ups if they appear.\n'
        '5. Wait for the results page and all data to load.\n'
        '   - Locate the Voyage and Arrival Date for the given booking ID.\n'
        '   - If any of the above is not visible, scroll down little bit.\n'
        '   - If scrolling down does not help, extract all tables in the page, then in one of the table you will find voyage.\n'
        '   - If multiple results, pick the first relevant match.\n'
        '6. Return the extracted values as a JSON object with keys "voyage_number" and "arrival_date".'
    )
    task = (
        'You are an expert web automation agent. Your task is to retrieve the voyage number and arrival date for a given booking ID.\n'
        'Note: If index based access is needed, always pick the first visible element dynamically (no hardcoded index).\n'    
        'Note: If you go to any login page, start from the beginning. If you exceed 3 tries, exit with an error.\n'
        f'1. Open the home page: {website}.\n'
        '   - Close any pop-ups if they appear.\n'
        '2. Search for any clickable element containing the substring "HMM" (not exact match).\n'
        '   - If found, click it. Click the first visible element dynamically (no hardcoded index).\n'
        '   - If a new tab or window opens, switch to it.\n'
        '   - Close any pop-ups if they appear.\n'
        '3. On the HMM page, look for any link or button related to "e-Service".\n'
        '   - Click the e-Service option when found.\n'
        '   - If a new tab or window opens, switch to it.\n'
        '   - Close any pop-ups if they appear.\n'
        '4. Locate the input field for booking ID dynamically.\n'
        f'   - Enter the booking ID: {booking_id}.\n'
        '   - Locate and click the corresponding Search button.\n'
        '   - If a new tab or window opens after clicking Search, switch to it.\n'
        '   - Close any pop-ups if they appear.\n'
        '5. Wait for the results page and all data to load.\n'
        '   - Locate the Voyage and Arrival Date for the given booking ID.\n'
        '   - If any of the above is not visible, scroll down little bit.\n'
        '   - If any of the above is not visible, scroll down little bit.\n'
        '   - If any of the above is not visible, scroll down little bit.\n'
        '   - If there is no voyage after scrolling multiple times, extract all tables in the page, then in one of the table you will find voyage.\n'
        '   - If multiple results, pick the first relevant match.\n'
        '6. Return the extracted values as a JSON object with keys "booking_id", "voyage_number" and "arrival_date".'
    )
    return task

def run_task_with_retry(client, task, max_retries=3, delay_range=(5, 10)):
    for attempt in range(max_retries):
        task_for_sdk = client.tasks.create_task(task=task, llm="gpt-4.1", flash_mode=True)
        result = task_for_sdk.complete()

        # Detect firewall or blocked access
        if any("firewall" in (step.memory or "").lower() for step in result.steps):
            print(f"[Warning] Firewall blocked attempt {attempt+1}/{max_retries}. Retrying...")
            time.sleep(random.randint(*delay_range))
            continue

        return result

    print("[Error] Max retries reached. Task failed.")
    return None

# input_query = "Given an HMM booking ID 'SINI25432400', retrieve the voyage number and arrival date from seacargotracking.net."
# print("Example input query:", input_query)
# print("===============================")
# input_query = input("Enter your query: ")
# booking_info = extract_booking_id(input_query).model_dump()
# print("Extracted booking info:", booking_info)
# website = booking_info["website"] 
# # website = "http://www.seacargotracking.net/index.html"
# booking_id = booking_info["booking_id"] 
# # booking_id = "SINI25432400"
# task = get_task_items(website, booking_id)
# # task_for_sdk = client.tasks.create_task(
# #     task=task,
# #     llm="gpt-4.1",
# #     flash_mode=True
# # )
# import time, datetime
# current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
# start = time.time()
# result = run_task_with_retry(client, task, max_retries=MAX_RETRIES)
# # result = task_for_sdk.complete()
# end = time.time()
# print(f"Time taken: {end - start} seconds")
# steps_dict = []
# for step in result.steps:
#     steps_dict.append({
#         "step_number": step.number,
#         "memory":step.memory,
#         "evaluation_previous_goal": step.evaluation_previous_goal,
#         "next_goal": step.next_goal,
#         "url":step.url,
#         "screenshot_url":step.screenshot_url,
#         "actions":step.actions,
#         }
#     )
# print("Output: ",result.output)
# result_json_name = f"result_{booking_id}_{current_time}.json"
# with open(result_json_name, "w", encoding="utf-8") as f:
#     json.dump(steps_dict, f, indent=2)