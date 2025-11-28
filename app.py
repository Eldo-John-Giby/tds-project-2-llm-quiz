import os
import json
import base64
import requests
from flask import Flask, request, jsonify
import time
from threading import Thread
import re
from groq import Groq

app = Flask(__name__)

# ===== UPDATE THESE VALUES =====
EMAIL = "24f2005903@ds.study.iitm.ac.in"
SECRET = "my_secret_key_12345"  # Change this to something unique!
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")  # FREE from Groq
# ================================

# Initialize Groq client (FREE & FAST)
groq_client = Groq(api_key=GROQ_API_KEY)

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
        return {
            "content": "",
            "success": False,
            "error": str(e)
        }

def download_file(url):
    """Download file and return base64"""
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        return base64.b64encode(response.content).decode('utf-8')
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return None

def solve_with_groq(page_content, quiz_url, previous_attempts=None):
    """Use Groq (FREE & FAST) to understand and solve the quiz"""
    
    context = ""
    if previous_attempts:
        context = f"\nPrevious attempts and feedback:\n{json.dumps(previous_attempts, indent=2)}\n"
    
    prompt = f"""You are solving a data analysis quiz. Analyze this page and solve the task.

QUIZ URL: {quiz_url}

PAGE CONTENT:
{page_content[:15000]}

{context}

Instructions:
1. Read the page carefully - it may contain base64 encoded content that needs decoding
2. Identify what task needs to be done (download file, analyze data, create visualization, etc.)
3. Note any URLs for files to download or APIs to call
4. Determine the submit URL where the answer should be posted
5. Calculate/generate the correct answer
6. Format the answer exactly as requested (number, string, boolean, object, or base64)

Respond with ONLY a valid JSON object (no markdown, no explanation):
{{
  "task": "brief description",
  "submit_url": "URL to POST answer",
  "file_urls": ["url1", "url2"] or [],
  "answer": <actual answer>,
  "reasoning": "brief explanation of your answer"
}}

CRITICAL: 
- If page has base64 content in <script> tags, decode it first
- Answer must match the expected type exactly
- Numbers should be numbers, not strings
- Include the full submit URL
- Do not include markdown code blocks in your response"""

    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are a data analysis expert. Always respond with valid JSON only, no markdown."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model="llama-3.3-70b-versatile",  # Fast and smart
            temperature=0.1,
            max_tokens=4096,
        )
        
        response_text = chat_completion.choices[0].message.content.strip()
        
        # Clean up markdown if present
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        result = json.loads(response_text)
        return result
        
    except Exception as e:
        print(f"Groq error: {e}")
        print(f"Response was: {response_text if 'response_text' in locals() else 'no response'}")
        return None

def decode_base64_in_page(html_content):
    """Extract and decode base64 content from page"""
    # Look for atob() calls
    match = re.search(r'atob\([`\'"]([^`\'"]+)[`\'"]', html_content)
    if match:
        try:
            decoded = base64.b64decode(match.group(1)).decode('utf-8')
            return decoded
        except:
            pass
    return html_content

def process_quiz(start_url):
    """Process the quiz chain"""
    current_url = start_url
    max_questions = 15
    question_count = 0
    results = []
    start_time = time.time()
    MAX_TIME = 170  # 2 minutes 50 seconds
    
    while current_url and question_count < max_questions:
        if time.time() - start_time > MAX_TIME:
            print("Time limit approaching, stopping")
            break
            
        question_count += 1
        print(f"\n{'='*60}")
        print(f"Question {question_count}: {current_url}")
        print(f"{'='*60}")
        
        # Fetch page
        page_data = fetch_page_content(current_url)
        if not page_data['success']:
            print(f"Failed to fetch: {page_data.get('error')}")
            break
        
        # Decode any base64 content
        content = decode_base64_in_page(page_data['content'])
        print(f"Page content preview: {content[:500]}...")
        
        # Solve with Groq
        previous_attempts = results[-1:] if results else None
        solution = solve_with_groq(content, current_url, previous_attempts)
        
        if not solution:
            print("Failed to get solution")
            break
        
        print(f"\nTask: {solution.get('task')}")
        print(f"Answer: {solution.get('answer')}")
        print(f"Reasoning: {solution.get('reasoning')}")
        
        # Download files if needed
        downloaded_files = {}
        if solution.get('file_urls'):
            for file_url in solution['file_urls']:
                if file_url:
                    print(f"Downloading: {file_url}")
                    file_data = download_file(file_url)
                    if file_data:
                        downloaded_files[file_url] = file_data
        
        # Submit answer
        submit_url = solution.get('submit_url')
        if not submit_url:
            print("No submit URL found!")
            break
        
        payload = {
            "email": EMAIL,
            "secret": SECRET,
            "url": current_url,
            "answer": solution['answer']
        }
        
        print(f"\nSubmitting to: {submit_url}")
        
        try:
            response = requests.post(submit_url, json=payload, timeout=30)
            response_data = response.json()
            
            print(f"Response: {response_data}")
            
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
                print("✓ CORRECT!")
                next_url = response_data.get('url')
                if next_url:
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    print("\n🎉 Quiz completed!")
                    break
            else:
                print(f"✗ WRONG: {reason}")
                # Check if there's a skip URL
                next_url = response_data.get('url')
                if next_url:
                    print("Moving to next question...")
                    current_url = next_url
                    time.sleep(0.5)
                else:
                    break
                    
        except Exception as e:
            print(f"Submit error: {e}")
            break
    
    print(f"\n{'='*60}")
    print(f"Quiz session completed: {question_count} questions")
    print(f"{'='*60}")
    
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
        return jsonify({"error": "Invalid secret"}), 403
    
    # Verify email
    if data.get('email') != EMAIL:
        return jsonify({"error": "Email mismatch"}), 403
    
    quiz_url = data.get('url')
    if not quiz_url:
        return jsonify({"error": "No URL provided"}), 400
    
    print(f"\n{'#'*60}")
    print(f"NEW QUIZ REQUEST: {quiz_url}")
    print(f"{'#'*60}")
    
    # Process in background
    def async_quiz():
        try:
            results = process_quiz(quiz_url)
            print(f"\nFinal results:\n{json.dumps(results, indent=2)}")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
    
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
        "api_key_set": bool(GROQ_API_KEY)
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"Starting server on port {port}...")
    print(f"Email: {EMAIL}")
    print(f"Secret configured: {bool(SECRET)}")
    print(f"API key configured: {bool(GROQ_API_KEY)}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
