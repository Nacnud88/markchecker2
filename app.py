from flask import Flask, request, jsonify, render_template, Response, stream_with_context
import requests
import traceback
import json
import time
import random
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import gc
import sqlite3
import uuid
import threading
import os
import atexit
from datetime import datetime, timedelta
from config import get_config

# Load configuration
cfg = get_config()
MAX_WORKERS = cfg.MAX_WORKERS
CHUNK_SIZE = cfg.CHUNK_SIZE
REQUEST_TIMEOUT = cfg.REQUEST_TIMEOUT
GC_ENABLED = cfg.GC_ENABLED
DB_PATH = cfg.DB_PATH
SESSION_CLEANUP_HOURS = cfg.SESSION_CLEANUP_HOURS

app = Flask(__name__, static_folder="static", template_folder="templates")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

sys.setrecursionlimit(2000)

# Database lock for thread safety
db_lock = threading.Lock()

def init_database():
    """Initialize SQLite database with required tables"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active',
                total_terms INTEGER DEFAULT 0,
                processed_terms INTEGER DEFAULT 0,
                total_products INTEGER DEFAULT 0
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                search_term TEXT,
                found BOOLEAN,
                product_id TEXT,
                retailer_product_id TEXT,
                name TEXT,
                brand TEXT,
                available BOOLEAN,
                category TEXT,
                image_url TEXT,
                current_price REAL,
                original_price REAL,
                discount_percentage INTEGER,
                unit_price REAL,
                unit_label TEXT,
                currency TEXT,
                offers TEXT,  -- JSON string
                not_found_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
        ''')
        
        # Create indices for better performance
        conn.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON products (session_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_found ON products (found)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_available ON products (available)')
        
        conn.commit()

def cleanup_old_sessions():
    """Clean up sessions older than SESSION_CLEANUP_HOURS"""
    try:
        cutoff_time = datetime.now() - timedelta(hours=SESSION_CLEANUP_HOURS)
        with sqlite3.connect(DB_PATH) as conn:
            # Get old session IDs
            cursor = conn.execute(
                'SELECT session_id FROM sessions WHERE created_at < ?', 
                (cutoff_time,)
            )
            old_sessions = [row[0] for row in cursor.fetchall()]
            
            if old_sessions:
                # Delete products for old sessions
                placeholders = ','.join('?' * len(old_sessions))
                conn.execute(f'DELETE FROM products WHERE session_id IN ({placeholders})', old_sessions)
                
                # Delete old sessions
                conn.execute(f'DELETE FROM sessions WHERE session_id IN ({placeholders})', old_sessions)
                
                conn.commit()
                logging.info(f"Cleaned up {len(old_sessions)} old sessions")
    except Exception as e:
        logging.error(f"Error cleaning up old sessions: {str(e)}")

def create_session():
    """Create a new session and return session ID"""
    session_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO sessions (session_id) VALUES (?)', 
            (session_id,)
        )
        conn.commit()
    return session_id

def update_session_progress(session_id, processed_terms=None, total_products=None):
    """Update session progress"""
    with sqlite3.connect(DB_PATH) as conn:
        updates = ['last_accessed = CURRENT_TIMESTAMP']
        values = []
        
        if processed_terms is not None:
            updates.append('processed_terms = ?')
            values.append(processed_terms)
            
        if total_products is not None:
            updates.append('total_products = ?')
            values.append(total_products)
            
        values.append(session_id)
        
        conn.execute(
            f'UPDATE sessions SET {", ".join(updates)} WHERE session_id = ?',
            values
        )
        conn.commit()

def store_products_batch(session_id, products_data):
    """Store a batch of products in the database"""
    with sqlite3.connect(DB_PATH) as conn:
        for product in products_data:
            # Convert offers to JSON string if present
            offers_json = json.dumps(product.get('offers', [])) if product.get('offers') else None
            
            conn.execute('''
                INSERT INTO products (
                    session_id, search_term, found, product_id, retailer_product_id,
                    name, brand, available, category, image_url, current_price,
                    original_price, discount_percentage, unit_price, unit_label,
                    currency, offers, not_found_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                product.get('searchTerm'),
                product.get('found', False),
                product.get('productId'),
                product.get('retailerProductId'),
                product.get('name'),
                product.get('brand'),
                product.get('available', False),
                product.get('category', ''),
                product.get('imageUrl'),
                product.get('currentPrice'),
                product.get('originalPrice'),
                product.get('discountPercentage'),
                product.get('unitPrice'),
                product.get('unitLabel'),
                product.get('currency', 'CAD'),
                offers_json,
                product.get('notFoundMessage')
            ))
        conn.commit()

def get_session_products(session_id):
    """Retrieve all products for a session from database"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute('''
            SELECT * FROM products WHERE session_id = ? ORDER BY id
        ''', (session_id,))
        
        products = []
        for row in cursor.fetchall():
            product = dict(row)
            
            # Parse offers JSON
            if product['offers']:
                try:
                    product['offers'] = json.loads(product['offers'])
                except json.JSONDecodeError:
                    product['offers'] = []
            else:
                product['offers'] = []
                
            # Convert database fields back to expected format
            product['searchTerm'] = product.pop('search_term')
            product['productId'] = product.pop('product_id')
            product['retailerProductId'] = product.pop('retailer_product_id')
            product['imageUrl'] = product.pop('image_url')
            product['currentPrice'] = product.pop('current_price')
            product['originalPrice'] = product.pop('original_price')
            product['discountPercentage'] = product.pop('discount_percentage')
            product['unitPrice'] = product.pop('unit_price')
            product['unitLabel'] = product.pop('unit_label')
            product['notFoundMessage'] = product.pop('not_found_message')
            
            # Remove database-specific fields
            del product['id']
            del product['session_id']
            del product['created_at']
            
            products.append(product)
            
        return products

def get_session_stats(session_id):
    """Get statistics for a session"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute('''
            SELECT 
                COUNT(*) as total_products,
                SUM(CASE WHEN found = 1 THEN 1 ELSE 0 END) as found_products,
                SUM(CASE WHEN found = 0 THEN 1 ELSE 0 END) as not_found_products
            FROM products WHERE session_id = ?
        ''', (session_id,))
        
        stats = cursor.fetchone()
        return {
            'total_products': stats[0] if stats else 0,
            'found_products': stats[1] if stats else 0,
            'not_found_products': stats[2] if stats else 0
        }

def cleanup_session(session_id):
    """Clean up all data for a specific session"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('DELETE FROM products WHERE session_id = ?', (session_id,))
            conn.execute('DELETE FROM sessions WHERE session_id = ?', (session_id,))
            conn.commit()
            logging.info(f"Cleaned up session: {session_id}")
    except Exception as e:
        logging.error(f"Error cleaning up session {session_id}: {str(e)}")

@app.route('/')
def index():
    """Serve the main HTML page"""
    return render_template('index.html')

# Keep existing functions (get_region_info, fallback_region_extraction, parse_search_terms, etc.)
def get_region_info(session_id):
    """Get region ID and details from Voila API using session ID"""
    try:
        url = "https://voila.ca/api/cart/v1/carts/active"
        
        headers = {
            "accept": "application/json; charset=utf-8",
            "client-route-id": "d55f7f13-4217-4320-907e-eadd09051a7c",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        cookies = {
            "global_sid": session_id
        }
        
        response = requests.get(url, headers=headers, cookies=cookies, timeout=15)
        
        if response.status_code == 200:
            try:
                data = response.json()
                region_info = {
                    "regionId": data.get("regionId"),
                    "nickname": None,
                    "displayAddress": None,
                    "postalCode": None
                }
                
                if "defaultCheckoutGroup" in data and "delivery" in data["defaultCheckoutGroup"]:
                    delivery = data["defaultCheckoutGroup"]["delivery"]
                    if "addressDetails" in delivery:
                        address = delivery["addressDetails"]
                        region_info["nickname"] = address.get("nickname")
                        region_info["displayAddress"] = address.get("displayAddress")
                        region_info["postalCode"] = address.get("postalCode")
                        
                if region_info["regionId"]:
                    if not region_info["nickname"] and region_info["regionId"]:
                        region_info["nickname"] = f"Region {region_info['regionId']}"
                    return region_info
                
                return fallback_region_extraction(response.text)
                
            except ValueError:
                return fallback_region_extraction(response.text)
        
        return {
            "regionId": "unknown",
            "nickname": "Unknown Region",
            "displayAddress": "No address available",
            "postalCode": "Unknown"
        }
    
    except requests.exceptions.Timeout:
        print("Request to Voila API timed out")
        return {
            "regionId": "unknown",
            "nickname": "Timeout Error",
            "displayAddress": "API request timed out",
            "postalCode": "Unknown"
        }
    except Exception as e:
        print(f"Error getting region info: {str(e)}")
        return {
            "regionId": "unknown",
            "nickname": "Error",
            "displayAddress": str(e)[:50],
            "postalCode": "Unknown"
        }

def fallback_region_extraction(text_response):
    """Extract region info using regex as a fallback method"""
    region_info = {
        "regionId": None,
        "nickname": None,
        "displayAddress": None,
        "postalCode": None
    }
    
    region_id_match = re.search(r'"regionId"\s*:\s*"?(\d+)"?', text_response)
    if region_id_match:
        region_info["regionId"] = region_id_match.group(1)
        
    nickname_match = re.search(r'"nickname"\s*:\s*"([^"]+)"', text_response)
    if nickname_match:
        region_info["nickname"] = nickname_match.group(1)
        
    addr_match = re.search(r'"displayAddress"\s*:\s*"([^"]+)"', text_response)
    if addr_match:
        region_info["displayAddress"] = addr_match.group(1)
        
    postal_match = re.search(r'"postalCode"\s*:\s*"([^"]+)"', text_response)
    if postal_match:
        region_info["postalCode"] = postal_match.group(1)
        
    if not region_info["regionId"]:
        alt_region_match = re.search(r'"region"\s*:\s*{\s*"id"\s*:\s*"?(\d+)"?', text_response)
        if alt_region_match:
            region_info["regionId"] = alt_region_match.group(1)
    
    if not region_info["nickname"] and region_info["regionId"]:
        region_info["nickname"] = f"Region {region_info['regionId']}"
        
    return region_info

def parse_search_terms(search_input):
    """Parse search input into individual search terms"""
    contains_ea_codes = False
    
    if 'EA' in search_input:
        continuous_ea_pattern = r'(\d+EA)'
        search_input = re.sub(continuous_ea_pattern, r'\1 ', search_input)
        contains_ea_codes = True

    terms = []
    if ',' in search_input or '\n' in search_input:
        terms = re.split(r'[,\n]', search_input)
    else:
        ea_codes = re.findall(r'\b\d+EA\b', search_input)
        if ea_codes:
            terms = ea_codes
            contains_ea_codes = True
        else:
            if len(search_input) > 50 and ' ' in search_input:
                terms = search_input.split()
            else:
                terms = [search_input]
    
    terms = [term.strip() for term in terms if term.strip()]
    total_terms = len(terms)
    
    seen = set()
    unique_terms = []
    duplicates = []
    
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)
        else:
            duplicates.append(term)
    
    duplicate_count = total_terms - len(unique_terms)
    
    return unique_terms, duplicate_count, contains_ea_codes, duplicates

# Keep existing fetch_product_data, extract_product_fields, extract_product_info functions...
def fetch_product_data(product_id, session_id):
    """Fetch product data from Voila.ca API using the provided session ID"""
    try:
        url = "https://voila.ca/api/v6/products/search"

        headers = {
            "accept": "application/json; charset=utf-8",
            "client-route-id": "5fa0016c-9764-4e09-9738-12c33fb47fc2",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        cookies = {
            "global_sid": session_id
        }

        params = {
            "term": product_id
        }

        response = requests.get(url, headers=headers, params=params, cookies=cookies, timeout=REQUEST_TIMEOUT)

        if response.status_code != 200:
            print(f"API returned status code {response.status_code} for term {product_id}")
            return None
        
        text_response = response.text
        
        if '"productId"' not in text_response and '"retailerProductId"' not in text_response:
            print(f"No products found for term {product_id}")
            return {"entities": {"product": {}}}
        
        result = {
            "entities": {
                "product": {}
            }
        }
        
        product_ids = []
        product_id_matches = re.finditer(r'"productId"\s*:\s*"([^"]+)"', text_response)
        for match in product_id_matches:
            product_ids.append(match.group(1))
        
        if not product_ids:
            retailer_id_matches = re.finditer(r'"retailerProductId"\s*:\s*"([^"]+)"', text_response)
            for match in retailer_id_matches:
                product_ids.append("retailer_" + match.group(1))
        
        for prod_id in product_ids:
            search_pattern = f'"productId"\\s*:\\s*"{prod_id}"' if not prod_id.startswith("retailer_") else f'"retailerProductId"\\s*:\\s*"{prod_id[9:]}"'
            id_match = re.search(search_pattern, text_response)
            
            if id_match:
                obj_start = text_response.rfind("{", 0, id_match.start())
                if obj_start >= 0:
                    brace_count = 1
                    obj_end = obj_start + 1
                    
                    while brace_count > 0 and obj_end < len(text_response):
                        if text_response[obj_end] == "{":
                            brace_count += 1
                        elif text_response[obj_end] == "}":
                            brace_count -= 1
                        obj_end += 1
                    
                    if brace_count == 0:
                        product_json = text_response[obj_start:obj_end]
                        
                        try:
                            import json
                            product_data = json.loads(product_json)
                            
                            actual_id = prod_id if not prod_id.startswith("retailer_") else product_data.get("productId", prod_id)
                            result["entities"]["product"][actual_id] = product_data
                        except json.JSONDecodeError as e:
                            print(f"Error parsing product JSON for {prod_id}: {str(e)}")
                            fallback_product = extract_product_fields(product_json, prod_id)
                            if fallback_product:
                                result["entities"]["product"][prod_id] = fallback_product
        
        if not result["entities"]["product"] and product_ids:
            print(f"Warning: Found {len(product_ids)} product IDs but couldn't parse them properly")
            for prod_id in product_ids:
                clean_id = prod_id[9:] if prod_id.startswith("retailer_") else prod_id
                result["entities"]["product"][clean_id] = {
                    "productId": clean_id,
                    "retailerProductId": product_id,
                    "name": f"Product {clean_id}",
                    "available": True
                }
        
        return result
        
    except requests.exceptions.Timeout:
        print(f"Request timeout for term {product_id}")
        return None
    except RecursionError:
        print(f"Recursion error fetching product data for {product_id}")
        return {"entities": {"product": {}}}
    except Exception as e:
        print(f"Unexpected error fetching product data for {product_id}: {str(e)}")
        return None

def extract_product_fields(product_json, product_id):
    """Extract essential product fields using regex when JSON parsing fails"""
    try:
        clean_id = product_id[9:] if product_id.startswith("retailer_") else product_id
        
        product = {
            "productId": clean_id,
            "retailerProductId": None,
            "name": None,
            "available": True,
            "brand": None,
            "categoryPath": [],
            "price": {
                "current": {
                    "amount": None,
                    "currency": "CAD"
                }
            }
        }
        
        retailer_id_match = re.search(r'"retailerProductId"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', product_json)
        if retailer_id_match:
            product["retailerProductId"] = retailer_id_match.group(1).replace('\\"', '"')
        
        name_match = re.search(r'"name"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', product_json)
        if name_match:
            product["name"] = name_match.group(1).replace('\\"', '"').replace('\\\\', '\\')
        
        brand_match = re.search(r'"brand"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', product_json)
        if brand_match:
            product["brand"] = brand_match.group(1).replace('\\"', '"').replace('\\\\', '\\')
        
        available_match = re.search(r'"available"\s*:\s*(true|false)', product_json)
        if available_match:
            product["available"] = available_match.group(1) == "true"
        
        price_match = re.search(r'"current"\s*:\s*{\s*"amount"\s*:\s*"([^"]+)"', product_json)
        if price_match:
            product["price"]["current"]["amount"] = price_match.group(1)
        
        image_match = re.search(r'"src"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', product_json)
        if image_match:
            product["image"] = {"src": image_match.group(1).replace('\\"', '"').replace('\\\\', '\\')}
        
        return product
    except Exception as e:
        print(f"Error in fallback extraction: {str(e)}")
        return None

def extract_product_info(product, search_term=None):
    """Extract product info in a memory-efficient way"""
    product_info = {
        "found": True,
        "searchTerm": search_term,
        "productId": product.get("productId"),
        "retailerProductId": product.get("retailerProductId"),
        "name": product.get("name"),
        "brand": product.get("brand"),
        "available": product.get("available", False),
        "imageUrl": None,
        "currency": "CAD"
    }
    
    if "image" in product and isinstance(product["image"], dict):
        product_info["imageUrl"] = product["image"].get("src")
    
    if "categoryPath" in product and isinstance(product["categoryPath"], list):
        product_info["category"] = " > ".join(product["categoryPath"])
    else:
        product_info["category"] = ""
    
    if "price" in product and isinstance(product["price"], dict):
        price_info = product["price"]
        
        if "current" in price_info and isinstance(price_info["current"], dict):
            product_info["currentPrice"] = price_info["current"].get("amount")
            product_info["currency"] = price_info["current"].get("currency", "CAD")
            
        if "original" in price_info and isinstance(price_info["original"], dict):
            product_info["originalPrice"] = price_info["original"].get("amount")
            
            if ("currentPrice" in product_info and "originalPrice" in product_info and
                product_info["currentPrice"] is not None and product_info["originalPrice"] is not None):
                try:
                    current_price = float(product_info["currentPrice"])
                    original_price = float(product_info["originalPrice"])
                    
                    if original_price > current_price:
                        discount = ((original_price - current_price) / original_price * 100)
                        product_info["discountPercentage"] = round(discount)
                except (ValueError, TypeError):
                    pass
                    
        if "unit" in price_info and isinstance(price_info["unit"], dict):
            if "current" in price_info["unit"] and isinstance(price_info["unit"]["current"], dict):
                product_info["unitPrice"] = price_info["unit"]["current"].get("amount")
            product_info["unitLabel"] = price_info["unit"].get("label")
    
    if "offers" in product and isinstance(product["offers"], list):
        offers = product.get("offers", [])
        product_info["offers"] = offers[:5] if offers else []
        
    if "offer" in product:
        product_info["primaryOffer"] = product.get("offer")
    
    return product_info

def process_term(term, session_id, limit, is_article_search=True):
    """Process a single search term and return products found"""
    try:
        raw_data = fetch_product_data(term, session_id)
        
        if not raw_data:
            return {
                "found": False,
                "searchTerm": term,
                "productId": None,
                "retailerProductId": None,
                "name": f"Article Not Found: {term}",
                "brand": None,
                "available": False,
                "category": "",
                "imageUrl": None,
                "notFoundMessage": f"The article \"{term}\" was not found. It may not be published yet or could be a typo."
            }, 0
        
        if "entities" in raw_data and "product" in raw_data["entities"]:
            product_entities = raw_data["entities"]["product"]
            
            if product_entities:
                total_found = len(product_entities)
                
                if is_article_search:
                    product_keys = list(product_entities.keys())[:1]
                else:
                    if limit != 'all':
                        try:
                            max_items = int(limit) if isinstance(limit, str) else limit
                            product_keys = list(product_entities.keys())[:max_items]
                        except (ValueError, TypeError):
                            product_keys = list(product_entities.keys())[:10]
                    else:
                        product_keys = list(product_entities.keys())[:50]
                
                if not is_article_search and len(product_keys) > 0:
                    all_products = []
                    
                    for product_id in product_keys:
                        product = product_entities[product_id]
                        product_info = extract_product_info(product, term)
                        all_products.append(product_info)
                    
                    return all_products, total_found
                
                elif product_keys:
                    product_id = product_keys[0]
                    product = product_entities[product_id]
                    
                    try:
                        product_info = extract_product_info(product, term)
                        return product_info, total_found
                    except RecursionError:
                        print(f"Recursion error processing product for term {term}")
                        return {
                            "found": True,
                            "searchTerm": term,
                            "productId": product.get("productId"),
                            "name": product.get("name", "Product Name Unavailable"),
                            "brand": product.get("brand", "Brand Unavailable"),
                            "available": False,
                            "category": "",
                            "imageUrl": None,
                            "currentPrice": None,
                            "message": "Product data too complex to fully process"
                        }, 1
        
        return {
            "found": False,
            "searchTerm": term,
            "productId": None,
            "retailerProductId": None,
            "name": f"Article Not Found: {term}",
            "brand": None,
            "available": False,
            "category": "",
            "imageUrl": None,
            "notFoundMessage": f"The article \"{term}\" was not found. It may not be published yet or could be a typo."
        }, 0
    
    except RecursionError:
        print(f"Recursion error processing term {term}")
        return {
            "found": False,
            "searchTerm": term,
            "productId": None,
            "retailerProductId": None,
            "name": f"Processing Error: {term}",
            "brand": None,
            "available": False,
            "category": "",
            "imageUrl": None,
            "notFoundMessage": "Data too complex to process. Try a more specific search term."
        }, 0
    except Exception as e:
        print(f"Error processing term {term}: {str(e)}")
        return {
            "found": False,
            "searchTerm": term,
            "productId": None,
            "retailerProductId": None,
            "name": f"Article Not Found: {term}",
            "brand": None,
            "available": False,
            "category": "",
            "imageUrl": None,
            "notFoundMessage": f"Error processing the article. Please try again."
        }, 0

@app.route('/api/start-search', methods=['POST'])
def start_search():
    """Start a new search session and return session ID"""
    try:
        # Clean up old sessions first
        cleanup_old_sessions()
        
        data = request.json
        if not data:
            return jsonify({"error": "No request data provided"}), 400

        search_term = data.get('searchTerm')
        session_id_from_voila = data.get('sessionId')
        search_type = data.get('searchType', 'article')

        if not search_term:
            return jsonify({"error": "Search term is required"}), 400
        if not session_id_from_voila:
            return jsonify({"error": "Session ID is required"}), 400

        # Get region info
        region_info = get_region_info(session_id_from_voila)
        if not region_info or not region_info.get("regionId"):
            return jsonify({"error": "Could not determine region from session ID"}), 400

        # Parse search terms
        individual_terms, duplicate_count, contains_ea_codes, duplicates = parse_search_terms(search_term)
        
        # Create new session
        session_id = create_session()
        
        # Update session with total terms
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                'UPDATE sessions SET total_terms = ? WHERE session_id = ?',
                (len(individual_terms), session_id)
            )
            conn.commit()

        return jsonify({
            "session_id": session_id,
            "region_info": region_info,
            "parsed_terms": individual_terms,
            "duplicate_count": duplicate_count,
            "duplicates": duplicates,
            "contains_ea_codes": contains_ea_codes,
            "search_type": search_type,
            "total_terms": len(individual_terms),
            "total_chunks": (len(individual_terms) + CHUNK_SIZE - 1) // CHUNK_SIZE
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/process-chunk', methods=['POST'])
def process_chunk():
    """Process a chunk of search terms"""
    try:
        data = request.json
        session_id = data.get('sessionId')
        voila_session_id = data.get('voilaSessionId')
        chunk_index = data.get('chunkIndex', 0)
        search_terms = data.get('searchTerms', [])
        limit = data.get('limit', 'all')
        search_type = data.get('searchType', 'article')

        if not session_id or not voila_session_id or not search_terms:
            return jsonify({"error": "Missing required parameters"}), 400

        is_article_search = search_type == 'article'
        chunk_products = []
        total_found = 0
        processed_count = 0

        # Process terms in this chunk
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_term, term, voila_session_id, limit, is_article_search): term for term in search_terms}
            
            for future in as_completed(futures):
                term = futures[future]
                processed_count += 1
                
                try:
                    product_result, term_total_found = future.result()
                    total_found += term_total_found
                    
                    if isinstance(product_result, list):
                        chunk_products.extend(product_result)
                    elif product_result:
                        chunk_products.append(product_result)
                        
                except Exception as e:
                    logging.error(f"Error processing term {term}: {str(e)}")
                    chunk_products.append({
                        "found": False,
                        "searchTerm": term,
                        "productId": None,
                        "retailerProductId": None,
                        "name": f"Article Not Found: {term}",
                        "brand": None,
                        "available": False,
                        "category": "",
                        "imageUrl": None,
                        "notFoundMessage": f"Error processing the article. Please try again."
                    })

        # Store products in database
        if chunk_products:
            store_products_batch(session_id, chunk_products)

        # Update session progress
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                'SELECT processed_terms, total_products FROM sessions WHERE session_id = ?',
                (session_id,)
            )
            result = cursor.fetchone()
            
            if result:
                current_processed = result[0] or 0
                current_total_products = result[1] or 0
                
                new_processed = current_processed + processed_count
                new_total_products = current_total_products + len(chunk_products)
                
                update_session_progress(session_id, new_processed, new_total_products)

        # Run garbage collection
        if GC_ENABLED:
            collected = gc.collect()
            logging.debug(f"Garbage collection: {collected} objects collected")

        return jsonify({
            "chunk_index": chunk_index,
            "processed_count": processed_count,
            "products_found": len(chunk_products),
            "total_found": total_found
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/get-results/<session_id>')
def get_results(session_id):
    """Get all results for a session"""
    try:
        # Get all products from database
        products = get_session_products(session_id)
        stats = get_session_stats(session_id)
        
        return jsonify({
            "products": products,
            "stats": stats
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/cleanup-session', methods=['POST'])
def cleanup_session_endpoint():
    """Clean up a specific session"""
    try:
        data = request.json
        session_id = data.get('sessionId')
        
        if not session_id:
            return jsonify({"error": "Session ID is required"}), 400
            
        cleanup_session(session_id)
        return jsonify({"success": True})
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/session-progress/<session_id>')
def get_session_progress(session_id):
    """Get progress for a session"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                'SELECT total_terms, processed_terms, total_products FROM sessions WHERE session_id = ?',
                (session_id,)
            )
            result = cursor.fetchone()
            
            if result:
                return jsonify({
                    "total_terms": result[0] or 0,
                    "processed_terms": result[1] or 0,
                    "total_products": result[2] or 0,
                    "progress_percentage": (result[1] / result[0] * 100) if result[0] > 0 else 0
                })
            else:
                return jsonify({"error": "Session not found"}), 404
                
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# Initialize database on startup
init_database()

# Register cleanup function to run on exit
atexit.register(cleanup_old_sessions)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
