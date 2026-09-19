from flask import Flask, render_template, request, jsonify, send_file
import cv2
import pytesseract
import logging
import re
import json
import os
import numpy as np
import base64
import os
import sqlite3
from pathlib import Path
from typing import Tuple, Optional, Dict, List, Union
from werkzeug.utils import secure_filename
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib.units import inch
from io import BytesIO

app = Flask(__name__)


# Configure upload folder
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
MAX_IMAGES = 6

# Create uploads directory if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# File paths for storing student data
UNLIMITED_STUDENTS_FILE = 'unlimited_students.json'
LIMITED_STUDENTS_FILE = 'limited_students.json'

def load_unlimited_students():
    try:
        if not os.path.exists(UNLIMITED_STUDENTS_FILE):
            return []
        with open(UNLIMITED_STUDENTS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading class wise students: {e}")
        return []

def save_unlimited_students(students):
    try:
        with open(UNLIMITED_STUDENTS_FILE, 'w') as f:
            json.dump(students, f, indent=2)
    except Exception as e:
        print(f"Error saving class wise: {e}")

def load_limited_students():
    try:
        if not os.path.exists(LIMITED_STUDENTS_FILE):
            return []
        with open(LIMITED_STUDENTS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading student wise: {e}")
        return []

def save_limited_students(students):
    try:
        with open(LIMITED_STUDENTS_FILE, 'w') as f:
            json.dump(students, f, indent=2)
    except Exception as e:
        print(f"Error saving student wise: {e}")

# Database Initialization Function
def init_db():
    conn = sqlite3.connect('student_marks.db')
    cur = conn.cursor()
    # Ensure tables exist but do NOT drop them
    cur.execute('''
        CREATE TABLE IF NOT EXISTS student_marks1 (
            reg_number TEXT PRIMARY KEY,
            marks REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS student_marks_limited (
            reg_number TEXT PRIMARY KEY,
            marks REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


class PaperMarkExtractor:
    def __init__(self, tesseract_path: str = r'C:\Program Files\Tesseract-OCR\tesseract.exe'):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        try:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            self.custom_config = r'--oem 3 --psm 6'
        except Exception as e:
            self.logger.error(f"Failed to initialize Tesseract: {str(e)}")
            raise

    def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.convertScaleAbs(gray, alpha=1.5, beta=0)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary

    def extract_text(self, image: np.ndarray) -> str:
        try:
            processed_image = self.preprocess_image(image)
            text = pytesseract.image_to_string(processed_image, config=self.custom_config)
            return text
        except Exception as e:
            self.logger.error(f"Error in text extraction: {str(e)}")
            return ""

    def find_registration_number(self, text: str) -> Optional[str]:
        patterns = [
            r'reg(?:istration)?\s*(?:no|number|#)?\s*[:.]?\s*(\w+)',
            r'registration\s*[:.]?\s*(\w+)',
            r'(?<![\w])(\d{6,8})(?![\w])',
            r'(?<![\w])([A-Z]{1,3}\d{5,6})(?![\w])',
            r'student\s*(?:id|number)\s*[:.]?\s*(\w+)',
        ]
        
        text = text.upper()
        
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                reg_num = match.group(1).strip()
                if len(reg_num) >= 5:
                    self.logger.info(f"Found registration number: {reg_num}")
                    return reg_num
        
        return None

    def find_largest_number(self, array: List[Dict]) -> Optional[int]:
        if not array:
            return None
        max_number = max(item['marks'] for item in array)
        return max_number

    def calculate_paper_ratio(self, core_papers: List[Dict], text: str) -> Tuple[int, int, float]:
        total_papers = len([line for line in text.split('\n') 
                          if re.search(r'.*?(\d{1,3})\s*marks.*', line, re.IGNORECASE)])
        num_core_papers = len(core_papers)
        ratio = num_core_papers / total_papers if total_papers > 0 else 0
        return num_core_papers, total_papers, ratio

    def parse_paper_details(self, text: str) -> Tuple[List[Dict], List[Dict], List[Dict], List[List[str]], Optional[str]]:
        core_papers = []
        all_marks = []
        core_paper_marks = []
        paper_words = []
        registration_number = self.find_registration_number(text)

        lines = text.split('\n')
        current_core_paper = None

        paper_pattern = re.compile(r'.*core.*', re.IGNORECASE)
        marks_pattern = re.compile(r'.*?(\d{1,3})\s*marks.*', re.IGNORECASE)
        total_pattern = re.compile(r'.*total.*(\d{1,3}).*marks.*', re.IGNORECASE)

        for i, line in enumerate(lines):
            if paper_pattern.match(line):
                words = [word.strip() for word in line.split() if word.strip()]
                paper_words.append(words)

                current_core_paper = {
                    'paper_name': line.strip(),
                    'line_text': line,
                    'index': i,
                    'words': words
                }
                core_papers.append(current_core_paper)

            marks_match = marks_pattern.match(line)
            if marks_match:
                mark_info = {
                    'marks': int(marks_match.group(1)),
                    'line_text': line,
                    'index': i
                }
                all_marks.append(mark_info)

                if current_core_paper and abs(i - current_core_paper['index']) <= 2:
                    core_mark = {
                        'paper_name': current_core_paper['paper_name'],
                        'marks': int(marks_match.group(1)),
                        'line_text': line,
                        'words': current_core_paper.get('words', [])
                    }
                    core_paper_marks.append(core_mark)

            total_match = total_pattern.match(line)
            if total_match and current_core_paper and abs(i - current_core_paper['index']) <= 2:
                core_mark = {
                    'paper_name': current_core_paper['paper_name'],
                    'marks': int(total_match.group(1)),
                    'line_text': line,
                    'is_total': True,
                    'words': current_core_paper.get('words', [])
                }
                core_paper_marks.append(core_mark)

            if current_core_paper:
                for word in line.split():
                    if word.isdigit() and 0 <= int(word) <= 100:
                        core_mark = {
                            'paper_name': current_core_paper['paper_name'],
                            'marks': int(word),
                            'line_text': line,
                            'words': current_core_paper.get('words', [])
                        }
                        core_paper_marks.append(core_mark)

        return core_papers, all_marks, core_paper_marks, paper_words, registration_number

    def process_image(self, image_path: str) -> Optional[Tuple[List[Dict], List[Dict], List[Dict], List[List[str]], np.ndarray, Optional[str]]]:
        try:
            if not Path(image_path).is_file():
                raise FileNotFoundError(f"Image file not found: {image_path}")
            
            image = cv2.imread(image_path)
            if image is None:
                raise ValueError(f"Failed to load image: {image_path}")
        
            text = self.extract_text(image)
            core_papers, all_marks, core_paper_marks, paper_words, registration_number = self.parse_paper_details(text)
        
            annotated_image = image.copy()
            h_img, w_img = annotated_image.shape[:2]
        
            boxes = pytesseract.image_to_boxes(self.preprocess_image(image))
            for b in boxes.splitlines():
                b = b.split()
                if len(b) == 6:
                    x, y, w, h = map(int, b[1:5])
                    cv2.rectangle(
                        annotated_image,
                        (x, h_img - y),
                        (w, h_img - h),
                        (0, 255, 0),
                        1
                    )

            return core_papers, all_marks, core_paper_marks, paper_words, annotated_image, registration_number
               
        except Exception as e:
            self.logger.error(f"Error processing image: {str(e)}")
            return None

    def process_uploaded_file(self, file_path: str, mode: str = 'class wise') -> dict:
        try:
            result = self.process_image(file_path)
            if not result:
                return {"error": "Failed to process image"}
                
            core_papers, all_marks, core_paper_marks, paper_words, annotated_image, registration_number = result
            
            _, buffer = cv2.imencode('.jpg', annotated_image)
            img_base64 = base64.b64encode(buffer).decode('utf-8')
            
            num_core, num_total, ratio = self.calculate_paper_ratio(core_papers, '')
            core_paper_largest_sum = sum(self.find_largest_number([mark for mark in core_paper_marks if mark['paper_name'] == paper['paper_name']]) or 0 for paper in core_papers)
            average_core_marks = core_paper_largest_sum/num_core if num_core > 0 else 0
            
            if registration_number and average_core_marks:
                conn = sqlite3.connect('student_marks.db')
                cur = conn.cursor()
                table_name = 'student_marks_limited' if mode == 'student wise' else 'student_marks1'
                cur.execute(f'''
                    INSERT OR REPLACE INTO {table_name} (reg_number, marks)
                    VALUES (?, ?)
                ''', (registration_number, average_core_marks/10))
                conn.commit()
                conn.close()
            
            return {
                "registration_number": registration_number,
                "core_papers": [{"name": p["paper_name"], "words": p["words"]} for p in core_papers],
                "all_marks": [m["marks"] for m in all_marks],
                "core_paper_marks": [{"paper": m["paper_name"], "marks": m["marks"], 
                                    "is_total": m.get("is_total", False)} for m in core_paper_marks],
                "statistics": {
                    "num_core_papers": num_core,
                    "num_total_papers": num_total,
                    "ratio": ratio,
                    "average_core_marks": average_core_marks/10
                },
                "annotated_image": img_base64
            }
            
        except Exception as e:
            self.logger.error(f"Error processing uploaded file: {str(e)}")
            return {"error": str(e)}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_students/<mode>')
def get_students(mode):
    try:
        conn = sqlite3.connect('student_marks.db')
        cur = conn.cursor()
        table_name = 'student_marks_limited' if mode == 'student wise' else 'student_marks1'
        
        # Get sort parameters from request
        sort_field = request.args.get('sort', 'registration_number')
        sort_direction = request.args.get('direction', 'asc')
        
        # Map front-end field names to database column names
        field_mapping = {
            'registration_number': 'reg_number',
            'average_marks': 'marks'
        }
        
        db_field = field_mapping.get(sort_field, 'reg_number')
        order = 'DESC' if sort_direction == 'desc' else 'ASC'
        
        # Execute sorted query
        cur.execute(f'SELECT * FROM {table_name} ORDER BY {db_field} {order}')
        students = cur.fetchall()
        conn.close()
        
        student_list = [{
            'registration_number': student[0],
            'average_marks': student[1]
        } for student in students]
        
        # Calculate total average only for limited mode
        if mode == 'student wise' and student_list:
            total_average = sum(student['average_marks'] for student in student_list) / len(student_list)
        else:
            total_average = None
            
        return jsonify({
            'students': student_list,
            'total_average': total_average if mode == 'student wise' else None
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/get_rankings')
def get_rankings():
    try:
        conn = sqlite3.connect('student_marks.db')
        cur = conn.cursor()
        
        # Get sort parameters
        sort_field = request.args.get('sort', 'rank')
        sort_direction = request.args.get('direction', 'asc')
        
        # Base query to get students and marks
        base_query = '''
            SELECT reg_number, marks,
            RANK() OVER (ORDER BY marks DESC) as rank
            FROM student_marks1
        '''
        
        # Add sorting
        field_mapping = {
            'registration_number': 'reg_number',
            'average_marks': 'marks',
            'rank': 'rank'
        }
        
        db_field = field_mapping.get(sort_field, 'rank')
        order = 'DESC' if sort_direction == 'desc' else 'ASC'
        query = f'{base_query} ORDER BY {db_field} {order}'
        
        cur.execute(query)
        students = cur.fetchall()
        conn.close()
        
        rankings = [{
            'registration_number': reg_number,
            'average_marks': marks,
            'rank': rank
        } for reg_number, marks, rank in students]
            
        return jsonify({'rankings': rankings})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/clear_records/<mode>', methods=['POST'])
def clear_records(mode):
    try:
        # Reset the global variable for registration number
        if 'first_limited_reg_number' in globals():
            del globals()['first_limited_reg_number']
        
        conn = sqlite3.connect('student_marks.db')
        cur = conn.cursor()
        table_name = 'student_marks_limited' if mode == 'student wise' else 'student_marks1'
        cur.execute(f'DELETE FROM {table_name}')
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Records cleared successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/download_records/<mode>')
def download_records(mode):
    try:
        # Get student records
        conn = sqlite3.connect('student_marks.db')
        cur = conn.cursor()
        table_name = 'student_marks_limited' if mode == 'student wise' else 'student_marks1'
        cur.execute(f'SELECT reg_number, marks FROM {table_name} ORDER BY marks DESC')
        students = cur.fetchall()
        
        if not students:
            return jsonify({'error': 'No records found'})
        
        # Calculate total average for limited mode
        total_average = None
        if mode == 'student wise' and students:
            total_average = sum(student[1] for student in students) / len(students)
        
        # Create PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        
        # Add title
        title_style = TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 16),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 20),
        ])
        title = Table([[f'Student Records - {mode.title()} Mode']], colWidths=[7*inch])
        title.setStyle(title_style)
        elements.append(title)
        
        # Define table data without ranks
        data = [['Registration Number', 'Average Marks']]
        for reg_number, marks in students:
            data.append([reg_number, f"{marks:.2f}"])
            
        if mode == 'student wise' and total_average is not None:
            data.append(['Total Average', f"{total_average:.2f}"])
        
        # Create table with adjusted column widths
        table = Table(data, colWidths=[4*inch, 2.5*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f'student_records_{mode}.pdf',
            mimetype='application/pdf'
        )
        
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/download_records_with_ranks/<mode>')
def download_records_with_ranks(mode):
    try:
        # Get student records
        conn = sqlite3.connect('student_marks.db')
        cur = conn.cursor()
        table_name = 'student_marks_limited' if mode == 'student wise' else 'student_marks1'
        cur.execute(f'SELECT reg_number, marks FROM {table_name} ORDER BY marks DESC')
        students = cur.fetchall()
        
        if not students:
            return jsonify({'error': 'No records found'})
        
        # Calculate rankings
        rankings = []
        current_rank = 1
        prev_marks = None
        
        for i, (reg_number, marks) in enumerate(students):
            if prev_marks is not None and marks < prev_marks:
                current_rank = i + 1
            rankings.append({
                'reg_number': reg_number,
                'marks': marks,
                'rank': current_rank
            })
            prev_marks = marks
        
        # Calculate total average for limited mode
        total_average = None
        if mode == 'student wise' and students:
            total_average = sum(student[1] for student in students) / len(students)
        
        # Create PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        
        # Add title
        title_style = TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 16),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 20),
        ])
        title = Table([[f'Student Rankings - {mode.title()} Mode']], colWidths=[7*inch])
        title.setStyle(title_style)
        elements.append(title)
        
        # Define table data with ranks
        data = [['Rank', 'Registration Number', 'Average Marks']]
        
        for student in rankings:
            rank_suffix = 'th'
            if student['rank'] % 10 == 1 and student['rank'] != 11:
                rank_suffix = 'st'
            elif student['rank'] % 10 == 2 and student['rank'] != 12:
                rank_suffix = 'nd'
            elif student['rank'] % 10 == 3 and student['rank'] != 13:
                rank_suffix = 'rd'
            
            data.append([
                f"{student['rank']}{rank_suffix}",
                student['reg_number'],
                f"{student['marks']:.2f}"
            ])
            
        if mode == 'student wise' and total_average is not None:
            data.append(['', 'Total Average', f"{total_average:.2f}"])
        
        # Create table with adjusted column widths
        table = Table(data, colWidths=[1.5*inch, 3*inch, 2*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f'student_rankings_{mode}.pdf',
            mimetype='application/pdf'
        )
        
    except Exception as e:
        return jsonify({'error': str(e)})
    
@app.route('/upload/<mode>', methods=['POST'])
def upload_file(mode):
    # Add a global or class-level variable to store the first registration number
    global first_limited_reg_number
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part"})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"})
    
    if file and allowed_file(file.filename):
        # For limited mode, check the number of existing records
        if mode == 'student wise':
            conn = sqlite3.connect('student_marks.db')
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM student_marks_limited')
            count = cur.fetchone()[0]
            conn.close()
            
            if count >= MAX_IMAGES:
                return jsonify({"error": f"Maximum limit of {MAX_IMAGES} images reached"})
        
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        extractor = PaperMarkExtractor()
        result = extractor.process_uploaded_file(filepath, mode)
        
        os.remove(filepath)
        
        # Registration number validation for limited mode
        if mode == 'student wise':
            # Check if this is the first upload in limited mode
            if 'first_limited_reg_number' not in globals():
                global first_limited_reg_number
                first_limited_reg_number = result.get('registration_number')
            else:
                # Compare current registration number with the first one
                current_reg_number = result.get('registration_number')
                if current_reg_number != first_limited_reg_number:
                    # Remove the last uploaded record
                    conn = sqlite3.connect('student_marks.db')
                    cur = conn.cursor()
                    cur.execute('DELETE FROM student_marks_limited WHERE reg_number = ?', (current_reg_number,))
                    conn.commit()
                    conn.close()
                    
                    return jsonify({
                        "error": "Registration number does not match the first uploaded image. Please upload images for the same student.",
                        "first_reg_number": first_limited_reg_number,
                        "current_reg_number": current_reg_number
                    })
        
        return jsonify(result)
    
    return jsonify({"error": "Invalid file type"})
    
    return jsonify({"error": "Invalid file type"})

@app.route('/delete_students/<mode>', methods=['POST'])
def delete_students(mode):
    try:
        # Validate request
        if not request.is_json:
            return jsonify({'error': 'Request must be JSON'}), 400
        
        data = request.json
        registration_numbers = data.get('registration_numbers', [])
        
        if not registration_numbers:
            return jsonify({'error': 'No registration numbers provided'}), 400
        
        # Connect to database
        conn = sqlite3.connect('student_marks.db')
        cur = conn.cursor()
        
        # Determine table based on mode
        table_name = 'student_marks_limited' if mode == 'student wise' else 'student_marks1'
        
        # Delete from database
        placeholders = ','.join(['?'] * len(registration_numbers))
        cur.execute(f'DELETE FROM {table_name} WHERE reg_number IN ({placeholders})', registration_numbers)
        
        deleted_count = cur.rowcount
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True, 
            'deleted_count': deleted_count
        })
    
    except Exception as e:
        return jsonify({
            'error': f'Deletion failed: {str(e)}'
        }), 500
if __name__ == '__main__':
    init_db()
    app.run(debug=True)