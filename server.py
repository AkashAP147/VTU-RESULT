import os
import re
import uuid
import base64
import socket
import requests
import json
import urllib3
import tempfile
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify, request
import firebase_admin
from firebase_admin import credentials, db
from captcha_bypass import CaptchaSolver

# Suppress InsecureRequestWarning for VTU SSL issues
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Store active scrape sessions
CRAWL_SESSIONS = {}

# Initialize Flask app
app = Flask(__name__, template_folder='templates')
app.secret_key = 'vtu-viewer-secret-key'
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Initialize Firebase
if not firebase_admin._apps:
    firebase_creds_env = os.environ.get("FIREBASE_CREDENTIALS") or os.environ.get("FIREBASE_CONFIG")
    if firebase_creds_env:
        try:
            cred_dict = json.loads(firebase_creds_env)
            cred = credentials.Certificate(cred_dict)
        except json.JSONDecodeError as e:
            print("❌ ERROR: FIREBASE_CONFIG is present but invalid JSON! Did you accidentally enter 'FIREBASE_CONFIG {' at the start of the value?")
            print(f"Error details: {e}")
            raise
    else:
        if not os.path.exists("serviceAccountKey.json"):
            print("❌ ERROR: FIREBASE_CONFIG environment variable is entirely MISSING in Render deployment! Did you save it?")
            raise ValueError("Missing FIREBASE_CONFIG env variable and lacking serviceAccountKey.json locally.")
        cred = credentials.Certificate("serviceAccountKey.json")
        
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://vtu-result-7-default-rtdb.firebaseio.com/'
    })

# Initialize Captcha Solver
try:
    print("⏳ Initializing Captcha Solver...")
    captcha_solver = CaptchaSolver()
    print("✅ Captcha Solver initialized successfully!")
except Exception as e:
    captcha_solver = None
    print(f"❌ Failed to initialize Captcha Solver: {e}")

# ===============================
# VTU RESULT URLS (BATCH-WISE)
# ===============================

BATCH_SEM_URLS = {
    "2026":{
        "1":"https://results.vtu.ac.in/JJRVcbcs25/index.php",
    },
    "2025": {
        "1": "https://results.vtu.ac.in/D25J26Ecbcs/index.php",
    },
    "2024": {
        "1": "https://results.vtu.ac.in/DJcbcs25/index.php",
        "2": "https://results.vtu.ac.in/JJEcbcs25/index.php",
        "3": "https://results.vtu.ac.in/D25J26Ecbcs/index.php",
    },
    "2023": {
        "1": "https://results.vtu.ac.in/DJcbcs24/index.php",
        "2": "https://results.vtu.ac.in/JJEcbcs24/index.php",
        "3": "https://results.vtu.ac.in/DJcbcs25/index.php",
        "4": "https://results.vtu.ac.in/JJEcbcs25/index.php",
        "5": "https://results.vtu.ac.in/D25J26Ecbcs/index.php",
    },
    "2022": {
        "1": "https://results.vtu.ac.in/JFEcbcs23/index.php",
        "2": "https://results.vtu.ac.in/JJEcbcs23/index.php",
        "3": "https://results.vtu.ac.in/DJcbcs24/index.php",
        "4": "https://results.vtu.ac.in/JJEcbcs24/index.php",
        "5": "https://results.vtu.ac.in/DJcbcs25/index.php",
        "6": "https://results.vtu.ac.in/JJEcbcs25/index.php",
        "7": "https://results.vtu.ac.in/D25J26Ecbcs/index.php",
    },
    "2021": {
        "1": "https://results.vtu.ac.in/FMEcbcs22/index.php",
        "3": "https://results.vtu.ac.in/JFEcbcs23/index.php",
        "4": "https://results.vtu.ac.in/JJEcbcs23/index.php",
        "5": "https://results.vtu.ac.in/DJcbcs24/index.php",
        "6": "https://results.vtu.ac.in/JJEcbcs24/index.php",
        "7": "https://results.vtu.ac.in/DJcbcs25/index.php",
        "8": "https://results.vtu.ac.in/JJEcbcs25/index.php",
    },
}

BATCH_SEM_REVAL_URLS = {
     "2023": {
         "1": "https://results.vtu.ac.in/DJRVcbcs24/index.php",
         "2": "https://results.vtu.ac.in/JJRVcbcs24/index.php",
         "3": "https://results.vtu.ac.in/DJRVcbcs25/index.php",
         "4": "https://results.vtu.ac.in/JJRVcbcs25/index.php",
         "5": "https://results.vtu.ac.in/D25J26RVcbcs/index.php"
     },
}

BATCH_SEM_MAKEUP_URLS = {
     "2023": {
         "3": "https://results.vtu.ac.in/MakeUpEcbcs24/index.php",
         "5": "https://results.vtu.ac.in/MakeUpEcbcs25/index.php"
     },
}

# ===============================
# CORE FUNCTIONS (NO SELENIUM)
# ===============================

def parse_usn(usn):
    usn = usn.upper()
    year_digits = usn[3:5]
    branch_code = usn[5:7]
    serial = int(usn[-3:])

    if serial >= 400:
        batch_year = str(2000 + int(year_digits) - 1)
    else:
        batch_year = str(2000 + int(year_digits))

    branch_map = {
        "CI": "Artificial Intelligence & Machine Learning",
        "CS": "Computer Science",
        "EC": "Electronics & Communication",
        "CV": "Civil Engineering",
        "ME": "Mechanical Engineering"
    }

    branch_name = branch_map.get(branch_code, "Unknown")
    return batch_year, branch_code, branch_name

def parse_exam_name(exam_name):
    name = exam_name.lower()
    if "makeup" in name:
        exam_type = "makeup"
    elif "revaluation" in name:
        exam_type = "revaluation"
    else:
        exam_type = "regular"
        
    year_match = re.findall(r'(20\d{2})', exam_name)
    year = year_match[-1] if year_match else ""
    
    month_match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)(?:\s*/\s*(january|february|march|april|may|june|july|august|september|october|november|december))?', exam_name, re.IGNORECASE)
    if month_match:
        months = [m.capitalize() for m in month_match.groups() if m]
        month = "_".join(months)
    else:
        month = ""
    return exam_type, year, month

def extract_result(html, semester):
    soup = BeautifulSoup(html, "html.parser")

    if "University Seat Number" not in soup.text:
        return None

    usn = ""
    name = ""
    exam_name = ""
    panel_heading = soup.find("div", class_="panel-heading")
    if panel_heading:
        b_tags = panel_heading.find_all("b")
        if b_tags and len(b_tags) > 1:
            exam_name = b_tags[-1].text.strip()
        elif b_tags:
            exam_name = b_tags[0].text.strip()

    semester_html = ""
    for div in soup.find_all("div", style=lambda v: v and "text-align:center" in v):
        if "Semester" in div.text:
            semester_html = div.text
            break
            
    semester_num = ""
    match = re.search(r"Semester\s*:?\s*(\d+)", semester_html)
    if match:
        semester_num = match.group(1)
    else:
        semester_num = semester

    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 2:
                if "University Seat Number" in cols[0].text:
                    usn = cols[1].text.strip()
                if "Student Name" in cols[0].text:
                    name = cols[1].text.strip()

    subjects = []
    rows = soup.find_all("div", class_="divTableRow")
    date_col_idx = None
    for row in rows:
        cols = row.find_all("div", class_="divTableCell")
        if cols and any("Announced" in c.text or "Updated on" in c.text for c in cols):
            for idx, c in enumerate(cols):
                if "Announced" in c.text or "Updated on" in c.text:
                    date_col_idx = idx
                    break
            break

    for row in rows:
        cols = row.find_all("div", class_="divTableCell")
        if not cols or cols[0].text.strip() == "Subject Code":
            continue
            
        result_date = ""
        if date_col_idx is not None and len(cols) > date_col_idx:
            result_date = cols[date_col_idx].text.strip()
            
        if len(cols) >= 9 and (
            "Old Marks" in [c.text for c in rows[0].find_all("div", class_="divTableCell")] or
            "RV Marks" in [c.text for c in rows[0].find_all("div", class_="divTableCell")]
        ):
            subjects.append({
                "type": "revaluation",
                "subject_code": cols[0].text.strip(),
                "subject_name": cols[1].text.strip(),
                "internal": cols[2].text.strip(),
                "old_marks": cols[3].text.strip(),
                "old_result": cols[4].text.strip(),
                "rv_marks": cols[5].text.strip(),
                "rv_result": cols[6].text.strip(),
                "final_marks": cols[7].text.strip(),
                "final_result": cols[8].text.strip(),
                "result_date": result_date
            })
        elif len(cols) >= 7:
            subjects.append({
                "type": "regular",
                "subject_code": cols[0].text.strip(),
                "subject_name": cols[1].text.strip(),
                "internal": cols[2].text.strip(),
                "external": cols[3].text.strip() if len(cols) > 3 else "",
                "total": cols[4].text.strip() if len(cols) > 4 else "",
                "result": cols[5].text.strip() if len(cols) > 5 else "",
                "result_date": result_date
            })

    return {
        "usn": usn,
        "name": name,
        "semester": semester_num,
        "subjects": subjects,
        "exam_name": exam_name
    }

def save_to_firebase(data):
    usn = data["usn"].replace(":", "").strip().lower()
    name = data["name"].replace(":", "").strip()
    semester = data["semester"]
    subjects = data["subjects"]
    exam_name = data.get("exam_name", "")

    batch_year, branch_code, branch_name = parse_usn(usn)

    student_ref = db.reference(f"students/{usn}")
    student_ref.update({
        "name": name,
        "batch": batch_year,
        "branch_code": branch_code,
        "branch": branch_name
    })

    semesters_ref = db.reference(f"students/{usn}/semesters")
    existing_sems = semesters_ref.get() or {}
    if isinstance(existing_sems, list):
        existing_sems = {str(i): v for i, v in enumerate(existing_sems)}

    exam_type, year, month = parse_exam_name(exam_name)
    attempt_id = "_".join([x for x in [year, month, exam_type] if x])

    for subject in subjects:
        subject_code = subject["subject_code"]
        subject_name = subject["subject_name"].replace(":", "").strip()
        backlog_found = False
        
        result = subject.get("final_result") or subject.get("result")
        for prev_sem, prev_data in existing_sems.items():
            if prev_sem == semester:
                continue
            if not prev_data:
                continue
            if subject_code in prev_data:
                prev_result = prev_data[subject_code].get("result", "")
                if prev_result.lower() != "pass" and result.lower() == "pass":
                    db.reference(
                        f"students/{usn}/semesters/{prev_sem}/{subject_code}"
                    ).update({
                        "backlog_cleared_in": semester,
                        "final_result": "Pass"
                    })
                    backlog_found = True
                    
        save_data = {
            "subject_name": subject_name,
            "internal": subject["internal"],
            "result_date": subject.get("result_date", ""),
            "exam_name": exam_name,
            "attempt_id": attempt_id,
            "exam_type": exam_type,
            "exam_year": year,
            "exam_month": month
        }
        if subject.get("type") == "revaluation":
            save_data.update({
                "old_marks": subject["old_marks"],
                "old_result": subject["old_result"],
                "rv_marks": subject["rv_marks"],
                "rv_result": subject["rv_result"],
                "final_marks": subject["final_marks"],
                "final_result": subject["final_result"],
                "is_revaluation": True
            })
        else:
            save_data.update({
                "external": subject["external"],
                "total": subject["total"],
                "result": subject["result"],
                "is_backlog_attempt": backlog_found,
                "is_revaluation": False
            })
        db.reference(
            f"students/{usn}/semesters/{semester}/{subject_code}/attempts/{attempt_id}"
        ).set(save_data)

    print(f"✅ Saved: {usn} | Sem {semester} | Attempt {attempt_id}")


# ===============================
# API ENDPOINTS
# ===============================

@app.route('/', methods=['GET'])
def index():
    return render_template('student.html')

@app.route('/api/student/<usn>', methods=['GET'])
def get_student(usn):
    usn_key = usn.replace(":", "").strip().lower()
    student_ref = db.reference(f"students/{usn_key}")
    student_data = student_ref.get()
    
    if student_data:
        return jsonify({"success": True, "data": student_data}), 200
    else:
        return jsonify({"success": False, "message": "USN not found. Results might not be published or scraped yet.", "status": 404}), 404

@app.route('/api/available_sems/<usn>', methods=['GET'])
def get_available_sems(usn):
    try:
        batch_year, _, _ = parse_usn(usn)
    except Exception:
        return jsonify({"success": False, "message": "Invalid USN Format."}), 400
        
    exam_type = request.args.get('exam_type', 'regular')
    
    url_dict = BATCH_SEM_URLS
    if exam_type == 'revaluation':
        url_dict = BATCH_SEM_REVAL_URLS
    elif exam_type == 'makeup':
        url_dict = BATCH_SEM_MAKEUP_URLS
        
    available_sems = url_dict.get(batch_year, {})
    semesters = sorted(list(available_sems.keys()), key=int)
    
    return jsonify({"success": True, "batch": batch_year, "semesters": semesters}), 200

@app.route('/api/scrape/init', methods=['POST'])
def init_scrape():
    data = request.json or {}
    usn = data.get('usn', '').strip().upper()
    semester = data.get('semester', '').strip()
    exam_type = data.get('exam_type', 'regular')
    
    if not usn or not semester:
        return jsonify({"success": False, "message": "USN and semester are required."}), 400
        
    try:
        batch_year, _, _ = parse_usn(usn)
    except Exception:
        return jsonify({"success": False, "message": "Invalid USN Format."}), 400
        
    url_dict = BATCH_SEM_URLS
    if exam_type == 'revaluation':
        url_dict = BATCH_SEM_REVAL_URLS
    elif exam_type == 'makeup':
        url_dict = BATCH_SEM_MAKEUP_URLS
        
    available_sems = url_dict.get(batch_year, {})
    if semester not in available_sems:
        return jsonify({"success": False, "message": f"Results for Sem {semester} not available in {exam_type} (Batch {batch_year})."}), 404
        
    vtu_url = available_sems[semester]
    
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"})
    
    try:
        res = sess.get(vtu_url, timeout=10, verify=False)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        if res.status_code != 200:
            return jsonify({"success": False, "message": f"VTU site returned status {res.status_code}"}), 500
            
        token_input = soup.find('input', {'name': 'Token'})
        token = token_input.get('value', '') if token_input else ""
            
        form = soup.find('form')
        if form and form.get('action'):
            submit_url = urljoin(vtu_url, form.get('action'))
        else:
            submit_url = urljoin(vtu_url, 'resultpage.php')
            
        img_tag = soup.find('img', src=re.compile(r'captcha', re.I))
        if not img_tag:
            return jsonify({"success": False, "message": "Could not find Captcha image on VTU page."}), 500
            
        captcha_url = urljoin(vtu_url, img_tag['src'])
        
        img_res = sess.get(captcha_url, timeout=10, verify=False)
        img_b64 = base64.b64encode(img_res.content).decode('utf-8')
        
        session_id = str(uuid.uuid4())
        CRAWL_SESSIONS[session_id] = {
            "session": sess,
            "url": vtu_url,
            "submit_url": submit_url,
            "token": token,
            "usn": usn,
            "semester": semester,
            "exam_type": exam_type
        }
        
        return jsonify({
            "success": True,
            "session_id": session_id,
            "captcha_b64": f"data:image/png;base64,{img_b64}",
            "batch": batch_year
        })
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Connection error: {str(e)}"}), 500

@app.route('/api/scrape/submit', methods=['POST'])
def submit_scrape():
    data = request.json or {}
    session_id = data.get('session_id')
    captcha_text = data.get('captcha_text', '').strip()
    
    if not session_id or session_id not in CRAWL_SESSIONS:
        return jsonify({"success": False, "message": "Session expired or invalid. Please try again."}), 400
        
    if not captcha_text:
        return jsonify({"success": False, "message": "Captcha text required."}), 400
        
    s_data = CRAWL_SESSIONS[session_id]
    sess = s_data["session"]
    post_url = s_data.get("submit_url", s_data["url"].replace("index.php", "resultpage.php"))
    
    payload = {
        "Token": s_data["token"],
        "lns": s_data["usn"],
        "captchacode": captcha_text
    }
    
    try:
        res = sess.post(post_url, data=payload, timeout=15, verify=False)
        html = res.text
        
        if "Invalid captcha" in html.lower() or "invalid code" in html.lower() or "alert('invalid captcha code" in html.lower() or "alert(\"invalid captcha code" in html.lower() or "invalid image code" in html.lower():
            del CRAWL_SESSIONS[session_id]
            return jsonify({"success": False, "message": "Invalid Captcha Code. Please try again."}), 400
            
        if "University Seat Number is not available or Invalid" in html or "alert('invalid" in html.lower():
            del CRAWL_SESSIONS[session_id]
            return jsonify({"success": False, "message": "USN not available or Invalid on VTU server."}), 404
            
        if "University Seat Number" not in html:
            del CRAWL_SESSIONS[session_id]
            return jsonify({"success": False, "message": "Extraction failed. Unexpected page content."}), 500
            
        result_data = extract_result(html, s_data["semester"])
        if not result_data:
            del CRAWL_SESSIONS[session_id]
            return jsonify({"success": False, "message": "Could not parse result data from HTML."}), 500
            
        save_to_firebase(result_data)
        del CRAWL_SESSIONS[session_id]
        
        return jsonify({"success": True, "message": "Scraped and saved successfully!"}), 200
        
    except Exception as e:
        if session_id in CRAWL_SESSIONS:
            del CRAWL_SESSIONS[session_id]
        return jsonify({"success": False, "message": f"Error scraping: {str(e)}"}), 500

@app.route('/api/scrape/auto', methods=['POST'])
def auto_scrape():
    if not captcha_solver:
        return jsonify({"success": False, "message": "Captcha solver not initialized on backend."}), 500

    data = request.json or {}
    usn = data.get('usn', '').strip().upper()
    semester = data.get('semester', '').strip()
    exam_type = data.get('exam_type', 'regular')
    
    if not usn or not semester:
        return jsonify({"success": False, "message": "USN and semester are required."}), 400
        
    try:
        batch_year, _, _ = parse_usn(usn)
    except Exception:
        return jsonify({"success": False, "message": "Invalid USN Format."}), 400
        
    url_dict = BATCH_SEM_URLS
    if exam_type == 'revaluation':
        url_dict = BATCH_SEM_REVAL_URLS
    elif exam_type == 'makeup':
        url_dict = BATCH_SEM_MAKEUP_URLS
        
    available_sems = url_dict.get(batch_year, {})
    if semester not in available_sems:
        return jsonify({"success": False, "message": f"Results for Sem {semester} not available in {exam_type} (Batch {batch_year})."}), 404
        
    vtu_url = available_sems[semester]
    
    max_retries = 12
    for attempt_num in range(1, max_retries + 1):
        sess = requests.Session()
        sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"})
        
        try:
            res = sess.get(vtu_url, timeout=10, verify=False)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            if res.status_code != 200:
                return jsonify({"success": False, "message": f"VTU site returned status {res.status_code}"}), 500
                
            token_input = soup.find('input', {'name': 'Token'})
            token = token_input.get('value', '') if token_input else ""
                
            form = soup.find('form')
            if form and form.get('action'):
                submit_url = urljoin(vtu_url, form.get('action'))
            else:
                submit_url = urljoin(vtu_url, 'resultpage.php')
                
            img_tag = soup.find('img', src=re.compile(r'captcha', re.I))
            if not img_tag:
                return jsonify({"success": False, "message": "Could not find Captcha image on VTU page."}), 500
                
            captcha_url = urljoin(vtu_url, img_tag['src'])
            img_res = sess.get(captcha_url, timeout=10, verify=False)
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_img:
                temp_img.write(img_res.content)
                temp_img_path = temp_img.name
                
            # Solve with our imported module
            captcha_text = captcha_solver.solve_from_image(temp_img_path)
            os.remove(temp_img_path)
            
            # Simple length check to rule out badly extracted strings
            if not captcha_text or len(captcha_text) != 6:
                print(f"Attempt {attempt_num}: Model generated '{captcha_text}', retrying...")
                continue
                
            payload = {
                "Token": token,
                "lns": usn,
                "captchacode": captcha_text
            }
            
            post_res = sess.post(submit_url, data=payload, timeout=15, verify=False)
            html = post_res.text
            
            if "Invalid captcha" in html.lower() or "invalid code" in html.lower() or "alert('invalid captcha code" in html.lower() or "alert(\"invalid captcha code" in html.lower() or "invalid image code" in html.lower():
                print(f"Attempt {attempt_num}: VTU rejected guess '{captcha_text}'")
                continue
                
            if "University Seat Number is not available or Invalid" in html or "alert('invalid" in html.lower():
                return jsonify({"success": False, "message": "USN not available or Invalid on VTU server."}), 404
                
            if "University Seat Number" not in html:
                return jsonify({"success": False, "message": "Extraction failed. Unexpected page content."}), 500
                
            result_data = extract_result(html, semester)
            if not result_data:
                return jsonify({"success": False, "message": "Could not parse result data from HTML."}), 500
                
            save_to_firebase(result_data)
            return jsonify({"success": True, "message": f"Scraped automatically successfully after {attempt_num} attempts!"}), 200
            
        except requests.exceptions.Timeout:
            print(f"Attempt {attempt_num}: Request timed out, retrying...")
            continue
        except Exception as e:
            print(f"Attempt {attempt_num} Error: {e}")
            if attempt_num == max_retries:
                return jsonify({"success": False, "message": f"Error scraping: {str(e)}"}), 500
                
    return jsonify({"success": False, "message": f"Failed to bypass Captcha after {max_retries} attempts."}), 400

def find_free_port(start_port=5000, max_tries=20):
    port = start_port
    for _ in range(max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                port += 1
    return 5000 # fallback

if __name__ == '__main__':
    port = int(os.environ.get("PORT", find_free_port(5000, 20)))
    print(f"==================================================")
    print(f"🚀 Deployment Server App is running on port {port}")
    print(f"==================================================")
    # 0.0.0.0 is needed for deployment platforms like Render, Heroku
    app.run(host='0.0.0.0', port=port, debug=False)
