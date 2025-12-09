# main.py
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
import openai
import requests
import os
import re
import random
import string
from dotenv import load_dotenv

app = FastAPI()
load_dotenv()

# CONFIGURATION
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

print("--- GHOST DIAGNOSE START ---")

# 1. TEST OPENAI
print("\n1. Testing OpenAI Connection...")
try:
    openai.api_key = OPENAI_KEY
    if not OPENAI_KEY:
        print("X Error: OPENAI_API_KEY not found in .env")
    else:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Say 'Hello Boss'!"}]
        )
        print(f"OK OpenAI says: {response.choices[0].message.content}")
except Exception as e:
    print(f"X OpenAI Error: {e}")

# 2. TEST SHOPIFY
print("\n2. Testing Shopify Connection...")
try:
    if not SHOPIFY_STORE_URL or not ACCESS_TOKEN:
        print("X Error: SHOPIFY credentials not found in .env")
    else:
        url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/shop.json"
        headers = {"X-Shopify-Access-Token": ACCESS_TOKEN}
        r = requests.get(url, headers=headers)
        
        if r.status_code == 200:
            shop_name = r.json()['shop']['name']
            print(f"OK Shopify Connected! Store Name: '{shop_name}'")
        else:
            print(f"X Shopify Error: Status {r.status_code} - {r.text}")
except Exception as e:
    print(f"X Connection Error: {e}")

print("\n------------------------------")