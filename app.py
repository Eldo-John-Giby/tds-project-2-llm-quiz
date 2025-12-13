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

def fetch_and_parse_js_secret(page_url, page_content):
    """Try to extract secret by fetching and parsing JavaScript files"""
    try:
        soup = BeautifulSoup(page_content, 'html.parser')
        scripts = soup.find_all('script', src=True)
        
        all_secrets = []
        
        for script in scripts:
            script_url = script.get('src')
            if script_url and not script_url.startswith('http'):
                script_url = urljoin(page_url, script_url)
            
            logger.info(f"📜 Fetching JS file: {script_url}")
            
            try:
                response = requests.get(script_url, timeout=10)
                js_content = response.text
                
                logger.info(f"📄 JS content preview: {js_content[:500]}")
                
                import_matches = re.findall(r'import.*?from\s+["\']([^"\']+)["\']', js_content)
                for import_file in import_matches:
                    if not import_file.startswith('http'):
                        import_url = urljoin(script_url, import_file)
                    else:
                        import_url = import_file
                    
                    logger.info(f"📜 Following import: {import_url}")
                    try:
                        imp_response = requests.get(import_url, timeout=10)
                        imp_content = imp_response.text
                        logger.info(f"📄 Imported JS preview: {imp_content[:500]}")
                        
                        secrets = parse_js_for_secrets(imp_content)
                        all_secrets.extend(secrets)
                    except Exception as e:
                        logger.error(f"Failed to fetch import {import_url}: {e}")
                
                secrets = parse_js_for_secrets(js_content)
                all_secrets.extend(secrets)
                
            except Exception as e:
                logger.error(f"Failed to fetch/parse JS: {e}")
                continue
        
        if all_secrets:
            secret = all_secrets[0]
            logger.info(f"✓ Found secret: {secret}")
            return secret
        
        logger.warning(f"⚠️ No secret found in any JS files")
        return None
        
    except Exception as e:
        logger.error(f"JS parsing error: {e}")
        return None

def compute_email_number(email):
    """Compute the emailNumber from an email address - converts email to a number"""
    try:
        digits = ''.join(c for c in email if c.isdigit())
        if digits:
            number = int(digits)
            logger.info(f"📧 Email number (digits): {number}")
            return number
        
        ascii_sum = sum(ord(c) for c in email)
        logger.info(f"📧 Email number (ASCII sum): {ascii_sum}")
        return ascii_sum
        
    except Exception as e:
        logger.error(f"Failed to compute email number: {e}")
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
        
        if any(t in content_type for t in ['text', 'csv', 'json', 'html', 'xml']):
            return {"success": True, "content": response.text, "type": "text", "url": url, "content_type": content_type}
        else:
            return {"success": True, "content": base64.b64encode(response.content).decode(), "type": "binary", "url": url, "content_type": content_type}
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return {"success": False, "error": str(e), "url": url}

def parse_csv_content(content, cutoff=None):
    """Parse CSV and extract all numeric data with statistics"""
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
            if cutoff is not None:
                filtered_numbers = [n for n in all_numbers if n > cutoff]
                logger.info(f"📊 Filtering numbers > {cutoff}: {len(all_numbers)} → {len(filtered_numbers)}")
                all_numbers = filtered_numbers
            
            total_sum = sum(all_numbers)
            return {
                "numbers": all_numbers,
                "count": len(all_numbers),
                "sum": int(total_sum) if total_sum == int(total_sum) else total_sum,
                "min": min(all_numbers) if all_numbers else 0,
                "max": max(all_numbers) if all_numbers else 0,
                "avg": sum(all_numbers) / len(all_numbers) if all_numbers else 0,
                "first_10": all_numbers[:10],
                "last_10": all_numbers[-10:],
                "cutoff": cutoff
            }
        return None
    except Exception as e:
        logger.error(f"CSV parse error: {e}")
        return None

def parse_js_for_secrets(js_content):
    """Parse JS content for potential secrets"""
    secrets = []
    
    patterns = [
        r'textContent\s*=\s*["\']([^"\']+)["\']',
        r'innerHTML\s*=\s*["\']([^"\']+)["\']',
        r'innerText\s*=\s*["\']([^"\']+)["\']',
        r'return\s+["\']([A-Za-z0-9_-]{5,})["\']',
        r':\s*["\']([A-Za-z0-9_-]{5,})["\']',
        r'=\s*["\']([A-Za-z0-9_-]{5,})["\']',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, js_content, re.IGNORECASE)
        for match in matches:
            if len(match) >= 5 and not any(kw in match.lower() for kw in ['question', 'email', 'submit', 'http', 'const', 'function', 'document', 'element', 'window', 'import', 'export', 'please', 'provide', 'secret', 'strong', 'utils']):
                secrets.append(match)
    
    return secrets

def extract_values_from_html(content):
    """Extract important values from HTML"""
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
        
        for tag_name in ['span', 'div', 'p', 'code', 'pre', 'strong', 'em']:
            for tag in soup.find_all(tag_name):
                text = tag.get_text().strip()
                if text and len(text) < 200:
                    if text.replace('-', '').replace('_', '').isalnum() and len(text) > 3:
                        class_name = ' '.join(tag.get('class', []))
                        key = f"{tag_name}.{class_name}" if class_name else tag_name
                        if key not in values:
                            values[key] = text
        
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
    logger.info(f"Extracted origin: {origin}")
    return origin

def solve_with_groq(page_content, quiz_url, downloaded_files=None, previous_attempts=None, email_number=None):
    """Use Groq AI to solve the quiz - FIXED: added email_number parameter"""
    if not groq_client:
        logger.error("Groq client not available!")
        return None
    
    files_context = ""
    if downloaded_files:
        files_context = "\n\n" + "="*60 + "\nDOWNLOADED/SCRAPED DATA:\n" + "="*60 + "\n"
        
        for url, data in downloaded_files.items():
            if not data.get("success"):
                continue
            
            if data.get("type") == "text":
                content = data.get("content", "")
                
                if 'csv' in url.lower() or 'csv' in data.get('content_type', '').lower():
                    # NOW email_number is available as parameter
                    csv_data = parse_csv_content(content, cutoff=email_number)
                    if csv_data:
                        files_context += f"\n📊 CSV FILE: {url}\n"
                        files_context += f"   Total numbers found: {csv_data['count']}\n"
                        if csv_data.get('cutoff'):
                            files_context += f"   ✓ CUTOFF: {csv_data['cutoff']} (only counting numbers above this)\n"
                        files_context += f"   ✓ SUM OF ALL NUMBERS: {csv_data['sum']}\n"
                        files_context += f"   MIN: {csv_data['min']}\n"
                        files_context += f"   MAX: {csv_data['max']}\n"
                        files_context += f"   AVERAGE: {csv_data['avg']:.2f}\n"
                        files_context += f"   First 10 numbers: {csv_data['first_10']}\n"
                        files_context += f"   Last 10 numbers: {csv_data['last_10']}\n"
                    else:
                        files_context += f"\n📄 FILE: {url}\n{content[:1500]}\n"
                
                elif 'html' in data.get('content_type', '').lower() or '<html' in content.lower():
                    html_data = extract_values_from_html(content)
                    files_context += f"\n🌐 HTML PAGE: {url}\n"
                    
                    if html_data['has_data'] and html_data['values']:
                        files_context += "   ✓ EXTRACTED VALUES (potential secrets/codes):\n"
                        for key, value in list(html_data['values'].items())[:20]:
                            files_context += f"      {key}: '{value}'\n"
                    
                    files_context += f"\n   TEXT CONTENT:\n{html_data['text'][:1500]}\n"
                
                else:
                    files_context += f"\n📄 FILE: {url}\n{content[:2000]}\n"
    
    context = ""
    if previous_attempts:
        last = previous_attempts[-1]
        context = f"\n⚠️ PREVIOUS WRONG ATTEMPT:\n"
        context += f"   Your answer: {last.get('answer')}\n"
        context += f"   Why wrong: {last.get('reason')}\n"
        context += f"   ➜ LEARN FROM THIS! Check the data above carefully.\n"
    
    prompt = f"""You are solving a data analysis quiz. Be EXTREMELY PRECISE.

QUIZ URL: {quiz_url}

PAGE CONTENT:
{page_content[:10000]}

{files_context}

{context}

🎯 YOUR TASK:
1. If page has base64 (atob OR const code = `...`), DECODE IT FIRST to see real question
2. Read the exact question carefully
3. Identify what files/URLs to download/scrape
4. Use the PRE-CALCULATED data above (don't recalculate!)
5. Find the exact answer

📊 FOR CSV SUM QUESTIONS:
- I've ALREADY calculated the sum for you above
- Look for "✓ SUM OF ALL NUMBERS: X"
- Use that EXACT number as your answer (as INTEGER, not float)
- DO NOT try to recalculate!

🔍 FOR SECRET/CODE QUESTIONS:
- Look at "✓ EXTRACTED VALUES" section above in the scraped data
- The secret will be in the SCRAPED page content, not the main page
- Find the actual secret/code value there
- It will be in format like "#secret: 'ABC123'" or "span: 'XYZ789'"
- The answer is the VALUE after the colon (e.g., 'ABC123')
- NEVER EVER use placeholder text like "your secret" - that's ALWAYS wrong!
- If you don't see the scraped data, add the URL to scrape_urls

🔗 FOR SUBMIT URL:
- If you see "<span class=\"origin\"></span>/submit", just return "/submit" as the submit_url
- If you see a path like "/q1/submit" or "/demo/submit", return JUST THE PATH starting with /
- I will add the correct domain automatically

📝 RESPOND WITH ONLY THIS JSON:
{{
  "task": "exact question from decoded content",
  "submit_url": "/submit",
  "file_urls": ["file1.csv"],
  "scrape_urls": ["/path/to/scrape"],
  "answer": <use the pre-calculated values from above>,
  "reasoning": "I used the SUM/value shown above: X"
}}

⚡ CRITICAL RULES:
- SUM questions: Use my pre-calculated "SUM OF ALL NUMBERS" value AS INTEGER
- SECRET questions: Use ACTUAL value from "EXTRACTED VALUES", NEVER placeholders
- Answer TYPE: sum=INTEGER (not float), secret=STRING, yes/no=BOOLEAN
- submit_url: MUST start with / (like "/submit" or "/q1/submit"), NOT full URL
- If secret not found yet, add URL to scrape_urls to get it
- If you see "✓ SUM OF ALL NUMBERS: 4803134.0", answer is 4803134 (integer)
- If you see "#secret: 'mycode123'", answer is "mycode123" (string)
- NEVER return placeholder values like "your secret", "your email", etc."""

    try:
        response = groq_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise data analyst. Use the pre-calculated values provided. Be extremely accurate. Respond with valid JSON only."
                },
                {
                    "role": "user",
                    "content": prompt
                }
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
    
    for q in range(20):
        elapsed = time.time() - start_time
        if elapsed > MAX_TIME:
            logger.warning(f"⏱️ Time limit approaching ({elapsed:.0f}s), stopping")
            break
        
        logger.info(f"\n{'='*70}")
        logger.info(f"❓ Question {q+1}: {current_url}")
        logger.info(f"{'='*70}")
        
        page = fetch_page_content(current_url)
        if not page['success']:
            logger.error(f"❌ Failed to fetch page")
            break
        
        content = decode_base64_in_page(page['content'])
        origin = extract_origin_from_page(page['content'], page['url'])
        logger.info(f"🌐 Origin: {origin}")
        logger.info(f"📄 Page preview: {content[:250]}...")
        
        # FIXED: Compute email_number BEFORE calling solve_with_groq
        email_number = None
        if '?email=' in current_url:
            email_match = re.search(r'email=([^&]+)', current_url)
            if email_match:
                user_email = email_match.group(1).replace('%40', '@')
                email_number = compute_email_number(user_email)
                logger.info(f"📧 Computed email number from {user_email}: {email_number}")
        
        # FIXED: Pass email_number to solve_with_groq
        solution = solve_with_groq(content, page['url'], None, results, email_number)
        if not solution:
            logger.error("❌ Failed to get solution")
            break
        
        logger.info(f"📋 Task identified: {solution.get('task')}")
        
        submit_url_raw = solution.get("submit_url", "")
        logger.info(f"🔗 Raw submit URL from AI: {submit_url_raw}")
        
        submit_url = f"{origin}/submit"
        logger.info(f"✓ Using submit URL: {submit_url}")
        
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
                    
                    js_secret = fetch_and_parse_js_secret(scrape_url, scraped["content"])
                    
                    if not js_secret and email_number:
                        js_secret = str(email_number)
                        logger.info(f"✓ Using email_number as secret: {js_secret}")
                    
                    if js_secret:
                        scraped_content = f"SECRET FOUND: {js_secret}\n\n" + scraped_content
                    
                    downloaded[scrape_url] = {
                        "success": True,
                        "content": scraped_content,
                        "type": "text",
                        "url": scrape_url,
                        "content_type": "text/html"
                    }
                    logger.info(f"✓ Scraped: {scrape_url}")
                    logger.info(f"   Preview: {scraped_content[:500]}...")
        
        if downloaded:
            logger.info(f"🔄 Re-analyzing with {len(downloaded)} resources...")
            # FIXED: Pass email_number here too
            solution = solve_with_groq(content, page["url"], downloaded, results, email_number)
            if not solution:
                logger.error("❌ Failed with downloaded data")
                break
        
        answer = solution.get("answer")
        logger.info(f"💡 Answer: {answer} (type: {type(answer).__name__})")
        logger.info(f"🧠 Reasoning: {solution.get('reasoning')}")
        
        if not submit_url or not submit_url.startswith("http"):
            logger.error(f"❌ Invalid submit URL: {submit_url}")
            break
        
        payload = {
            "email": EMAIL,
            "secret": SECRET,
            "url": current_url,
            "answer": answer
        }
        
        logger.info(f"📤 Submitting to: {submit_url}")
        
        try:
            resp = requests.post(submit_url, json=payload, timeout=30)
            
            if not resp.text.strip():
                logger.warning(f"⚠️ Empty response from server (status: {resp.status_code})")
                logger.warning(f"🛑 Cannot proceed without response data")
                break
            
            logger.info(f"📥 Raw response ({resp.status_code}): {resp.text[:500]}")
            
            try:
                data = resp.json()
            except json.JSONDecodeError as e:
                logger.error(f"❌ Invalid JSON response: {e}")
                logger.error(f"Response text: {resp.text[:1000]}")
                break
            
            logger.info(f"📥 Parsed response: {data}")
            
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
                logger.info(f"✅ CORRECT! Moving forward...")
                next_url = data.get("url")
                if next_url:
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.info(f"\n🎉🎉🎉 QUIZ COMPLETED! 🎉🎉🎉")
                    break
            else:
                logger.warning(f"❌ WRONG: {reason}")
                next_url = data.get("url")
                if next_url:
                    logger.info(f"➡️ Moving to next question anyway...")
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.warning(f"🛑 No next URL, quiz ended")
                    break
        
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Network error during submission: {e}")
            break
        except Exception as e:
            logger.error(f"❌ Submit error: {e}")
            import traceback
            traceback.print_exc()
            break
    
    correct_count = sum(1 for r in results if r.get("correct"))
    total = len(results)
    percentage = (correct_count / total * 100) if total > 0 else 0
    
    logger.info(f"\n{'='*70}")
    logger.info(f"📊 FINAL SCORE: {correct_count}/{total} correct ({percentage:.1f}%)")
    logger.info(f"{'='*70}\n")
    
    return results

@app.route('/', methods=['POST'])
def quiz_endpoint():
    """Main quiz endpoint"""
    try:
        data = request.get_json(force=True)
    except:
        return jsonify({"error": "Invalid JSON"}), 400
    
    if data.get("secret") != SECRET:
        logger.warning(f"⚠️ Invalid secret attempt")
        return jsonify({"error": "Invalid secret"}), 403
    
    if data.get("email") != EMAIL:
        logger.warning(f"⚠️ Invalid email")
        return jsonify({"error": "Invalid email"}), 403
    
    url = data.get("url")
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    
    logger.info(f"✓ Received quiz request: {url}")
    
    Thread(target=lambda: process_quiz(url), daemon=True).start()
    
    return jsonify({"status": "accepted", "message": "Processing quiz"}), 200

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({"status": "ok", "service": "TDS Quiz Solver"}), 200

@app.route('/test', methods=['GET'])
def test():
    """Test configuration"""
    return jsonify({
        "email": EMAIL,
        "secret_set": bool(SECRET),
        "api_key_set": bool(GROQ_API_KEY),
        "groq_ready": groq_client is not None,
        "status": "ready" if groq_client else "api_key_missing"
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Starting TDS Quiz Solver on port {port}")
    logger.info(f"📧 Email: {EMAIL}")
    logger.info(f"🔑 Secret: {'*' * len(SECRET)}")
    logger.info(f"🤖 Groq API: {'✓ Configured' if GROQ_API_KEY else '✗ MISSING'}")
    logger.info(f"{'='*70}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
