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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ===== UPDATE THESE VALUES =====
EMAIL = "24f2005903@ds.study.iitm.ac.in"
SECRET = "my_secret_key_12345"  # Change this to something unique!
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
# ================================

# Initialize Groq client
try:
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info("Groq client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Groq client: {e}")
    groq_client = None

def fetch_page_content(url):
    """Fetch page content with requests"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return {
            "content": response.text,
            "success": True,
            "url": response.url  # Final URL after redirects
        }
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return {
            "content": "",
            "success": False,
            "error": str(e)
        }

def download_file(url, base_url=None):
    """Download a file and return its content"""
    try:
        # Handle relative URLs
        if base_url and not url.startswith(('http://', 'https://')):
            url = urljoin(base_url, url)
        
        logger.info(f"Downloading: {url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '').lower()
        
        # Text files (CSV, JSON, TXT, HTML)
        if any(t in content_type for t in ['text', 'csv', 'json', 'html', 'xml']):
            return {
                "success": True,
                "content": response.text,
                "type": "text",
                "content_type": content_type,
                "url": url
            }
        else:
            # Binary files - return base64
            return {
                "success": True,
                "content": base64.b64encode(response.content).decode('utf-8'),
                "type": "binary",
                "content_type": content_type,
                "url": url
            }
    except Exception as e:
        logger.error(f"Error downloading {url}: {e}")
        return {
            "success": False,
            "error": str(e),
            "url": url
        }

def extract_links_from_html(html_content, base_url):
    """Extract all links from HTML"""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        links = []
        
        # Find all <a> tags with href
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not href.startswith(('javascript:', 'mailto:', '#')):
                absolute_url = urljoin(base_url, href)
                links.append(absolute_url)
        
        # Find all <link> tags (stylesheets, etc)
        for link in soup.find_all('link', href=True):
            href = link['href']
            absolute_url = urljoin(base_url, href)
            links.append(absolute_url)
        
        # Find all <script> and <img> src
        for tag in soup.find_all(['script', 'img', 'audio', 'video'], src=True):
            src = tag['src']
            if not src.startswith(('javascript:', 'data:')):
                absolute_url = urljoin(base_url, src)
                links.append(absolute_url)
        
        return list(set(links))  # Remove duplicates
    except Exception as e:
        logger.error(f"Error extracting links: {e}")
        return []

def decode_base64_in_page(html_content):
    """Extract and decode base64 content from page"""
    match = re.search(r'atob\([`\'"]([^`\'"]+)[`\'"]', html_content)
    if match:
        try:
            decoded = base64.b64decode(match.group(1)).decode('utf-8')
            return decoded
        except:
            pass
    return html_content

def solve_with_groq(page_content, quiz_url, downloaded_files=None, previous_attempts=None):
    """Use Groq to solve the quiz with enhanced context"""
    
    if not groq_client:
        logger.error("Groq client not initialized!")
        return None
    
    # Build context with downloaded files
    files_context = ""
    if downloaded_files:
        files_context = "\n\nDOWNLOADED FILES:\n"
        for url, file_data in downloaded_files.items():
            if file_data.get('success'):
                if file_data.get('type') == 'text':
                    content = file_data.get('content', '')
                    # Truncate if too long
                    if len(content) > 8000:
                        content = content[:8000] + "\n...(truncated)"
                    files_context += f"\nFile: {url}\nContent:\n{content}\n---\n"
                else:
                    files_context += f"\nFile: {url}\nType: Binary ({file_data.get('content_type')})\n"
    
    context = ""
    if previous_attempts:
        context = f"\nPrevious attempts:\n{json.dumps(previous_attempts[-2:], indent=2)}\n"
    
    prompt = f"""You are solving a data analysis quiz. Analyze this page and solve the task.

QUIZ URL: {quiz_url}

PAGE CONTENT:
{page_content[:12000]}

{files_context}

{context}

Instructions:
1. Read the page - it may have base64 encoded content (decode it first!)
2. Identify the exact task being asked
3. List any file URLs that need downloading (CSV, PDF, images, etc.)
4. List any page URLs that need scraping for data
5. Use downloaded file content to calculate the answer
6. Determine the exact submit URL (construct from page if needed)
7. Provide the answer in the EXACT format requested

Respond with ONLY valid JSON (no markdown, no extra text):
{{
  "task": "clear description of what's being asked",
  "submit_url": "full URL to POST answer to",
  "file_urls": ["url1", "url2"] or [],
  "scrape_urls": ["url_to_scrape"] or [],
  "answer": <actual answer in correct type>,
  "reasoning": "step-by-step how you got the answer"
}}

CRITICAL RULES:
- If the page has atob() with base64, DECODE IT FIRST to see the real question
- Answer must be correct TYPE: number vs "string" vs true/false vs {{"key": "value"}}
- If question says "sum", provide a NUMBER not string
- If question asks for text/name, provide a STRING
- Construct submit_url from page content if not obvious (look for <span class="origin">)
- Use ALL available data from downloaded files to calculate answer
- If you see relative URLs like "/demo-scrape-data", note them in scrape_urls"""

    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a data analysis expert. Respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=4096,
        )
        
        response_text = chat_completion.choices[0].message.content.strip()
        
        # Clean markdown
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        result = json.loads(response_text)
        return result
        
    except Exception as e:
        logger.error(f"Groq error: {e}")
        logger.error(f"Response: {response_text if 'response_text' in locals() else 'N/A'}")
        return None

def process_quiz(start_url):
    """Process the quiz chain with file downloading and scraping"""
    logger.info(f"{'#'*60}")
    logger.info(f"STARTING QUIZ: {start_url}")
    logger.info(f"{'#'*60}")
    
    current_url = start_url
    max_questions = 15
    question_count = 0
    results = []
    start_time = time.time()
    MAX_TIME = 170  # 2 min 50 sec
    
    while current_url and question_count < max_questions:
        if time.time() - start_time > MAX_TIME:
            logger.warning("Time limit approaching, stopping")
            break
            
        question_count += 1
        logger.info(f"\n{'='*60}")
        logger.info(f"Question {question_count}: {current_url}")
        logger.info(f"{'='*60}")
        
        # Fetch page
        page_data = fetch_page_content(current_url)
        if not page_data['success']:
            logger.error(f"Failed to fetch: {page_data.get('error')}")
            break
        
        actual_url = page_data.get('url', current_url)
        
        # Decode base64 content
        content = decode_base64_in_page(page_data['content'])
        logger.info(f"Page preview: {content[:400]}...")
        
        # Extract all links from page
        all_links = extract_links_from_html(content, actual_url)
        logger.info(f"Found {len(all_links)} links in page")
        
        # First pass: ask LLM what it needs
        solution = solve_with_groq(content, actual_url, None, results)
        
        if not solution:
            logger.error("Failed to get initial solution")
            break
        
        logger.info(f"Task: {solution.get('task')}")
        
        # Download files mentioned by LLM
        downloaded_files = {}
        file_urls = solution.get('file_urls', [])
        
        for file_url in file_urls:
            if file_url:
                file_data = download_file(file_url, actual_url)
                if file_data.get('success'):
                    downloaded_files[file_url] = file_data
                    logger.info(f"✓ Downloaded: {file_url}")
        
        # Scrape additional URLs mentioned
        scrape_urls = solution.get('scrape_urls', [])
        for scrape_url in scrape_urls:
            if scrape_url:
                # Make absolute
                if not scrape_url.startswith(('http://', 'https://')):
                    scrape_url = urljoin(actual_url, scrape_url)
                
                scraped = fetch_page_content(scrape_url)
                if scraped.get('success'):
                    scraped_content = decode_base64_in_page(scraped['content'])
                    downloaded_files[scrape_url] = {
                        'success': True,
                        'content': scraped_content,
                        'type': 'text',
                        'url': scrape_url
                    }
                    logger.info(f"✓ Scraped: {scrape_url}")
        
        # Second pass: solve with all downloaded data
        if downloaded_files:
            logger.info(f"Re-solving with {len(downloaded_files)} resources")
            solution = solve_with_groq(content, actual_url, downloaded_files, results)
            
            if not solution:
                logger.error("Failed with downloaded files")
                break
        
        answer = solution.get('answer')
        logger.info(f"Answer: {answer} (type: {type(answer).__name__})")
        logger.info(f"Reasoning: {solution.get('reasoning')}")
        
        # Get submit URL
        submit_url = solution.get('submit_url')
        if not submit_url:
            logger.error("No submit URL!")
            break
        
        # Make absolute
        if not submit_url.startswith(('http://', 'https://')):
            submit_url = urljoin(actual_url, submit_url)
        
        payload = {
            "email": EMAIL,
            "secret": SECRET,
            "url": current_url,
            "answer": answer
        }
        
        logger.info(f"Submitting to: {submit_url}")
        
        try:
            response = requests.post(submit_url, json=payload, timeout=30)
            response_data = response.json()
            
            logger.info(f"Response: {response_data}")
            
            correct = response_data.get('correct', False)
            reason = response_data.get('reason', '')
            
            results.append({
                "question": question_count,
                "url": current_url,
                "answer": answer,
                "correct": correct,
                "reason": reason
            })
            
            if correct:
                logger.info("✓ CORRECT!")
                next_url = response_data.get('url')
                if next_url:
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.info("🎉 Quiz completed successfully!")
                    break
            else:
                logger.warning(f"✗ WRONG: {reason}")
                next_url = response_data.get('url')
                if next_url:
                    logger.info("Moving to next question...")
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.warning("No next URL, ending")
                    break
                    
        except Exception as e:
            logger.error(f"Submit error: {e}")
            import traceback
            traceback.print_exc()
            break
    
    correct_count = sum(1 for r in results if r.get('correct'))
    logger.info(f"\n{'='*60}")
    logger.info(f"QUIZ COMPLETE: {correct_count}/{question_count} correct")
    logger.info(f"{'='*60}")
    
    return results

@app.route('/', methods=['POST'])
def quiz_endpoint():
    """Main endpoint"""
    
    try:
        data = request.get_json(force=True)
    except:
        return jsonify({"error": "Invalid JSON"}), 400
    
    if data.get('secret') != SECRET:
        logger.warning(f"Invalid secret: {data.get('secret')}")
        return jsonify({"error": "Invalid secret"}), 403
    
    if data.get('email') != EMAIL:
        logger.warning(f"Invalid email: {data.get('email')}")
        return jsonify({"error": "Email mismatch"}), 403
    
    quiz_url = data.get('url')
    if not quiz_url:
        return jsonify({"error": "No URL provided"}), 400
    
    logger.info(f"✓ Received quiz request: {quiz_url}")
    
    def async_quiz():
        try:
            process_quiz(quiz_url)
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
    
    thread = Thread(target=async_quiz, daemon=True)
    thread.start()
    
    return jsonify({
        "status": "accepted",
        "message": "Processing quiz"
    }), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

@app.route('/test', methods=['GET'])
def test():
    return jsonify({
        "email": EMAIL,
        "secret_set": bool(SECRET),
        "api_key_set": bool(GROQ_API_KEY),
        "groq_ready": groq_client is not None
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Server starting on port {port}")
    logger.info(f"Email: {EMAIL}")
    logger.info(f"Secret: {'*' * len(SECRET)}")
    logger.info(f"Groq API: {'configured' if GROQ_API_KEY else 'MISSING'}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
