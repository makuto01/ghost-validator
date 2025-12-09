# main.py
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
import openai
import requests
import os

app = FastAPI()

# CONFIGURATION
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_KEY

# 1. THE LISTENER
@app.post("/webhook/product-update")
async def product_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}
    
    product_id = data.get("id")
    title = data.get("title")
    description = data.get("body_html")
    variants = data.get("variants", [])
    
    print(f"🕵️ Received Product: {title} ({product_id})")
    
    # Send to the Brain
    background_tasks.add_task(audit_and_fix_product, product_id, title, description, variants)
    
    return {"status": "received"}

# 2. THE BRAIN (Consolidated Logic)
def audit_and_fix_product(product_id, title, description, variants):
    print(f"⚙️ Auditing {title}...")
    
    payload = {}
    
    # --- CHECK 1: DESCRIPTION ---
    # We check if it is None (empty) OR shorter than 10 characters
    if not description or len(description) < 10:
        print("❌ Description empty/short. Generating AI description...")
        try:
            prompt = f"Write a 3-sentence exciting sales description for a product named: {title}. Format it with HTML paragraph tags <p>."
            
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            new_desc = response.choices[0].message.content
            payload["body_html"] = new_desc
            print("✅ AI Description Generated.")
        except Exception as e:
            print(f"⚠️ OpenAI Error: {e}")
            # Check if it's a quota issue
            if "insufficient_quota" in str(e):
                print("‼️ CRITICAL: OpenAI credits expired. Check billing at platform.openai.com")

    # --- CHECK 2: WEIGHT TAGS ---
    has_weight_issue = False
    for variant in variants:
        if float(variant.get('weight', 0)) == 0:
            has_weight_issue = True
            break
            
    if has_weight_issue:
        print("⚠️ Found 0kg weight. Queueing tag.")
        # We handle tags carefully to not delete old ones
        add_tag_to_product(product_id, "Validation-Error: Missing Weight")

    # --- SAVE UPDATES ---
    if payload:
        print(f"💾 Saving description update...")
        update_shopify_product(product_id, payload)
    else:
        print("✨ Description looked good. No update needed.")

# --- HELPER FUNCTIONS ---

def update_shopify_product(product_id, payload):
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/products/{product_id}.json"
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    response = requests.put(url, json={"product": payload}, headers=headers)
    if response.status_code == 200:
        print(f"✅ Product {product_id} updated!")
    else:
        print(f"❌ Update Failed: {response.text}")

def add_tag_to_product(product_id, tag):
    # Safer tagging: Fetch existing tags first, then append
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": ACCESS_TOKEN}
    
    try:
        r = requests.get(url, headers=headers)
        current_tags = r.json()['product']['tags']
        
        if tag not in current_tags:
            new_tags = f"{current_tags}, {tag}" if current_tags else tag
            # Update just the tags
            requests.put(url, json={"product": {"id": product_id, "tags": new_tags}}, headers=headers)
            print(f"✅ Tag '{tag}' added.")
    except Exception as e:
        print(f"❌ Tagging Error: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)