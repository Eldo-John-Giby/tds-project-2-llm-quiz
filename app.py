import os
import json
import base64
import requests
from flask import Flask, request, jsonify
import time
from threading import Thread
import re
from groq import Groq
import sys
import logging
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import csv
from io import StringIO
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

EMAIL = "24f2005903@ds.study.iitm.ac.in"
SECRET = "my_secret_key_12345"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

try:
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info("✓ Groq client initialized")
except Exception as e:
    logger.error(f"✗ Groq init failed: {e}")
    groq_client = None

def fetch_page_content(url):
    """Fetch page content"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return {"content": response.text, "success": True, "url": response.url}
    except Exception as e:
        logger.error(f"Fetch failed {url}: {e}")
        return {"content": "", "success": False, "error": str(e)}

def compute_email_number(email):
    """Compute the emailNumber from an email address"""
    try:
        digits = ''.join(c for c in email if c.isdigit())
        if digits:
            number = int(digits)
            logger.info(f"📧 Email number: {number}")
            return number
        
        ascii_sum = sum(ord(c) for c in email)
        logger.info(f"📧 Email number (ASCII sum): {ascii_sum}")
        return ascii_sum
        
    except Exception as e:
        logger.error(f"Failed to compute email number: {e}")
        return None

def compute_secret_from_email(email):
    """Compute SHA-1 hash secret from email"""
    try:
        hash_obj = hashlib.sha1(email.encode('utf-8'))
        full_hash = hash_obj.hexdigest()
        secret = full_hash[:10]
        logger.info(f"🔐 Computed SHA-1 secret: {secret}")
        return secret
    except Exception as e:
        logger.error(f"Failed to compute secret: {e}")
        return None

def fetch_and_parse_js_secret(page_url, page_content, email=None):
    """Analyze JavaScript to compute secret"""
    try:
        page_lower = page_content.lower()
        uses_email_number = 'emailnumber' in page_lower
        uses_sha1 = 'sha1' in page_lower
        
        if (uses_email_number or uses_sha1) and email:
            secret = compute_secret_from_email(email)
            if secret:
                logger.info(f"✅ Using computed SHA-1 secret: {secret}")
                return secret
        
        return None
        
    except Exception as e:
        logger.error(f"JS parsing error: {e}")
        return None

def download_file(url, base_url=None):
    """Download a file"""
    try:
        if base_url and not url.startswith(('http://', 'https://')):
            url = urljoin(base_url, url)
        
        logger.info(f"Downloading: {url}")
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '').lower()
        
        if any(t in content_type for t in ['text', 'csv', 'json', 'html', 'xml', 'sql']):
            return {"success": True, "content": response.text, "type": "text", "url": url, "content_type": content_type}
        elif 'pdf' in content_type:
            # For PDF, try to extract text using simple method
            try:
                import PyPDF2
                from io import BytesIO
                pdf_reader = PyPDF2.PdfReader(BytesIO(response.content))
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text()
                logger.info(f"📄 Extracted {len(text)} chars from PDF")
                return {"success": True, "content": text, "type": "text", "url": url, "content_type": "text/plain"}
            except:
                # Fallback: return base64
                logger.warning("⚠️ Could not extract PDF text, returning base64")
                return {"success": True, "content": base64.b64encode(response.content).decode(), "type": "binary", "url": url, "content_type": content_type}
        else:
            return {"success": True, "content": base64.b64encode(response.content).decode(), "type": "binary", "url": url, "content_type": content_type}
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return {"success": False, "error": str(e), "url": url}

def parse_csv_content(content, cutoff=None):
    """Parse CSV - return BOTH total sum AND filtered sum"""
    try:
        for delimiter in [',', ';', '\t', '|']:
            try:
                csv_reader = csv.reader(StringIO(content), delimiter=delimiter)
                rows = list(csv_reader)
                if len(rows) > 0 and len(rows[0]) > 1:
                    break
            except:
                continue
        
        all_numbers = []
        for row in rows:
            for cell in row:
                cell = cell.strip()
                try:
                    num = float(cell.replace(',', '').replace(' ', ''))
                    all_numbers.append(num)
                except:
                    pass
        
        if all_numbers:
            total_sum = sum(all_numbers)
            result = {
                "all_numbers": all_numbers,
                "total_count": len(all_numbers),
                "sum_all": int(total_sum) if total_sum == int(total_sum) else total_sum,
            }
            
            if cutoff is not None:
                numbers_gt = [n for n in all_numbers if n > cutoff]
                sum_gt = sum(numbers_gt)
                
                result.update({
                    "cutoff": cutoff,
                    "count_gt": len(numbers_gt),
                    "sum_gt": int(sum_gt) if sum_gt == int(sum_gt) else sum_gt,
                })
                
                logger.info(f"📊 CSV: Total={result['sum_all']}, >cutoff={result['sum_gt']}")
            
            return result
        return None
    except Exception as e:
        logger.error(f"CSV parse error: {e}")
        return None

def parse_sql_file(content):
    """Parse SQL file and extract data"""
    try:
        # Look for INSERT statements
        inserts = re.findall(r"INSERT INTO.*?VALUES\s*\((.*?)\)", content, re.IGNORECASE | re.DOTALL)
        
        data = []
        for insert in inserts:
            # Parse values
            values = re.findall(r"'([^']*)'|(\d+)", insert)
            row = [v[0] if v[0] else v[1] for v in values]
            data.append(row)
        
        logger.info(f"📊 SQL: Parsed {len(data)} rows")
        return {"rows": data, "count": len(data)}
    except Exception as e:
        logger.error(f"SQL parse error: {e}")
        return None

def extract_values_from_html(content):
    """Extract values from HTML"""
    try:
        soup = BeautifulSoup(content, 'html.parser')
        
        for tag in soup(["script", "style"]):
            tag.decompose()
        
        values = {}
        
        for tag in soup.find_all(id=True):
            tag_id = tag.get('id')
            text = tag.get_text().strip()
            if text and len(text) < 500:
                values[f"#{tag_id}"] = text
        
        text = soup.get_text()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        clean_text = '\n'.join(lines)
        
        return {
            "values": values,
            "text": clean_text,
            "has_data": len(values) > 0
        }
    except Exception as e:
        logger.error(f"HTML parse error: {e}")
        return {"values": {}, "text": content[:2000], "has_data": False}

def decode_base64_in_page(html_content):
    """Extract and decode base64 from page"""
    patterns = [
        r'atob\([`\'"]([^`\'"]+)[`\'"]',
        r'const code = `([A-Za-z0-9+/=]+)`',
        r'code = ["\']([A-Za-z0-9+/=]+)["\']',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html_content)
        if match:
            try:
                decoded = base64.b64decode(match.group(1)).decode('utf-8')
                logger.info(f"✓ Decoded base64: {decoded[:300]}...")
                return decoded
            except:
                pass
    
    return html_content

def extract_origin_from_page(html_content, page_url):
    """Extract the origin (base URL) from page"""
    parsed = urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin

def solve_with_groq(page_content, quiz_url, downloaded_files=None, previous_attempts=None, email_number=None):
    """Use Groq AI to solve the quiz"""
    if not groq_client:
        logger.error("Groq client not available!")
        return None
    
    files_context = ""
    if downloaded_files:
        files_context = "\n\n" + "="*60 + "\nDOWNLOADED FILES:\n" + "="*60 + "\n"
        
        for url, data in downloaded_files.items():
            if not data.get("success"):
                continue
            
            if data.get("type") == "text":
                content = data.get("content", "")
                
                # CSV files
                if 'csv' in url.lower() or 'csv' in data.get('content_type', '').lower():
                    csv_data = parse_csv_content(content, cutoff=email_number)
                    if csv_data:
                        files_context += f"\n📊 CSV: {url}\n"
                        files_context += f"   ✅ TOTAL SUM (all numbers): {csv_data['sum_all']}\n"
                        if csv_data.get('cutoff'):
                            files_context += f"   Sum > cutoff ({csv_data['cutoff']}): {csv_data['sum_gt']}\n"
                    else:
                        files_context += f"\n📄 {url}\n{content[:1000]}\n"
                
                # SQL files
                elif 'sql' in url.lower() or 'sql' in data.get('content_type', '').lower():
                    sql_data = parse_sql_file(content)
                    files_context += f"\n📊 SQL: {url}\n"
                    if sql_data:
                        files_context += f"   Rows: {sql_data['count']}\n"
                        files_context += f"   Data preview: {sql_data['rows'][:5]}\n"
                    files_context += f"\n   Full content:\n{content[:2000]}\n"
                
                # JSON files
                elif 'json' in url.lower() or 'json' in data.get('content_type', '').lower():
                    try:
                        json_data = json.loads(content)
                        files_context += f"\n📊 JSON: {url}\n"
                        files_context += f"   {json.dumps(json_data, indent=2)[:3000]}\n"
                    except:
                        files_context += f"\n📄 {url}\n{content[:2000]}\n"
                
                # HTML/Text
                else:
                    if 'SECRET FOUND:' in content:
                        secret_match = re.search(r'SECRET FOUND:\s*([^\s\n]+)', content)
                        if secret_match:
                            files_context += f"\n🔐 {url}\n   SECRET: {secret_match.group(1)}\n"
                    files_context += f"\n📄 {url}\n{content[:1500]}\n"
    
    context = ""
    if previous_attempts:
        last = previous_attempts[-1]
        context = f"\n⚠️ PREVIOUS WRONG:\n"
        context += f"   Answer: {last.get('answer')}\n"
        context += f"   Reason: {last.get('reason')}\n"
    
    prompt = f"""Solve this quiz question precisely.

URL: {quiz_url}

PAGE:
{page_content[:8000]}

{files_context}

{context}

INSTRUCTIONS:
1. Read the question carefully
2. For CSV: use TOTAL SUM (all numbers) unless specifically asked for filtered sum
3. For SQL: count/analyze the data rows shown
4. For JSON: extract exact values requested
5. For arrays: return as JSON array like ["item1", "item2"]
6. For PDF: extract numbers/text from the content shown

Return ONLY valid JSON:
{{
  "task": "brief description",
  "submit_url": "/submit",
  "file_urls": [],
  "scrape_urls": [],
  "answer": <exact value - number, string, array, etc>,
  "reasoning": "explanation"
}}"""

    try:
        response = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Extract exact values. Return valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=4096
        )
        
        text = response.choices[0].message.content.strip()
        
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        
        result = json.loads(text)
        return result
    except Exception as e:
        logger.error(f"Groq error: {e}")
        if 'text' in locals():
            logger.error(f"Response: {text}")
        return None

def process_quiz(start_url):
    """Process the quiz chain"""
    logger.info(f"\n{'#'*70}")
    logger.info(f"🚀 STARTING QUIZ: {start_url}")
    logger.info(f"{'#'*70}\n")
    
    current_url = start_url
    results = []
    start_time = time.time()
    MAX_TIME = 170
    
    for q in range(25):
        elapsed = time.time() - start_time
        if elapsed > MAX_TIME:
            logger.warning(f"⏱️ Time limit ({elapsed:.0f}s)")
            break
        
        logger.info(f"\n{'='*70}")
        logger.info(f"❓ Question {q+1}: {current_url}")
        logger.info(f"{'='*70}")
        
        page = fetch_page_content(current_url)
        if not page['success']:
            logger.error(f"❌ Failed to fetch")
            break
        
        content = decode_base64_in_page(page['content'])
        origin = extract_origin_from_page(page['content'], page['url'])
        logger.info(f"📄 Preview: {content[:200]}...")
        
        email_number = None
        user_email = None
        if '?email=' in current_url:
            email_match = re.search(r'email=([^&]+)', current_url)
            if email_match:
                user_email = email_match.group(1).replace('%40', '@')
                email_number = compute_email_number(user_email)
        
        solution = solve_with_groq(content, page['url'], None, results, email_number)
        if not solution:
            logger.error("❌ No solution")
            break
        
        logger.info(f"📋 Task: {solution.get('task')}")
        
        submit_url = f"{origin}/submit"
        
        downloaded = {}
        
        for file_url in solution.get("file_urls", []):
            if file_url:
                data = download_file(file_url, page['url'])
                if data.get("success"):
                    downloaded[file_url] = data
                    logger.info(f"✓ Downloaded: {file_url}")
        
        for scrape_url in solution.get("scrape_urls", []):
            if scrape_url:
                if not scrape_url.startswith("http"):
                    scrape_url = urljoin(page['url'], scrape_url)
                
                logger.info(f"🔍 Scraping: {scrape_url}")
                scraped = fetch_page_content(scrape_url)
                if scraped["success"]:
                    scraped_content = decode_base64_in_page(scraped["content"])
                    
                    js_secret = fetch_and_parse_js_secret(scrape_url, scraped["content"], user_email)
                    
                    if js_secret:
                        scraped_content = f"SECRET FOUND: {js_secret}\n\n" + scraped_content
                    
                    downloaded[scrape_url] = {
                        "success": True,
                        "content": scraped_content,
                        "type": "text",
                        "url": scrape_url,
                        "content_type": "text/html"
                    }
                    logger.info(f"✓ Scraped")
        
        if downloaded:
            logger.info(f"🔄 Re-analyzing with {len(downloaded)} files...")
            solution = solve_with_groq(content, page["url"], downloaded, results, email_number)
            if not solution:
                logger.error("❌ Failed re-analysis")
                break
        
        answer = solution.get("answer")
        logger.info(f"💡 Answer: {answer} (type: {type(answer).__name__})")
        logger.info(f"🧠 Reasoning: {solution.get('reasoning')}")
        
        payload = {
            "email": EMAIL,
            "secret": SECRET,
            "url": current_url,
            "answer": answer
        }
        
        logger.info(f"📤 Submitting...")
        
        try:
            resp = requests.post(submit_url, json=payload, timeout=30)
            
            if not resp.text.strip():
                logger.warning(f"⚠️ Empty response")
                break
            
            try:
                data = resp.json()
            except json.JSONDecodeError as e:
                logger.error(f"❌ Invalid JSON: {e}")
                break
            
            correct = data.get("correct", False)
            reason = data.get("reason", "")
            
            results.append({
                "question": q+1,
                "url": current_url,
                "answer": answer,
                "correct": correct,
                "reason": reason
            })
            
            if correct:
                logger.info(f"✅ CORRECT!")
                next_url = data.get("url")
                if next_url:
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.info(f"\n🎉 COMPLETED! 🎉")
                    break
            else:
                logger.warning(f"❌ WRONG: {reason}")
                next_url = data.get("url")
                if next_url:
                    logger.info(f"➡️ Next...")
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.warning(f"🛑 Ended")
                    break
        
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            break
    
    correct_count = sum(1 for r in results if r.get("correct"))
    total = len(results)
    pct = (correct_count / total * 100) if total > 0 else 0
    
    logger.info(f"\n{'='*70}")
    logger.info(f"📊 FINAL: {correct_count}/{total} ({pct:.1f}%)")
    logger.info(f"{'='*70}\n")
    
    return results

@app.route('/', methods=['POST'])
def quiz_endpoint():
    try:
        data = request.get_json(force=True)
    except:
        return jsonify({"error": "Invalid JSON"}), 400
    
    if data.get("secret") != SECRET:
        return jsonify({"error": "Invalid secret"}), 403
    
    if data.get("email") != EMAIL:
        return jsonify({"error": "Invalid email"}), 403
    
    url = data.get("url")
    if not url:
        return jsonify({"error": "No URL"}), 400
    
    logger.info(f"✓ Quiz request: {url}")
    
    Thread(target=lambda: process_quiz(url), daemon=True).start()
    
    return jsonify({"status": "accepted"}), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

@app.route('/test', methods=['GET'])
def test():
    return jsonify({
        "email": EMAIL,
        "groq_ready": groq_client is not None
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
