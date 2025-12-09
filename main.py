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
    background_tasks.add_task(audit_and_fix_product, product_id, title, description, variants, vendor)
    
    return {"status": "received"}

# 2. THE BRAIN: The Logic to Check and Fix Data
def audit_and_fix_product(product_id, title, description, variants, vendor):
    print(f"⚙️ Auditing '{title}'...")
    
    fixes_needed = False
    new_data = {}

    # --- HELPER FUNCTIONS ---
    def get_google_category(title, description):
        """
        Uses AI to find the strict Google Product Taxonomy ID.
        """
        print(f"🧠 AI Categorizing: {title}")
        prompt = f"""
        You are a Google Shopping Taxonomy expert. 
        Map the following product to the MOST specific Google Product Category ID.
        
        Product Title: "{title}"
        Product Description: "{description}"
        
        Rules:
        1. Respond with the Numeric ID ONLY (e.g., 536).
        2. Do not write words.
        3. If unsure, use the general category for the niche.
        """
        try:
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            category_id = response.choices[0].message.content.strip()
            if category_id.isdigit():
                return category_id
            else:
                print(f"⚠️ AI returned non-digit: {category_id}")
                return None
        except Exception as e:
            print(f"❌ AI Error: {e}")
            return None

    def sanitize_html(html_content):
        """
        Strips non-standard characters and reformats HTML.
        Simple regex version for now.
        """
        if not html_content:
            return ""
        # Remove weird non-ascii chars (basic cleaning)
        cleaned = re.sub(r'[^\x00-\x7F]+', ' ', html_content)
        # Remove empty paragraphs
        cleaned = re.sub(r'<p>\s*</p>', '', cleaned)
        # Ensure paragraphs are well-formed (very basic)
        if "<p>" not in cleaned:
             cleaned = f"<p>{cleaned}</p>"
        return cleaned

    def check_gtin(variants, vendor):
        """
        Checks for missing GTINs.
        If missing:
          - Known brand -> Tag error
          - Custom brand -> Generate custom SKU
        Returns: (needs_update, updated_variants_list, tag_to_add)
        """
        updated_variants = []
        variants_changed = False
        tag_to_add = None
        
        known_brands = ["Nike", "Adidas", "Samsung", "Sony", "Apple"] # Example list

        for variant in variants:
            barcode = variant.get('barcode', '')
            if not barcode:
                if vendor in known_brands:
                    print(f"⚠️ Missing GTIN for known brand {vendor}. Flagging.")
                    tag_to_add = "Error: Missing GTIN"
                else:
                    # Custom brand - generate internal SKU/Barcode if missing
                    # Note: We are putting this in 'sku' or 'barcode' depending on intent. 
                    # Prompt said "specialized SKU structure". Let's update SKU if empty, or Barcode?
                    # Usually 'barcode' field needs a valid GTIN. If custom, maybe we leave barcode empty 
                    # but ensure SKU is set? Let's assume we gen a custom SKU.
                    if not variant.get('sku'):
                        new_sku = f"CUST-{vendor[:3].upper()}-{random.randint(1000, 9999)}"
                        variant['sku'] = new_sku
                        print(f"🔧 Generated custom SKU: {new_sku}")
                        variants_changed = True
            updated_variants.append(variant)
            
        return variants_changed, updated_variants, tag_to_add

    # --- EXECUTE CHECKS ---

    # CHECK 1: Sanitize Description
    sanitized_desc = sanitize_html(description)
    if sanitized_desc != description:
        print("🧹 Description sanitized.")
        new_data["body_html"] = sanitized_desc
        fixes_needed = True
        description = sanitized_desc # Update local var for next checks

    # CHECK 2: Description Quality (AI)
    if not description or len(description) < 50:
        print("❌ Description too short. Generating AI description...")
        prompt = f"Write a professional, SEO-optimized e-commerce description for a product titled '{title}'. Format as HTML."
        try:
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            new_description = response.choices[0].message.content
            new_data["body_html"] = new_description
            fixes_needed = True
            description = new_description
        except Exception as e:
            print(f"❌ OpenAI Error: {e}")

    # CHECK 3: Google Product Category
    google_cat_id = get_google_category(title, description)
    if google_cat_id:
        print(f"✅ Determined Google Category ID: {google_cat_id}")
        # Note: Metafields are updated separately or nested in some API versions. 
        # For simplicity/safety, we will add it to a separate metafield update list 
        # or structure it so the update function handles it.
        # Assuming Shopify API version allows nested metafields in product update:
        new_data["metafields"] = [
            {
                "namespace": "google",
                "key": "google_product_category",
                "value": int(google_cat_id),
                "type": "integer",
                "owner_resource": "product", # Required for some endpoints
                "owner_id": product_id
            }
        ]
        fixes_needed = True

    # CHECK 4: GTIN / Variants
    variants_changed, updated_variants, tag_error = check_gtin(variants, vendor)
    if variants_changed:
        new_data["variants"] = updated_variants
        fixes_needed = True
    
    if tag_error:
        add_tag_to_product(product_id, tag_error)

    # CHECK 5: Weight Check
    for variant in variants:
        if variant.get('weight', 0) == 0:
            print(f"⚠️ Variant {variant.get('id')} has 0 weight. Flagging.")
            add_tag_to_product(product_id, "Validation-Error: Missing Weight")
            # We don't stop here, we continue to allow other fixes to proceed
    
    # --- ACTION ---
    if fixes_needed:
        update_shopify_product(product_id, new_data)

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