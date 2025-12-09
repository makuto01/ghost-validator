# main.py
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
import openai
import requests
import os

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
    data = await request.json()
    
    # Extract the Product ID and Data
    product_id = data.get("id")
    title = data.get("title")
    description = data.get("body_html")
    variants = data.get("variants", [])
    
    print(f"🕵️ Received Product: {title} ({product_id})")
    
    # Send to the "Brain" in the background (so Shopify doesn't timeout)
    background_tasks.add_task(audit_and_fix_product, product_id, title, description, variants)
    
    return {"status": "received"}

# 2. THE BRAIN: The Logic to Check and Fix Data
def audit_and_fix_product(product_id, title, description, variants):
    print(f"⚙️ Auditing {title}...")
    
    fixes_needed = False
    new_data = {}

    # CHECK 1: Is the description missing or too short?
    if not description or len(description) < 50:
        print("❌ Description too short. Generating AI description...")
        prompt = f"Write a professional, SEO-optimized e-commerce description for a product titled '{title}'. Format as HTML."
        
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        new_description = response.choices[0].message.content
        new_data["body_html"] = new_description
        fixes_needed = True

    # CHECK 2: Do variants have weight? (Amazon rejects 0 weight)
    # Note: AI can't weigh objects, but it can flagging it or apply a default.
    # Here we just flag it for the log, or set a "default placeholder" if the user wants.
    for variant in variants:
        if variant['weight'] == 0:
            print(f"⚠️ Variant {variant['id']} has 0 weight. Flagging.")
            # In a real app, we might apply a tag "Requires-Weight-Fix"
            add_tag_to_product(product_id, "Validation-Error: Missing Weight")
            return # Stop here if manual intervention is needed

    # 3. THE ACTION: Update Shopify if we fixed something
    if fixes_needed:
        update_shopify_product(product_id, new_data)

def update_shopify_product(product_id, payload):
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/products/{product_id}.json"
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    response = requests.put(url, json={"product": payload}, headers=headers)
    if response.status_code == 200:
        print(f"✅ Product {product_id} automatically fixed and updated!")
    else:
        print(f"❌ Failed to update: {response.text}")

def add_tag_to_product(product_id, tag):
    # Logic to fetch current tags, append new tag, and save.
    pass 

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)