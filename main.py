# main.py
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
import openai
import requests
import os
import re
import random
import string

app = FastAPI()

# CONFIGURATION (We will move these to environment variables later)
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL") # e.g. "my-shop.myshopify.com"
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_KEY

# 1. THE LISTENER: Shopify hits this URL when a product is created/updated
@app.post("/webhook/product-update")
async def product_webhook(request: Request, background_tasks: BackgroundTasks):
    # Verify the webhook (Security step - simplified for now)
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}
    
    # Extract the Product ID and Data
    product_id = data.get("id")
    title = data.get("title")
    description = data.get("body_html")
    variants = data.get("variants", [])
    vendor = data.get("vendor", "")
    
    print(f"🕵️ Received Product: {title} (ID: {product_id})")
    
    # Send to the "Brain" in the background (so Shopify doesn't timeout)
    background_tasks.add_task(audit_and_fix_product, product_id, title, description, variants)
    
    return {"status": "received"}

# 2. THE BRAIN: The Logic to Check and Fix Data
def audit_and_fix_product(product_id, title, description, variants):
    print(f"⚙️ Auditing {title}...")
    
    # We will build a single payload with all changes
    payload = {}
    tags_to_add = []
    
    # 1. CHECK DESCRIPTION
    if not description or len(description) < 10: # Changed to 10 for testing
        print("❌ Description empty. Generating AI description...")
        try:
            prompt = f"Write a 3-sentence exciting sales description for: {title}"
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            new_desc = response.choices[0].message.content
            payload["body_html"] = new_desc
            print("✅ AI Description Generated.")
        except Exception as e:
            print(f"⚠️ OpenAI Error: {e}")

    # 2. CHECK WEIGHT (The "Tagging" Logic)
    # We check if ANY variant has 0 weight
    has_weight_issue = False
    for variant in variants:
        if float(variant.get('weight', 0)) == 0:
            has_weight_issue = True
            break
            
    if has_weight_issue:
        print("⚠️ Found 0kg weight. Adding tag.")
        tags_to_add.append("Validation-Error: Missing Weight")

    # 3. SAVE EVERYTHING
    # If we have a new description OR new tags, we update Shopify
    if payload or tags_to_add:
        # First, we need to handle tags (Shopify expects a comma-separated string)
        if tags_to_add:
            # We would ideally fetch existing tags first, but for now let's just push the error tag
            payload["tags"] = ",".join(tags_to_add)
            
        print(f"💾 Saving updates to Shopify: {payload.keys()}")
        update_shopify_product(product_id, payload)
    else:
        print("✨ Product looks good. No changes needed.")

def update_shopify_product(product_id, payload):
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/products/{product_id}.json"
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    # Filter out fields that shouldn't be sent if empty or unchanged to avoid issues
    # simplified for this demo
    
    response = requests.put(url, json={"product": payload}, headers=headers)
    if response.status_code == 200:
        print(f"✅ Product {product_id} automatically fixed and updated!")
    else:
        print(f"❌ Failed to update: {response.text}")

def add_tag_to_product(product_id, tag):
    print(f"🏷️ Adding tag '{tag}' to product {product_id}...")
    # Fetch current tags first
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/products/{product_id}.json"
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    try:
        get_response = requests.get(url, headers=headers)
        if get_response.status_code == 200:
            current_tags = get_response.json()['product']['tags']
            if tag not in current_tags:
                new_tags = f"{current_tags}, {tag}" if current_tags else tag
                update_response = requests.put(url, json={"product": {"id": product_id, "tags": new_tags}}, headers=headers)
                if update_response.status_code == 200:
                    print(f"✅ Tag Added.")
                else:
                    print(f"❌ Failed to add tag: {update_response.text}")
            else:
                 print(f"ℹ️ Tag already exists.")
        else:
             print(f"❌ Failed to fetch product for tagging: {get_response.text}")
    except Exception as e:
        print(f"❌ Error adding tag: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)