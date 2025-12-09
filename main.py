import os
import requests
import openai
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
import uvicorn
from sqlalchemy import create_engine, Column, String
from sqlalchemy.orm import sessionmaker, declarative_base

# --- CONFIGURATION ---
DATABASE_URL = os.getenv("DATABASE_URL")
# Fix for Render's URL format if necessary (postgres:// -> postgresql://)
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY")
SHOPIFY_API_SECRET = os.getenv("SHOPIFY_API_SECRET")
APP_URL = os.getenv("APP_URL") # e.g. https://ghost-validator.onrender.com
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

app = FastAPI()

# --- DATABASE SETUP ---
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define our "Shop" table
class Shop(Base):
    __tablename__ = "shops"
    shop_url = Column(String, primary_key=True, index=True)
    access_token = Column(String)

# Create tables if they don't exist
Base.metadata.create_all(bind=engine)

# --- HELPER: DATABASE ACCESS ---
def get_shop_token(shop_url):
    db = SessionLocal()
    shop = db.query(Shop).filter(Shop.shop_url == shop_url).first()
    db.close()
    return shop.access_token if shop else None

def save_shop_token(shop_url, token):
    db = SessionLocal()
    shop = db.query(Shop).filter(Shop.shop_url == shop_url).first()
    if not shop:
        shop = Shop(shop_url=shop_url, access_token=token)
        db.add(shop)
    else:
        shop.access_token = token
    db.commit()
    db.close()

# --- ROUTE 1: INSTALLATION (Start) ---
@app.get("/auth")
def auth(shop: str):
    # Redirect merchant to Shopify permission screen
    scopes = "read_products,write_products"
    redirect_uri = f"{APP_URL}/auth/callback"
    install_url = f"https://{shop}/admin/oauth/authorize?client_id={SHOPIFY_API_KEY}&scope={scopes}&redirect_uri={redirect_uri}"
    return RedirectResponse(install_url)

# --- ROUTE 2: CALLBACK (Finish) ---
@app.get("/auth/callback")
def callback(shop: str, code: str):
    # Exchange the temporary code for a permanent Access Token
    url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_API_KEY,
        "client_secret": SHOPIFY_API_SECRET,
        "code": code
    }
    response = requests.post(url, json=payload)
    token = response.json().get("access_token")
    
    if token:
        save_shop_token(shop, token)
        print(f"✅ Installed on {shop}")
        return RedirectResponse(f"https://{shop}/admin/apps")
    else:
        return {"error": "Failed to get token"}

# --- ROUTE 3: WEBHOOK (The Listener) ---
@app.post("/webhook/product-update")
async def product_webhook(request: Request, background_tasks: BackgroundTasks):
    # 1. Identify which store sent this
    shop_domain = request.headers.get("X-Shopify-Shop-Domain")
    
    data = await request.json()
    product_id = data.get("id")
    title = data.get("title")
    description = data.get("body_html")
    variants = data.get("variants", [])
    
    print(f"🕵️ Received form {shop_domain}: {title}")
    
    # 2. Get the specific token for THIS store
    token = get_shop_token(shop_domain)
    
    if token:
        background_tasks.add_task(audit_and_fix_product, shop_domain, token, product_id, title, description, variants)
    else:
        print(f"❌ No token found for {shop_domain}")
    
    return {"status": "received"}

# --- THE BRAIN (AI Logic) ---
def audit_and_fix_product(shop_domain, token, product_id, title, description, variants):
    print(f"⚙️ Auditing {title} on {shop_domain}...")
    
    payload = {}
    
    # CHECK 1: Description
    if not description or len(description) < 10:
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
            print(f"⚠️ OpenAI Error: {e}")

    # CHECK 2: Weight
    has_weight_issue = False
    for variant in variants:
        if float(variant.get('weight', 0)) == 0:
            has_weight_issue = True
            break
            
    if has_weight_issue:
        add_tag_to_product(shop_domain, token, product_id, "Validation-Error: Missing Weight")

    # SAVE
    if payload:
        update_shopify_product(shop_domain, token, product_id, payload)

# --- HELPERS ---
def update_shopify_product(shop, token, product_id, payload):
    url = f"https://{shop}/admin/api/2023-10/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    requests.put(url, json={"product": payload}, headers=headers)

def add_tag_to_product(shop, token, product_id, tag):
    url = f"https://{shop}/admin/api/2023-10/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": token}
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