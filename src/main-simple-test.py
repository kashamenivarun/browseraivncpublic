#!/usr/bin/env python3

import os
import time
import json
import re
import datetime
import logging
import subprocess
from dotenv import load_dotenv
from random import randint
from pydantic import BaseModel, ValidationError
from browser_use_sdk import BrowserUse
# import google.generativeai as genai

# Load env vars (Gemini + BrowserUse keys)
load_dotenv("env.local")
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BROWSER_USE_API_KEY = os.getenv("BROWSER_USE_API_KEY")

# Configure Gemini SDK
# genai.configure(api_key=GEMINI_API_KEY)

# Setup BrowserUse client
client = BrowserUse(api_key=BROWSER_USE_API_KEY)

# Constants
MAX_RETRIES = 3
DISPLAY_NUM = 99
DISPLAY = f":{DISPLAY_NUM}"
LOCK_PATH = f"/tmp/.X{DISPLAY_NUM}-lock"


# === Virtual Display Functions ===
def start_display():
    if os.path.exists(LOCK_PATH):
        os.remove(LOCK_PATH)
    subprocess.run(["pkill", "-f", f"X.*{DISPLAY}"], stderr=subprocess.DEVNULL)
    cmd = ["Xvfb", DISPLAY, "-screen", "0", "1024x768x24"]
    xv_proc = subprocess.Popen(cmd)
    os.environ["DISPLAY"] = DISPLAY
    logging.info("Waiting for virtual display to be ready...")
    time.sleep(5)  # You can improve with a socket poll
    return xv_proc

def stop_display(proc):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception as e:
        logging.warning(f"Could not stop Xvfb: {e}")


# === Gemini Model & Task Generation ===

class BookingInfo(BaseModel):
    booking_id: str | None
    website: str | None
    required_info: list[str]

def extract_booking_id(user_query: str) -> BookingInfo:
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""
    Extract the booking ID and website from this user query. Also extract what are required from the query
    Return the output as following json format:
    {{
        "booking_id": "string",
        "website": "string",
        "required_info": ["string",...]
    }}
    User Query: {user_query}
    """
    response = model.generate_content(prompt)
    text = response.text.strip()

    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            # Normalize website
            website = data.get("website", "")
            if website:
                website = re.sub(r"^https?://", "", website).rstrip("/")
                if not website.startswith("www."):
                    website = "www." + website
                website = "http://" + website
                data["website"] = website
            return BookingInfo(**data)
        except (json.JSONDecodeError, ValidationError):
            pass
    return BookingInfo(booking_id=None, website=None, required_info=[])

def get_task_items(website: str, booking_id: str) -> str:
    return (
        'You are an expert web automation agent. Your task is to retrieve the voyage number and arrival date for a given booking ID.\n'
        f'1. Open the home page: {website}.\n'
        '2. Search for any clickable element containing "HMM" and click it.\n'
        '3. On the HMM page, go to "e-Service".\n'
        f'4. Enter the booking ID: {booking_id} and click Search.\n'
        '5. Wait for results and extract voyage number and arrival date.\n'
        '6. Return a JSON object: {"booking_id": "...", "voyage_number": "...", "arrival_date": "..."}'
    )

def run_task_with_retry(client, task, max_retries=3, delay_range=(5, 10)):
    for attempt in range(max_retries):
        task_obj = client.tasks.create_task(task=task, llm="gpt-4.1", flash_mode=True)
        result = task_obj.complete()
        if any("firewall" in (step.memory or "").lower() for step in result.steps):
            logging.warning(f"Firewall blocked attempt {attempt+1}, retrying...")
            time.sleep(randint(*delay_range))
            continue
        return result
    logging.error("Max retries reached. Task failed.")
    return None


# === Main App Flow ===
def main():
    logging.basicConfig(level=logging.INFO)
    xv = start_display()
    try:
        # user_query = os.getenv("QUERY")
        # if not user_query:
        #     try:
        #         user_query = input("Enter your query (e.g. HMM booking request): ").strip()
        #     except EOFError:
        #         logging.error("No input provided and no QUERY env var set. Exiting.")
        #         return
        # if not user_query:
        #     raise ValueError("Query is empty")

        # logging.info("Extracting booking info using Gemini...")
        # booking_info = extract_booking_id(user_query).model_dump()
        # logging.info(f"Extracted info: {booking_info}")

        # website = booking_info["website"]
        # booking_id = booking_info["booking_id"]
        website = "http://www.seacargotracking.net/index.html"
        booking_id = "SINI25432400"

        if not (website and booking_id):
            raise ValueError("Incomplete booking information. Exiting.")

        task = get_task_items(website, booking_id)

        logging.info("Running automation task using BrowserUse SDK...")
        start = time.time()
        result = run_task_with_retry(client, task, max_retries=MAX_RETRIES)
        end = time.time()
        logging.info(f"Automation completed in {end - start:.2f} seconds")

        if result:
            steps_dict = [{
                "step_number": step.number,
                "memory": step.memory,
                "evaluation_previous_goal": step.evaluation_previous_goal,
                "next_goal": step.next_goal,
                "url": step.url,
                "screenshot_url": step.screenshot_url,
                "actions": step.actions
            } for step in result.steps]

            output = result.output
            logging.info(f"Result Output: {output}")

            filename = f"result_{booking_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(steps_dict, f, indent=2)
            logging.info(f"Saved steps to: {filename}")
        else:
            logging.error("Task failed with no result.")

    except Exception as e:
        logging.exception(f"Fatal error: {e}")
    finally:
        stop_display(xv)

if __name__ == "__main__":
    main()
