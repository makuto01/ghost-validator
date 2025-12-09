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

# 2. THE BRAIN (With Debugging)
def audit_and_fix_product(product_id, title, description, variants):
    print(f"⚙️ Auditing {title}...")
    
    payload = {}
    error_tag = None
    
    # --- CHECK 1: DESCRIPTION ---
    if not description or len(description) < 10:
        print("❌ Description empty. Generating AI description...")
        try:
            prompt = f"Write a 3-sentence exciting sales description for: {title}. Use HTML <p> tags."
            
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            new_desc = response.choices[0].message.content
            payload["body_html"] = new_desc
            print("✅ AI Description Generated.")
            
        except Exception as e:
            # THIS IS THE NEW PART: Catch the error and prepare it as a tag
            error_message = str(e)
            print(f"⚠️ OpenAI Error: {error_message}")
            
            if "insufficient_quota" in error_message:
                error_tag = "ERR: No-Credits"
            elif "invalid_api_key" in error_message:
                error_tag = "ERR: Bad-Key"
            else:
                # Take first 15 chars of error
                clean_err = ''.join(e for e in error_message if e.isalnum())[:15]
                error_tag = f"ERR: {clean_err}"

    # --- CHECK 2: WEIGHT TAGS ---
    has_weight_issue = False
    for variant in variants:
        if float(variant.get('weight', 0)) == 0:
            has_weight_issue = True
            break
            
    # --- SAVE TAGS ---
    # We combine weight error AND debug error
    if has_weight_issue:
        add_tag_to_product(product_id, "Validation-Error: Missing Weight")
        
    if error_tag:
        add_tag_to_product(product_id, error_tag)

    # --- SAVE DESCRIPTION ---
    if payload:
        update_shopify_product(product_id, payload)

# --- HELPER FUNCTIONS ---

def update_shopify_product(product_id, payload):
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": ACCESS_TOKEN, "Content-Type": "application/json"}
    requests.put(url, json={"product": payload}, headers=headers)

def add_tag_to_product(product_id, tag):
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": ACCESS_TOKEN}
    try:
        r = requests.get(url, headers=headers)
        current_tags = r.json()['product']['tags']
        if tag not in current_tags:
            new_tags = f"{current_tags}, {tag}" if current_tags else tag
            requests.put(url, json={"product": {"id": product_id, "tags": new_tags}}, headers=headers)
    except Exception:
        pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)