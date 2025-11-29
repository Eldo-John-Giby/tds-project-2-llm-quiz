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
    logger.info("Groq client initialized")
except Exception as e:
    logger.error(f"Groq init failed: {e}")
    groq_client = None


def fetch_page_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return {"content": response.text, "success": True, "url": response.url}
    except Exception as e:
        logger.error(f"Fetch failed {url}: {e}")
        return {"content": "", "success": False, "error": str(e)}


def download_file(url, base_url=None):
    try:
        if base_url and not url.startswith(('http://', 'https://')):
            url = urljoin(base_url, url)

        logger.info(f"Downloading: {url}")
        response = requests.get(url, timeout=60)
        response.raise_for_status()

        content_type = response.headers.get('content-type', '').lower()

        if any(t in content_type for t in ['text', 'csv', 'json', 'html']):
            return {"success": True, "content": response.text, "type": "text", "url": url}
        else:
            return {"success": True, "content": base64.b64encode(response.content).decode(), "type": "binary", "url": url}
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return {"success": False, "error": str(e)}


def decode_base64_in_page(html_content):
    match = re.search(r'atob\([`\'"]([^`\'"]+)[`\'"]', html_content)
    if match:
        try:
            return base64.b64decode(match.group(1)).decode()
        except:
            pass
    return html_content


def solve_with_groq(page_content, quiz_url, downloaded_files=None, previous_attempts=None):
    if not groq_client:
        return None

    files_context = ""
    if downloaded_files:
        files_context = "\n\nDOWNLOADED DATA:\n"
        for url, data in downloaded_files.items():
            if data.get("success") and data.get("type") == "text":
                content = data.get("content", "")[:8000]
                files_context += f"\n=== {url} ===\n{content}\n"

    context = ""
    if previous_attempts:
        context = f"\nPREVIOUS WRONG:\n{json.dumps(previous_attempts[-1:], indent=2)}\n"

    prompt = f"""Solve this data analysis quiz.

URL: {quiz_url}
PAGE: {page_content[:12000]}
{files_context}
{context}

STEPS:
1. Decode base64 (look for atob)
2. Read exact question
3. List files/URLs to download/scrape
4. Calculate answer
5. JSON output only

RETURN:
{{"task": "question", "submit_url": "url", "file_urls": [], "scrape_urls": [], "answer": <value>, "reasoning": "steps"}}"""

    try:
        response = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Data analysis expert. JSON only."},
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

        return json.loads(text)
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return None


def page_has_no_question(content):
    return ("How to play" in content and "/submit" in content and "question" not in content.lower())


def process_quiz(start_url):
    logger.info(f"{'#'*60}\nSTART: {start_url}\n{'#'*60}")
    current_url = start_url
    results = []
    start_time = time.time()

    for q in range(15):
        if time.time() - start_time > 170:
            break

        logger.info(f"\n{'='*60}\nQ{q+1}: {current_url}\n{'='*60}")
        page = fetch_page_content(current_url)
        if not page['success']:
            break

        content = decode_base64_in_page(page['content'])
        logger.info(f"Preview: {content[:200]}...")

        # FIX: initial handshake page (no question yet)
        if page_has_no_question(content):
            logger.info("No question found → submitting empty answer to unlock Q1")
            payload = {"email": EMAIL, "secret": SECRET, "url": current_url, "answer": ""}
            resp = requests.post(urljoin(current_url, "/submit"), json=payload)
            data = resp.json()
            logger.info(f"Handshake response: {data}")
            current_url = data.get("url", current_url)
            continue

        solution = solve_with_groq(content, page['url'], None, results)
        if not solution:
            break

        downloaded = {}
        for url in solution.get("file_urls", []):
            downloaded[url] = download_file(url, page['url'])

        for url in solution.get("scrape_urls", []):
            if not url.startswith("http"):
                url = urljoin(page['url'], url)
            scraped = fetch_page_content(url)
            if scraped["success"]:
                downloaded[url] = {"success": True, "content": decode_base64_in_page(scraped["content"]), "type": "text"}

        if downloaded:
            solution = solve_with_groq(content, page["url"], downloaded, results)

        answer = solution.get("answer")
        logger.info(f"Answer: {answer} ({type(answer).__name__})")

        submit_url = solution.get("submit_url")
        if not submit_url.startswith("http"):
            submit_url = urljoin(page["url"], submit_url)

        payload = {"email": EMAIL, "secret": SECRET, "url": current_url, "answer": answer}
        resp = requests.post(submit_url, json=payload)
        data = resp.json()
        logger.info(f"Response: {data}")

        correct = data.get("correct", False)
        results.append({"q": q+1, "url": current_url, "answer": answer, "correct": correct})

        if not correct and not data.get("url"):
            break

        current_url = data.get("url", current_url)
        time.sleep(0.4)

    correct = sum(1 for r in results if r.get("correct"))
    logger.info(f"\n{'='*60}\nSCORE: {correct}/{len(results)}\n{'='*60}")
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

    logger.info(f"✓ Request: {url}")
    Thread(target=lambda: process_quiz(url), daemon=True).start()
    return jsonify({"status": "accepted"}), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
