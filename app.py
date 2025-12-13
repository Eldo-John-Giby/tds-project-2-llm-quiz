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
from urllib.parse import urljoin
from bs4 import BeautifulSoup

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
    logger.info("Groq client initialized successfully")
except Exception as e:
    logger.error(f"Groq init failed: {e}")
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
            return {"success": True, "content": response.text, "type": "text", "url": url}
        else:
            return {"success": True, "content": base64.b64encode(response.content).decode(), "type": "binary", "url": url}
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return {"success": False, "error": str(e), "url": url}

def decode_base64_in_page(html_content):
    """Extract and decode base64 from page"""
    match = re.search(r'atob\([`\'"]([^`\'"]+)[`\'"]', html_content)
    if match:
        try:
            decoded = base64.b64decode(match.group(1)).decode('utf-8')
            return decoded
        except:
            pass
    return html_content

def solve_with_groq(page_content, quiz_url, downloaded_files=None, previous_attempts=None):
    """Use Groq to solve the quiz"""
    if not groq_client:
        logger.error("Groq client not available!")
        return None
    
    files_context = ""
    if downloaded_files:
        files_context = "\n\nDOWNLOADED DATA:\n"
        for url, data in downloaded_files.items():
            if data.get("success") and data.get("type") == "text":
                content = data.get("content", "")
                if len(content) > 8000:
                    content = content[:8000] + "\n...(truncated)"
                files_context += f"\n=== FROM: {url} ===\n{content}\n================\n"
    
    context = ""
    if previous_attempts:
        context = f"\nPREVIOUS WRONG ATTEMPTS:\n{json.dumps(previous_attempts[-1:], indent=2)}\nLearn from mistakes!\n"
    
    prompt = f"""Solve this data analysis quiz.

QUIZ URL: {quiz_url}

PAGE CONTENT:
{page_content[:12000]}

{files_context}

{context}

INSTRUCTIONS:
1. Decode any base64 content (look for atob)
2. Read the EXACT question being asked
3. List files to download and URLs to scrape
4. Use downloaded/scraped data to calculate answer
5. For CSV SUM: add ALL numbers together (not max/min/first)
6. For scraping: find ACTUAL value in HTML tags (not placeholder text)

Respond with ONLY valid JSON:
{{"task": "exact question", "submit_url": "full URL", "file_urls": ["file1.csv"], "scrape_urls": ["/path/to/scrape"], "answer": <calculated answer>, "reasoning": "I decoded X, downloaded Y, calculated Z"}}

CRITICAL RULES:
- If question says "sum", add ALL numbers: 1+2+3 = 6
- If question says "get secret from page", find actual value like <span id="code">ABC123</span>, answer is "ABC123"
- Placeholder text like "your secret" is NOT the answer
- If decoded text mentions scraping "/demo-data", add "/demo-data" to scrape_urls
- Answer TYPE matters: sum=INTEGER, text=STRING, yes/no=BOOLEAN"""

    try:
        response = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a data analysis expert. Respond with valid JSON only, no markdown."},
                {"role": "user", "content": prompt}
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
        logger.error(f"Response text: {text if 'text' in locals() else 'N/A'}")
        return None

def process_quiz(start_url):
    """Process the quiz chain"""
    logger.info(f"{'#'*60}")
    logger.info(f"STARTING QUIZ: {start_url}")
    logger.info(f"{'#'*60}")
    
    current_url = start_url
    results = []
    start_time = time.time()
    MAX_TIME = 170
    
    for q in range(15):
        if time.time() - start_time > MAX_TIME:
            logger.warning("Time limit approaching, stopping")
            break
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Question {q+1}: {current_url}")
        logger.info(f"{'='*60}")
        
        # Fetch page
        page = fetch_page_content(current_url)
        if not page['success']:
            logger.error(f"Failed to fetch page")
            break
        
        # Decode base64
        content = decode_base64_in_page(page['content'])
        logger.info(f"Page preview: {content[:300]}...")
        
        # Get solution
        solution = solve_with_groq(content, page['url'], None, results)
        if not solution:
            logger.error("Failed to get solution from Groq")
            break
        
        logger.info(f"Task: {solution.get('task')}")
        
        # Download files
        downloaded = {}
        for file_url in solution.get("file_urls", []):
            if file_url:
                data = download_file(file_url, page['url'])
                if data.get("success"):
                    downloaded[file_url] = data
                    logger.info(f"✓ Downloaded: {file_url}")
        
        # Scrape URLs
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
                        "url": scrape_url
                    }
                    logger.info(f"✓ Scraped: {scrape_url}")
        
        # Re-solve with downloaded data
        if downloaded:
            logger.info(f"Re-solving with {len(downloaded)} resources")
            solution = solve_with_groq(content, page["url"], downloaded, results)
            if not solution:
                logger.error("Failed to solve with downloaded data")
                break
        
        answer = solution.get("answer")
        logger.info(f"Answer: {answer} (type: {type(answer).__name__})")
        logger.info(f"Reasoning: {solution.get('reasoning')}")
        
        # Get submit URL
        submit_url = solution.get("submit_url")
        if not submit_url:
            logger.error("No submit URL found!")
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
        
        logger.info(f"Submitting to: {submit_url}")
        
        try:
            resp = requests.post(submit_url, json=payload, timeout=30)
            data = resp.json()
            logger.info(f"Response: {data}")
            
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
                logger.info("✓ CORRECT!")
                next_url = data.get("url")
                if next_url:
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.info("🎉 Quiz completed!")
                    break
            else:
                logger.warning(f"✗ WRONG: {reason}")
                next_url = data.get("url")
                if next_url:
                    logger.info("Moving to next question...")
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.warning("No next URL, ending quiz")
                    break
        
        except Exception as e:
            logger.error(f"Submit error: {e}")
            import traceback
            traceback.print_exc()
            break
    
    correct_count = sum(1 for r in results if r.get("correct"))
    logger.info(f"\n{'='*60}")
    logger.info(f"QUIZ COMPLETE: {correct_count}/{len(results)} correct")
    logger.info(f"{'='*60}")
    
    return results

@app.route('/', methods=['POST'])
def quiz_endpoint():
    """Main quiz endpoint"""
    try:
        data = request.get_json(force=True)
    except:
        return jsonify({"error": "Invalid JSON"}), 400
    
    if data.get("secret") != SECRET:
        logger.warning(f"Invalid secret attempt")
        return jsonify({"error": "Invalid secret"}), 403
    
    if data.get("email") != EMAIL:
        logger.warning(f"Invalid email")
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
    """Health check endpoint"""
    return jsonify({"status": "ok"}), 200

@app.route('/test', methods=['GET'])
def test():
    """Test configuration endpoint"""
    return jsonify({
        "email": EMAIL,
        "secret_set": bool(SECRET),
        "api_key_set": bool(GROQ_API_KEY),
        "groq_ready": groq_client is not None
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting server on port {port}")
    logger.info(f"Email: {EMAIL}")
    logger.info(f"Secret configured: {'Yes' if SECRET else 'No'}")
    logger.info(f"Groq API key: {'Configured' if GROQ_API_KEY else 'MISSING'}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
