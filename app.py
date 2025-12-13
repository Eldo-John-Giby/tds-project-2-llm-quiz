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

def parse_csv_content(content):
    """Parse CSV and extract all numeric data with statistics"""
    try:
        # Try different delimiters
        for delimiter in [',', ';', '\t', '|']:
            try:
                csv_reader = csv.reader(StringIO(content), delimiter=delimiter)
                rows = list(csv_reader)
                if len(rows) > 0 and len(rows[0]) > 0:
                    break
            except:
                continue
        
        # Extract all numbers
        all_numbers = []
        for row in rows:
            for cell in row:
                cell = cell.strip()
                # Try to convert to number
                try:
                    num = float(cell.replace(',', ''))
                    all_numbers.append(num)
                except:
                    pass
        
        if all_numbers:
            return {
                "numbers": all_numbers,
                "count": len(all_numbers),
                "sum": sum(all_numbers),
                "min": min(all_numbers),
                "max": max(all_numbers),
                "avg": sum(all_numbers) / len(all_numbers),
                "first_10": all_numbers[:10],
                "last_10": all_numbers[-10:]
            }
        return None
    except Exception as e:
        logger.error(f"CSV parse error: {e}")
        return None

def extract_values_from_html(content):
    """Extract important values from HTML"""
    try:
        soup = BeautifulSoup(content, 'html.parser')
        
        # Remove script and style
        for tag in soup(["script", "style"]):
            tag.decompose()
        
        values = {}
        
        # Extract from tags with IDs
        for tag in soup.find_all(id=True):
            tag_id = tag.get('id')
            text = tag.get_text().strip()
            if text and len(text) < 500:
                values[f"#{tag_id}"] = text
        
        # Extract from common semantic tags
        for tag_name in ['span', 'div', 'p', 'code', 'pre', 'strong', 'em']:
            for tag in soup.find_all(tag_name):
                text = tag.get_text().strip()
                # Look for potential codes/secrets (alphanumeric strings)
                if text and len(text) < 200:
                    # If it looks like a code (no spaces, mix of letters/numbers)
                    if text.replace('-', '').replace('_', '').isalnum() and len(text) > 3:
                        class_name = ' '.join(tag.get('class', []))
                        key = f"{tag_name}.{class_name}" if class_name else tag_name
                        if key not in values:
                            values[key] = text
        
        # Get clean text
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
    match = re.search(r'atob\([`\'"]([^`\'"]+)[`\'"]', html_content)
    if match:
        try:
            decoded = base64.b64decode(match.group(1)).decode('utf-8')
            logger.info(f"Decoded base64: {decoded[:200]}...")
            return decoded
        except:
            pass
    return html_content

def solve_with_groq(page_content, quiz_url, downloaded_files=None, previous_attempts=None):
    """Use Groq AI to solve the quiz"""
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
                
                # Check if it's CSV
                if 'csv' in url.lower() or 'csv' in data.get('content_type', '').lower():
                    csv_data = parse_csv_content(content)
                    if csv_data:
                        files_context += f"\n📊 CSV FILE: {url}\n"
                        files_context += f"   Total numbers found: {csv_data['count']}\n"
                        files_context += f"   ✓ SUM OF ALL NUMBERS: {csv_data['sum']}\n"
                        files_context += f"   MIN: {csv_data['min']}\n"
                        files_context += f"   MAX: {csv_data['max']}\n"
                        files_context += f"   AVERAGE: {csv_data['avg']:.2f}\n"
                        files_context += f"   First 10 numbers: {csv_data['first_10']}\n"
                        files_context += f"   Last 10 numbers: {csv_data['last_10']}\n"
                    else:
                        files_context += f"\n📄 FILE: {url}\n{content[:1500]}\n"
                
                # Check if it's HTML
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
1. If page has base64 (atob), DECODE IT FIRST to see real question
2. Read the exact question carefully
3. Identify what files/URLs to download/scrape
4. Use the PRE-CALCULATED data above (don't recalculate!)
5. Find the exact answer

📊 FOR CSV SUM QUESTIONS:
- I've ALREADY calculated the sum for you above
- Look for "✓ SUM OF ALL NUMBERS: X"
- Use that EXACT number as your answer
- DO NOT try to recalculate!

🔍 FOR SECRET/CODE QUESTIONS:
- Look at "✓ EXTRACTED VALUES" section above
- Find the actual secret/code value there
- It will be in format like "#secret: 'ABC123'" or "span: 'XYZ789'"
- The answer is the VALUE after the colon (e.g., 'ABC123')
- NEVER use placeholder text like "your secret"

📝 RESPOND WITH ONLY THIS JSON:
{{
  "task": "exact question from decoded content",
  "submit_url": "full URL to POST answer to",
  "file_urls": ["file1.csv"],
  "scrape_urls": ["/path/to/scrape"],
  "answer": <use the pre-calculated values from above>,
  "reasoning": "I used the SUM/value shown above: X"
}}

⚡ CRITICAL RULES:
- SUM questions: Use my pre-calculated "SUM OF ALL NUMBERS" value
- SECRET questions: Use value from "EXTRACTED VALUES" (look for #id or tag names)
- Answer TYPE: sum=INTEGER, secret=STRING, yes/no=BOOLEAN
- If you see "✓ SUM OF ALL NUMBERS: 4803134", answer is 4803134 (integer)
- If you see "#secret: 'mycode123'", answer is "mycode123" (string)"""

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
        
        # Clean markdown
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
        
        # Fetch page
        page = fetch_page_content(current_url)
        if not page['success']:
            logger.error(f"❌ Failed to fetch page")
            break
        
        # Decode base64
        content = decode_base64_in_page(page['content'])
        logger.info(f"📄 Page preview: {content[:250]}...")
        
        # Get initial solution
        solution = solve_with_groq(content, page['url'], None, results)
        if not solution:
            logger.error("❌ Failed to get solution")
            break
        
        logger.info(f"📋 Task identified: {solution.get('task')}")
        
        # Download files and scrape URLs
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
                
                scraped = fetch_page_content(scrape_url)
                if scraped["success"]:
                    scraped_content = decode_base64_in_page(scraped["content"])
                    downloaded[scrape_url] = {
                        "success": True,
                        "content": scraped_content,
                        "type": "text",
                        "url": scrape_url,
                        "content_type": "text/html"
                    }
                    logger.info(f"✓ Scraped: {scrape_url}")
        
        # Re-solve with downloaded data
        if downloaded:
            logger.info(f"🔄 Re-analyzing with {len(downloaded)} resources...")
            solution = solve_with_groq(content, page["url"], downloaded, results)
            if not solution:
                logger.error("❌ Failed with downloaded data")
                break
        
        answer = solution.get("answer")
        logger.info(f"💡 Answer: {answer} (type: {type(answer).__name__})")
        logger.info(f"🧠 Reasoning: {solution.get('reasoning')}")
        
        # Get submit URL
        submit_url = solution.get("submit_url")
        if not submit_url:
            logger.error("❌ No submit URL!")
            break
        
        if not submit_url.startswith("http"):
            submit_url = urljoin(page["url"], submit_url)
        
        # Submit answer
        payload = {
            "email": EMAIL,
            "secret": SECRET,
            "url": current_url,
            "answer": answer
        }
        
        logger.info(f"📤 Submitting to: {submit_url}")
        
        try:
            resp = requests.post(submit_url, json=payload, timeout=30)
            data = resp.json()
            logger.info(f"📥 Response: {data}")
            
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
    
    # Process in background
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
