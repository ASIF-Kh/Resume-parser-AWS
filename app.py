import os
import csv
import time
from io import StringIO
from collections import Counter
import boto3
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, Response, jsonify,
    redirect, url_for, flash
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)

from botocore.exceptions import ClientError

# --- Environment Variable Loading ---
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    raise FileNotFoundError("⚠️ .env file not found! Please create it in the project folder.")

# --- AWS & App Configuration ---
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_REGION = os.getenv('AWS_REGION')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

dynamodb = boto3.resource(
    'dynamodb',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)
users_table = dynamodb.Table('app_users')

# --- Flask App Initialization ---
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-very-secret-key-that-is-long-and-secure'

app = Flask(__name__)
app.config.from_object(Config)
app.config['ALLOWED_EXTENSIONS'] = {'pdf'}

# --- Login Manager Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Route for login page
login_manager.login_message = "You must be logged in to access the admin dashboard."
login_manager.login_message_category = "info"

# --- User Model ---
class User(UserMixin):
    def __init__(self, username, password):
        self.id = username
        self.password = password

    @staticmethod
    def get(username):
        try:
            response = users_table.get_item(Key={'username': username})
            if 'Item' in response:
                return User(username=response['Item']['username'], password=response['Item']['password'])
        except ClientError as e:
            print(f"Error getting user: {e}")
        return None

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_all_profiles():
    # ... (code unchanged) ...
    table = dynamodb.Table('profiles'); all_items = []
    response = table.scan()
    all_items.extend(response.get('Items', []))
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        all_items.extend(response.get('Items', []))
    return all_items

def get_error_count():
    # ... (code unchanged) ...
    try:
        table = dynamodb.Table("resume_errors"); response = table.scan(Select='COUNT')
        item_count = response['Count']
        while 'LastEvaluatedKey' in response:
            response = table.scan(Select='COUNT', ExclusiveStartKey=response['LastEvaluatedKey'])
            item_count += response['Count']
        return item_count
    except Exception: return 0

def calculate_stats(profiles):
    # ... (code unchanged) ...
    successful_parses, error_parses = len(profiles), get_error_count()
    total_uploads = error_parses + successful_parses
    success_rate = (successful_parses / total_uploads * 100) if total_uploads > 0 else 0
    return {"total_uploads": total_uploads, "successful_parses": successful_parses, "error_parses": error_parses, "success_rate": f"{success_rate:.2f}%"}

def filter_candidates(profiles, search_query):
    # ... (code unchanged) ...
    if not search_query: return profiles
    filtered_list, search_lower = [], search_query.lower()
    for profile in profiles:
        if profile in filtered_list: continue
        if search_lower in profile.get("experience", "").lower():
            filtered_list.append(profile); continue
        for skill_list in profile.get("skills", {}).values():
            if any(search_lower in skill.lower() for skill in skill_list):
                filtered_list.append(profile); break
    return filtered_list

def generate_csv(profiles):
    # ... (code unchanged) ...
    output = StringIO(); writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'Email', 'Contact', 'Education', 'Experience', 'Skills', 'Skills Score'])
    for profile in profiles:
        skills_str = ", ".join(skill for cat in profile.get('skills', {}).values() for skill in cat)
        writer.writerow([profile.get(k, 'N/A') for k in ['id', 'name', 'email', 'contact']] + [profile.get('education', 'N/A').replace('\n', ' ').strip(), profile.get('experience', 'N/A').replace('\n', ' ').strip(), skills_str, profile.get('skills_score', 0)])
    return output.getvalue()

def analyze_skills_distribution(profiles):
    # ... (code unchanged) ...
    all_skills = [s.lower().strip() for p in profiles for sl in p.get('skills', {}).values() for s in sl]
    if not all_skills: return {"labels": [], "data": []}
    skill_counts = Counter(all_skills); top_skills = skill_counts.most_common(15)
    if not top_skills: return {"labels": [], "data": []}
    labels, data = zip(*top_skills)
    return {"labels": list(labels), "data": list(data)}

# --- PUBLIC ROUTES ---

@app.route('/', methods=['GET', 'POST'])
def upload_page():
    """Public-facing resume upload page."""
    if request.method == 'POST':
        if 'resume' not in request.files:
            flash('No file part in the request.', 'danger'); return redirect(request.url)
        
        file = request.files['resume']
        if file.filename == '':
            flash('No file selected.', 'warning'); return redirect(request.url)

        if file and allowed_file(file.filename):
            try:
                timestamp = int(time.time())
                name, ext = os.path.splitext(file.filename)
                unique_filename = f"{name.replace(' ', '_')}_{timestamp}{ext}"
                s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY, region_name=AWS_REGION)
                s3.upload_fileobj(file, S3_BUCKET_NAME, unique_filename)
                flash(f"Upload Successful! Thank you for submitting your resume.", 'success')
            except Exception as e:
                flash(f"❌ Upload failed: {str(e)}", "danger")
            return redirect(url_for('upload_page'))
        else:
            flash('Only PDF files are allowed.', 'danger')
            return redirect(request.url)
    
    return render_template('upload.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login page."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        user = User.get(request.form.get('username'))
        if user and user.password == request.form.get('password').strip():
            login_user(user, remember=request.form.get('remember'))
            # Redirect to the dashboard after successful login
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid admin username or password.', 'danger')
    return render_template('login.html')

# --- ADMIN (PROTECTED) ROUTES ---

@app.route('/dashboard')
@login_required
def dashboard():
    all_profiles = get_all_profiles()
    stats = calculate_stats(all_profiles)
    search_query = request.args.get('search', '').strip()
    filtered_profiles = filter_candidates(all_profiles, search_query)
    return render_template('index.html', profiles=filtered_profiles, stats=stats, search_query=search_query)

@app.route('/visualize')
@login_required
def visualize():
    return render_template('visualize.html')

@app.route('/api/skills_data')
@login_required
def skills_data():
    all_profiles = get_all_profiles()
    skills_distribution = analyze_skills_distribution(all_profiles)
    return jsonify(skills_distribution)

@app.route('/download_csv', methods=['GET'])
@login_required
def download_csv():
    all_profiles = get_all_profiles(); search_query = request.args.get('search', '').strip()
    filtered_profiles = filter_candidates(all_profiles, search_query)
    csv_data = generate_csv(filtered_profiles)
    return Response(csv_data, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=candidate_report.csv"})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

# --- Main Execution ---
if __name__ == '__main__':
    app.run(debug=True, port=5001)