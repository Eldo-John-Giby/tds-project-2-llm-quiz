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
import io

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
            "success": True
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
        
        logger.info(f"Downloading file from: {url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '').lower()
        
        # Return appropriate format based on content type
        if 'text' in content_type or 'csv' in content_type or 'json' in content_type:
            return {
                "success": True,
                "content": response.text,
                "type": "text",
                "content_type": content_type
            }
        else:
            # Binary files (PDF, images, etc.) - return base64
            return {
                "success": True,
                "content": base64.b64encode(response.content).decode('utf-8'),
                "type": "binary",
                "content_type": content_type
            }
    except Exception as e:
        logger.error(f"Error downloading {url}: {e}")
        return {
            "success": False,
            "error": str(e)
        }

def extract_urls_from_page(html_content, base_url):
    """Extract file URLs and links from HTML"""
    urls = []
    
    # Find href links
    href_pattern = r'href=["\']([^"\']+)["\']'
    hrefs = re.findall(href_pattern, html_content)
    
    # Find src attributes
    src_pattern = r'src=["\']([^"\']+)["\']'
    srcs = re.findall(src_pattern, html_content)
    
    all_urls = hrefs + srcs
    
    for url in all_urls:
        # Skip javascript, mailto, etc.
        if url.startswith(('javascript:', 'mailto:', '#', 'data:')):
            continue
        
        # Make absolute URL
        if not url.startswith(('http://', 'https://')):
            url = urljoin(base_url, url)
        
        urls.append(url)
    
    return list(set(urls))  # Remove duplicates

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
                    # Include text content (truncated if too long)
                    content = file_data.get('content', '')
                    if len(content) > 5000:
                        content = content[:5000] + "\n...(truncated)"
                    files_context += f"\nFile: {url}\nContent:\n{content}\n"
                else:
                    files_context += f"\nFile: {url}\nType: {file_data.get('content_type')}\n(Binary file available as base64)\n"
    
    context = ""
    if previous_attempts:
        context = f"\nPrevious attempts:\n{json.dumps(previous_attempts, indent=2)}\n"
    
    prompt = f"""You are solving a data analysis quiz. Analyze this page and solve the task.

QUIZ URL: {quiz_url}

PAGE CONTENT:
{page_content[:10000]}

{files_context}

{context}

Instructions:
1. Read the page carefully - it may have base64 encoded content that needs decoding
2. Identify the task (download file, analyze data, create visualization, scrape data, etc.)
3. Look at any downloaded files provided above
4. If you need to scrape another URL mentioned in the page, note it in scrape_urls
5. Determine the submit URL where the answer should be posted
6. Calculate/generate the correct answer based on ALL available data
7. Format answer exactly as requested (number, string, boolean, object, or base64)

Respond with ONLY valid JSON (no markdown, no extra text):
{{
  "task": "brief description",
  "submit_url": "URL to POST answer",
  "file_urls": ["url1", "url2"] or [],
  "scrape_urls": ["url1", "url2"] or [],
  "answer": <actual answer - must match expected type>,
  "reasoning": "brief explanation of how you got the answer"
}}

CRITICAL: 
- Decode base64 content in <script> tags first
- Answer type must match exactly (number vs string vs boolean)
- If asked for a number, provide a NUMBER not a string
- If asked for a string, provide a STRING
- Include full submit URL (may need to construct it from page content)
- Use data from downloaded files to calculate the answer
- If a link is relative, note it as relative and we'll resolve it"""

    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a data analysis expert. Respond with valid JSON only, no markdown."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1,
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
        logger.error(f"Response text: {response_text if 'response_text' in locals() else 'N/A'}")
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
        
        # Decode base64 content
        content = decode_base64_in_page(page_data['content'])
        logger.info(f"Page preview: {content[:300]}...")
        
        # Extract URLs from page
        page_urls = extract_urls_from_page(content, current_url)
        logger.info(f"Found {len(page_urls)} URLs in page")
        
        # First pass: get initial solution to see what files/URLs are needed
        solution = solve_with_groq(content, current_url, None, results[-1:] if results else None)
        
        if not solution:
            logger.error("Failed to get solution")
            break
        
        logger.info(f"Task: {solution.get('task')}")
        
        # Download any files mentioned
        downloaded_files = {}
        file_urls = solution.get('file_urls', [])
        
        if file_urls:
            for file_url in file_urls:
                if file_url:
                    file_data = download_file(file_url, current_url)
                    if file_data.get('success'):
                        downloaded_files[file_url] = file_data
                        logger.info(f"Downloaded: {file_url} ({file_data.get('type')})")
        
        # Scrape any additional URLs mentioned
        scrape_urls = solution.get('scrape_urls', [])
        if scrape_urls:
            for scrape_url in scrape_urls:
                if scrape_url:
                    # Make absolute URL
                    if not scrape_url.startswith(('http://', 'https://')):
                        scrape_url = urljoin(current_url, scrape_url)
                    
                    scraped_data = fetch_page_content(scrape_url)
                    if scraped_data.get('success'):
                        # Decode if needed
                        scraped_content = decode_base64_in_page(scraped_data['content'])
                        downloaded_files[scrape_url] = {
                            'success': True,
                            'content': scraped_content,
                            'type': 'text'
                        }
                        logger.info(f"Scraped: {scrape_url}")
        
        # Second pass: solve with downloaded files
        if downloaded_files:
            logger.info(f"Re-solving with {len(downloaded_files)} downloaded resources")
            solution = solve_with_groq(content, current_url, downloaded_files, results[-1:] if results else None)
            
            if not solution:
                logger.error("Failed to get solution with files")
                break
        
        logger.info(f"Answer: {solution.get('answer')}")
        logger.info(f"Answer type: {type(solution.get('answer')).__name__}")
        logger.info(f"Reasoning: {solution.get('reasoning')}")
        
        # Submit answer
        submit_url = solution.get('submit_url')
        if not submit_url:
            logger.error("No submit URL found!")
            break
        
        # Make submit URL absolute if needed
        if not submit_url.startswith(('http://', 'https://')):
            submit_url = urljoin(current_url, submit_url)
        
        payload = {
            "email": EMAIL,
            "secret": SECRET,
            "url": current_url,
            "answer": solution['answer']
        }
        
        logger.info(f"Submitting to: {submit_url}")
        logger.info(f"Payload: {json.dumps(payload, default=str)}")
        
        try:
            response = requests.post(submit_url, json=payload, timeout=30)
            response_data = response.json()
            
            logger.info(f"Response: {response_data}")
            
            correct = response_data.get('correct', False)
            reason = response_data.get('reason', '')
            
            results.append({
                "question": question_count,
                "url": current_url,
                "answer": solution['answer'],
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
                    logger.info("🎉 Quiz completed!")
                    break
            else:
                logger.warning(f"✗ WRONG: {reason}")
                next_url = response_data.get('url')
                if next_url:
                    logger.info("Moving to next question...")
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    logger.warning("No next URL provided, ending quiz")
                    break
                    
        except Exception as e:
            logger.error(f"Submit error: {e}")
            import traceback
            traceback.print_exc()
            break
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Quiz completed: {question_count} questions")
    correct_count = sum(1 for r in results if r.get('correct'))
    logger.info(f"Score: {correct_count}/{question_count}")
    logger.info(f"Results: {json.dumps(results, indent=2)}")
    logger.info(f"{'='*60}")
    
    return results

@app.route('/', methods=['POST'])
def quiz_endpoint():
    """Main endpoint"""
    
    # Validate JSON
    try:
        data = request.get_json(force=True)
    except:
        return jsonify({"error": "Invalid JSON"}), 400
    
    # Verify secret
    if data.get('secret') != SECRET:
        logger.warning(f"Invalid secret attempt: {data.get('secret')}")
        return jsonify({"error": "Invalid secret"}), 403
    
    # Verify email
    if data.get('email') != EMAIL:
        logger.warning(f"Invalid email: {data.get('email')}")
        return jsonify({"error": "Email mismatch"}), 403
    
    quiz_url = data.get('url')
    if not quiz_url:
        return jsonify({"error": "No URL provided"}), 400
    
    logger.info(f"Received quiz request for: {quiz_url}")
    
    # Process in background
    def async_quiz():
        try:
            process_quiz(quiz_url)
        except Exception as e:
            logger.error(f"Quiz processing error: {e}", exc_info=True)
    
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
        "groq_client_ready": groq_client is not None
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting server on port {port}")
    logger.info(f"Email: {EMAIL}")
    logger.info(f"Secret configured: {bool(SECRET)}")
    logger.info(f"API key configured: {bool(GROQ_API_KEY)}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
