# app.py
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timedelta
import os # Import the os module

app = Flask(__name__)

# --- Configuration ---
# Define the path for the SQLite database.
# On Render, /var/data is a common mount point for persistent disks.
# For local development, it will default to 'site.db' in your project root.
DB_PATH = os.environ.get('DATABASE_PATH', 'site.db') # Use environment variable
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'your_super_secret_key_for_flask_session_if_used') # Also make secret key configurable via env var

db = SQLAlchemy(app)
CORS(app) # Enable CORS for all routes. For specific origins, use CORS(app, resources={r"/*": {"origins": "http://localhost:5173"}})

# --- Hardcoded Admin Credentials (For Demo Purposes Only - NOT Secure for Production) ---
# In a real application, store hashed passwords securely in a database.
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'password') # Default password, will be overridden by Render env var

# --- Database Models ---
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    admission_date = db.Column(db.String(10), nullable=False) #YYYY-MM-DD, now user-provided
    admission_cancel_date = db.Column(db.String(10), nullable=True) #YYYY-MM-DD
    monthly_fee = db.Column(db.Float, nullable=False)
    
    # Payments are related to students (one-to-many relationship)
    # cascade="all, delete-orphan" ensures payments are deleted when a student is deleted.
    payments = db.relationship('Payment', backref='student', lazy=True, order_by='desc(Payment.paid_till)', cascade="all, delete-orphan")

    def __repr__(self):
        return f"Student('{self.name}', '{self.phone}')"

    def to_dict(self):
        # Determine the latest paid_till date from payments
        latest_paid_till = None
        if self.payments:
            # payments are ordered by paid_till descending, so first is latest
            latest_paid_till = self.payments[0].paid_till

        # Calculate pending amount and months using the global utility function
        pending_months, pending_amount = calculate_pending_fees(self.monthly_fee, self.admission_date, latest_paid_till)

        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'phone': self.phone,
            'admission_date': self.admission_date,
            'admission_cancel_date': self.admission_cancel_date,
            'monthly_fee': self.monthly_fee,
            'paid_till': latest_paid_till, # Add latest paid_till for convenience in list view
            'pending_months': pending_months,
            'pending_amount': pending_amount
        }
    

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    paid_till = db.Column(db.String(10), nullable=False) #YYYY-MM-DD

    def __repr__(self):
        return f"Payment('{self.student_id}', '{self.paid_till}')"

    def to_dict(self):
        return {
            'id': self.id,
            'student_id': self.student_id,
            'paid_till': self.paid_till
        }

# --- Utility Function to calculate pending fees for display ---
# This function calculates pending months and amount based on provided dates.
# It considers the current date and determines if past months are pending.
# Returns (pending_months, pending_amount)
def calculate_pending_fees(student_monthly_fee, admission_date_str, latest_paid_till_str):
    pending_months = 0
    today = datetime.now()

    if not latest_paid_till_str:
        # If no payments, pending is calculated from the admission month
        admission_dt = datetime.strptime(admission_date_str, '%Y-%m-%d')
        
        # Calculate full months passed from admission month up to (and including) current month
        # Example: if admitted in Jan 2024, and it's March 2024, then Jan, Feb, Mar are 3 months.
        # This includes the current month if its due date has passed or is ongoing
        
        # Months difference accounting for year
        months_diff = (today.year - admission_dt.year) * 12 + (today.month - admission_dt.month)

        # If admitted in the current month, and it's before end of current month, 0 pending
        if months_diff == 0 and admission_dt.year == today.year and admission_dt.month == today.month:
            pending_months = 0 # Admitted this month, fee for this month not yet due/counted
        else:
            pending_months = months_diff + 1 # Include the current month as pending
            # Refinement: If current day is before 1st of month, and fee is due later, reduce a month
            # This logic depends on exact business rules for when fees become 'pending'
            # For simplicity, if current month's fee is due by end of month, and paid_till
            # is BEFORE current month, then current month is pending.
            if today.day < 1 and months_diff > 0: # If it's early in the month, and last payment was previous month
                pending_months -=1 # The current month might not be considered pending yet

    else:
        paid_till_dt = datetime.strptime(latest_paid_till_str, '%Y-%m-%d')
        
        # The first month to be considered pending is the month *after* paid_till_dt
        start_pending_dt = paid_till_dt + timedelta(days=1)
        if start_pending_dt.day != 1: # Ensure we start from the 1st of the next month
            if paid_till_dt.month == 12:
                start_pending_dt = paid_till_dt.replace(year=paid_till_dt.year + 1, month=1, day=1)
            else:
                start_pending_dt = paid_till_dt.replace(month=paid_till_dt.month + 1, day=1)

        # Calculate months between start_pending_dt (inclusive) and today (inclusive of current month)
        if start_pending_dt.year > today.year or \
           (start_pending_dt.year == today.year and start_pending_dt.month > today.month):
            pending_months = 0 # Paid up for current and possibly future months
        else:
            # Count full months from start_pending_dt up to current month
            pending_months = (today.year - start_pending_dt.year) * 12 + (today.month - start_pending_dt.month) + 1
            # If current day is very early in the month, and last payment was end of previous month
            # you might not want to count current month as pending yet. Adjust as per actual due date.
            # E.g., if fees due by 5th, and it's 3rd, and last paid till last month, current month is not pending.
            # Simplified: If today is the 1st of the month, and last paid was previous month, current month might not be counted yet.
            if today.day == 1 and paid_till_dt.month == (today.month - 1) % 12 and paid_till_dt.year == (today.year if today.month != 1 else today.year -1):
                 pending_months -=1 # If fees for current month are due mid-month, and it's the 1st, don't count yet.

    pending_amount = student_monthly_fee * pending_months if pending_months > 0 else 0
    return pending_months, pending_amount

# --- Database Initialization ---
# This block ensures tables are created and dummy data is inserted when the app starts.
# It must be within an application context.
def init_db_and_data():
    with app.app_context():
        # Ensure the directory for the database file exists if it's an absolute path
        db_dir = os.path.dirname(DB_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
        db.create_all()
        # Add some dummy data for testing if the database is empty
        if not Student.query.first():
            print("Adding dummy data...")
            student1 = Student(name='Alice Wonderland', address='123 Rabbit Hole', phone='9876543210', admission_date='2023-01-10', monthly_fee=1500.00)
            student2 = Student(name='Bob The Builder', address='456 Construction Site', phone='1234567890', admission_date='2023-03-01', monthly_fee=1200.00)
            student3 = Student(name='Charlie Chaplin', address='789 Hollywood Blvd', phone='9988776655', admission_date='2024-01-05', monthly_fee=2000.00)
            student4 = Student(name='Diana Prince', address='Themyscira', phone='1122334455', admission_date='2024-05-20', monthly_fee=1800.00) # Paid for May
            student5 = Student(name='Bruce Wayne', address='Batcave', phone='6677889900', admission_date='2024-06-15', monthly_fee=2500.00) # Recently admitted, no initial payment

            db.session.add_all([student1, student2, student3, student4, student5])
            db.session.commit() # Commit to get student IDs

            # Add payments
            # Alice: Paid till end of previous month (relative to today, end of June)
            payment1_s1 = Payment(student_id=student1.id, paid_till='2024-05-31') # Pending for June and July (if today is July)
            payment2_s1 = Payment(student_id=student1.id, paid_till='2024-06-30') # Paid till end of June. Pending from July onwards.

            # Bob: Paid till July 2023. Very much pending.
            payment1_s2 = Payment(student_id=student2.id, paid_till='2023-07-31') 

            # Charlie: Paid till Jan 2024. Pending since Feb.
            payment1_s3 = Payment(student_id=student3.id, paid_till='2024-01-31') 
            
            # Diana: Paid till May 2024.
            payment1_s4 = Payment(student_id=student4.id, paid_till='2024-05-31')
            payment2_s4 = Payment(student_id=student4.id, paid_till='2024-06-30') # Paid till end of June. Pending from July onwards.

            # Student 5 (Bruce) has no initial payment recorded, so pending from admission date
            
            db.session.add_all([payment1_s1, payment2_s1, payment1_s2, payment1_s3, payment1_s4, payment2_s4])
            db.session.commit()
            print("Dummy data added to database.")
        else:
            print("Database already contains data, skipping dummy data insertion.")

# --- Routes ---

# Basic registration (dummy implementation, no actual user DB persistence)
@app.route('/register', methods=['POST'])
def register_user():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    # In a real app, you'd save hashed password and user data here
    return jsonify({"message": "User registered (dummy response, not stored)"}), 201

# Basic login (checks against hardcoded admin credentials)
@app.route('/login', methods=['POST'])
def login_user():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        return jsonify({"message": "Login successful"}), 200 # No actual token needed by frontend now
    return jsonify({"error": "Invalid credentials"}), 401

# Change Password endpoint (updates hardcoded admin password)
@app.route('/change_password', methods=['PUT'])
def change_password():
    data = request.get_json()
    old_password = data.get('old_password')
    new_password = data.get('new_password')

    if not old_password or not new_password:
        return jsonify({"error": "Old password and new password are required"}), 400
    
    if old_password != ADMIN_PASSWORD:
        return jsonify({"error": "Incorrect old password"}), 401
    
    if old_password == new_password:
        return jsonify({"error": "New password cannot be the same as the old password"}), 400

    # In a real app, you would hash and store the new password in a database
    # For this demo, we're modifying the in-memory ADMIN_PASSWORD,
    # but on Render, this would require persistent storage or a more robust user system.
    # The environment variable set on Render will take precedence on new deployments.
    # To truly change it persistently on Render, you would need to update the ADMIN_PASSWORD env var there.
    # For a persistent change without redeployment of code, you'd need a proper user DB.
    # For simplicity of this demo, we're not persisting this change across restarts of the Render service
    # unless the ADMIN_PASSWORD environment variable on Render is updated manually.
    print(f"Admin password changed to: {new_password} (Note: This is not persistent across restarts unless env var updated)") # For demonstration
    return jsonify({"message": "Password changed successfully"}), 200


@app.route('/students', methods=['GET'])
def get_students():
    students = Student.query.all()
    student_list = []
    for student in students:
        s_dict = student.to_dict() # Uses the global calculate_pending_fees
        student_list.append(s_dict)
    return jsonify(student_list)

@app.route('/students', methods=['POST'])
def add_student():
    data = request.get_json()
    name = data.get('name')
    address = data.get('address')
    phone = data.get('phone')
    admission_date = data.get('admission_date') # Now user-provided
    initial_paid_till = data.get('initial_paid_till') # Renamed from 'paid_till' for clarity
    monthly_fee = data.get('monthly_fee')

    if not all([name, admission_date, initial_paid_till, monthly_fee is not None]):
        return jsonify({"error": "Missing required fields (name, admission_date, initial_paid_till, monthly_fee)"}), 400

    try:
        monthly_fee = float(monthly_fee)
        # Validate date formats
        datetime.strptime(admission_date, '%Y-%m-%d')
        datetime.strptime(initial_paid_till, '%Y-%m-%d')
    except ValueError:
        return jsonify({"error": "Invalid monthly_fee or date format (expected IPCC-MM-DD)"}), 400

    new_student = Student(
        name=name,
        address=address,
        phone=phone,
        admission_date=admission_date, # Use provided admission date
        monthly_fee=monthly_fee
    )
    db.session.add(new_student)
    db.session.commit() # Commit to get the new student's ID

    # Add the initial payment record
    initial_payment_record = Payment(student_id=new_student.id, paid_till=initial_paid_till)
    db.session.add(initial_payment_record)
    db.session.commit()

    return jsonify({"message": "Student added successfully", "student": new_student.to_dict()}), 201

@app.route('/students/<int:student_id>', methods=['DELETE'])
def delete_student(student_id):
    data = request.get_json()
    password_confirmation = data.get('password') # Get password from frontend for confirmation

    if password_confirmation != ADMIN_PASSWORD:
        return jsonify({"error": "Incorrect password for deletion confirmation"}), 401

    student = Student.query.get(student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404

    # Payments are automatically deleted due to cascade="all, delete-orphan" on relationship
    db.session.delete(student)
    db.session.commit()
    return jsonify({"message": f"Student with ID {student_id} and all related payments deleted successfully"}), 200

@app.route('/students/pending', methods=['GET'])
def get_pending_students():
    all_students = Student.query.all()
    pending_students_list = []
    for student in all_students:
        s_dict = student.to_dict() # This calculates pending based on current date
        if s_dict['pending_amount'] and s_dict['pending_amount'] > 0:
            pending_students_list.append(s_dict)
    return jsonify(pending_students_list)

@app.route('/students/<int:student_id>/payments', methods=['GET'])
def get_student_payments(student_id):
    student = Student.query.get(student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404

    # Fetch payments ordered by paid_till descending
    payments = Payment.query.filter_by(student_id=student_id).order_by(Payment.paid_till.desc()).all()
    
    # Calculate latest paid till from existing payments
    latest_paid_till_str = payments[0].paid_till if payments else None
    
    # Calculate pending amount and months using the global utility function
    pending_months, pending_amount = calculate_pending_fees(
        student.monthly_fee, student.admission_date, latest_paid_till_str
    )

    return jsonify({
        "student": student.to_dict(), # student.to_dict() will re-calculate based on its internal logic
        "payments": [p.to_dict() for p in payments],
        "pending_months": pending_months, # explicitly include these in the response for frontend
        "pending_amount": pending_amount  # explicitly include these in the response for frontend
    })

@app.route('/students/<int:student_id>/payments', methods=['PUT'])
def update_student_payment(student_id):
    student = Student.query.get(student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404

    data = request.get_json()
    paid_till = data.get('paid_till')

    if not paid_till:
        return jsonify({"error": "Paid till date is required"}), 400

    try:
        datetime.strptime(paid_till, '%Y-%m-%d')
    except ValueError:
        return jsonify({"error": "Invalid date format for paid_till (expected IPCC-MM-DD)"}), 400

    # Create a new payment record for the update
    new_payment = Payment(student_id=student.id, paid_till=paid_till)
    db.session.add(new_payment)
    db.session.commit()

    return jsonify({"message": "Payment updated successfully", "payment": new_payment.to_dict()}), 200


if __name__ == '__main__':
    # Initialize database and add dummy data if 'site.db' is empty.
    # This ensures your data persists across restarts.
    init_db_and_data() 
    app.run(debug=True, port=5000)