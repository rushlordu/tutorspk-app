import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

# Import from the user's app.py in project root
from app import app, db, User, Booking


DEMO_PASSWORD = os.getenv("DEMO_SEED_PASSWORD", "Demo@12345")
UPLOAD_FOLDER = Path(app.config.get("UPLOAD_FOLDER", "uploads"))
SEED_SUBDIR = "demo_seed"
SEED_FOLDER = UPLOAD_FOLDER / SEED_SUBDIR
SEED_FOLDER.mkdir(parents=True, exist_ok=True)

FIRST_NAMES = [
    "Ali", "Ahmed", "Usman", "Hamza", "Hassan", "Zain", "Bilal", "Saad", "Areeb", "Danish",
    "Fatima", "Ayesha", "Maryam", "Hira", "Iqra", "Maham", "Laiba", "Sana", "Noor", "Zoya",
]
LAST_NAMES = [
    "Khan", "Ahmed", "Raza", "Malik", "Qureshi", "Butt", "Siddiqui", "Sheikh", "Chaudhry", "Farooq",
]
CITIES = ["Islamabad", "Rawalpindi", "Lahore", "Karachi", "Peshawar", "Quetta", "Online Only"]
GENDERS = ["male", "female"]
SUBJECTS = [
    "mathematics", "physics", "chemistry", "biology", "english",
    "urdu", "computer_science", "ielts", "spoken_english", "arabic", "french"
]
LEVELS = [
    "grade_1_5", "grade_6_8", "matric", "intermediate", "o_level", "a_level", "university", "language_learning"
]
QUALIFICATIONS = ["bachelors", "masters", "mphil", "phd"]
INSTITUTIONS = [
    "NUST", "FAST", "COMSATS", "IIUI", "Punjab University", "University of Karachi",
    "Bahria University", "Air University", "UET Lahore", "QAU"
]
INTER_COLLEGES = [
    "Punjab College", "APS College", "Fazaia College", "FG College", "Roots College", "KIPS College"
]
SCHOOLS = [
    "APSACS", "Beaconhouse", "The City School", "Roots Millennium", "Fazaia School", "Froebels"
]
BIO_TEMPLATES = [
    "Experienced tutor focused on concept clarity, exam strategy, and steady confidence building.",
    "I teach in a structured and friendly way with regular practice, feedback, and revision support.",
    "My classes are interactive, result-oriented, and tailored for school, board, and entry-test learners.",
    "I help students strengthen fundamentals first, then move to problem solving and performance improvement.",
]
VIDEO_LINKS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=ysz5S6PUM-U",
    "https://youtu.be/aqz-KE-bpKQ",
    "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "https://vimeo.com/148751763",
    "https://vimeo.com/76979871",
]
LEARNING_MODES = ["online", "hybrid"]
TEACHING_MODES = ["online", "hybrid"]
PREFERRED_TUTOR_GENDER = ["no_preference", "male", "female"]
ADDITIONAL_QUAL_LEVELS = ["none", "bachelors", "masters", "mphil", "certificate"]


def slug(s: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in s).strip("_")


def make_avatar(filename: str, display_name: str):
    path = SEED_FOLDER / filename
    if path.exists():
        return f"{SEED_SUBDIR}/{filename}"

    size = 512
    bg = tuple(random.randint(80, 210) for _ in range(3))
    fg = (255, 255, 255)
    image = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(image)

    initials = "".join(part[0] for part in display_name.split()[:2]).upper() or "TP"
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 180)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), initials, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 10), initials, fill=fg, font=font)
    image.save(path, format="PNG")
    return f"{SEED_SUBDIR}/{filename}"


def pick_name(used_emails: set, prefix: str):
    while True:
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        full_name = f"{first} {last}"
        email = f"{slug(first)}.{slug(last)}.{prefix}{random.randint(100,999)}@example.com"
        if email not in used_emails:
            used_emails.add(email)
            return first, last, full_name, email


def set_common_user_fields(user, full_name, public_name, email, gender, city, profile_image):
    user.full_name = full_name
    user.public_name = public_name
    user.email = email
    user.gender = gender
    user.city = city
    user.profile_image = profile_image
    user.is_active_user = True
    user.set_password(DEMO_PASSWORD)


def fill_tutor_fields(user, i):
    qualification = random.choice(QUALIFICATIONS)
    main_subject = random.choice(SUBJECTS)
    second_subject = random.choice([s for s in SUBJECTS if s != main_subject])
    level1 = random.choice(LEVELS)
    level2 = random.choice([l for l in LEVELS if l != level1])

    user.role = "tutor"
    user.qualification = qualification
    user.subjects = f"{main_subject}, {second_subject}"
    user.main_subject = main_subject
    user.additional_subjects = second_subject
    user.class_levels = f"{level1}, {level2}"
    user.experience_years = random.randint(2, 11)
    user.bio = random.choice(BIO_TEMPLATES)
    user.teaching_mode = random.choice(TEACHING_MODES)
    user.learning_mode = "online"
    user.hourly_rate = random.choice([1200, 1500, 1800, 2000, 2500, 3000])
    user.demo_video_url = random.choice(VIDEO_LINKS)
    user.modest_profile = random.choice([True, False])
    user.audio_only = random.choice([False, False, True])

    highest_map = {
        "bachelors": ("Bachelor of Science", "bachelor_to_inter"),
        "masters": ("Master of Science", "masters_to_bachelors"),
        "mphil": ("MPhil", "mphil_to_masters"),
        "phd": ("PhD", random.choice(["phd_to_mphill", "phd_to_masters"]))
    }
    degree_title, prev_path = highest_map[qualification]
    user.degree_title = degree_title
    user.degree_major = main_subject.replace("_", " ").title()
    user.degree_institution = random.choice(INSTITUTIONS)
    user.degree_year = str(random.randint(2016, 2024))
    user.degree_grade = random.choice(["3.1/4.0", "3.3/4.0", "3.5/4.0", "A", "A-"])
    user.previous_path_choice = prev_path

    if qualification == "phd":
        if prev_path == "phd_to_mphill":
            user.mphil_title = "MPhil"
            user.mphil_major = user.degree_major
            user.mphil_institution = random.choice(INSTITUTIONS)
            user.mphil_year = str(random.randint(2013, 2021))
            user.mphil_grade = random.choice(["3.4/4.0", "A", "A-"])
            user.masters_title = "Master of Science"
            user.masters_major = user.degree_major
            user.masters_institution = random.choice(INSTITUTIONS)
            user.masters_year = str(random.randint(2011, 2019))
            user.masters_grade = random.choice(["3.3/4.0", "A", "A-"])
        else:
            user.masters_title = "Master of Science"
            user.masters_major = user.degree_major
            user.masters_institution = random.choice(INSTITUTIONS)
            user.masters_year = str(random.randint(2012, 2020))
            user.masters_grade = random.choice(["3.2/4.0", "A", "B+"])
    elif qualification == "mphil":
        user.masters_title = "Master of Science"
        user.masters_major = user.degree_major
        user.masters_institution = random.choice(INSTITUTIONS)
        user.masters_year = str(random.randint(2012, 2020))
        user.masters_grade = random.choice(["3.2/4.0", "A", "B+"])
    elif qualification == "masters":
        pass

    user.bachelor_title = random.choice(["Bachelor of Science", "Bachelor of Arts", "BS"])
    user.bachelor_major = random.choice([user.degree_major, "Education", "Computer Science", "English"])
    user.bachelor_institution = random.choice(INSTITUTIONS)
    user.bachelor_year = str(random.randint(2009, 2020))
    user.bachelor_grade = random.choice(["3.0/4.0", "3.2/4.0", "A", "B+"])
    user.bachelor_additional_note = random.choice(["", "Minor in Statistics", "Evening Program", "Honours Stream"])

    user.inter_program = random.choice(["FSc Pre-Engineering", "FSc Pre-Medical", "ICS", "FA"])
    user.inter_institution = random.choice(INTER_COLLEGES)
    user.inter_grade = random.choice(["A", "A-", "B+"])
    user.inter_year = str(random.randint(2007, 2018))

    user.matric_program = random.choice(["Science", "Computer Science", "Arts"])
    user.matric_institution = random.choice(SCHOOLS)
    user.matric_grade = random.choice(["A+", "A", "A-"])
    user.matric_year = str(int(user.inter_year) - 2)

    add_level = random.choice(ADDITIONAL_QUAL_LEVELS)
    if add_level != "none":
        user.additional_qualification_level = add_level
        user.additional_qualification_title = random.choice([
            "Diploma in Teaching", "Certificate in Spoken English", "Advanced IELTS Training", "Short Course in AI"
        ])
        user.additional_qualification_major = random.choice([
            "Education", "English", "Teaching Methods", "Data Analysis"
        ])
        user.additional_qualification_institution = random.choice(INSTITUTIONS)
        user.additional_qualification_year = str(random.randint(2018, 2025))
        user.additional_qualification_grade = random.choice(["A", "Pass", "Distinction"])
    else:
        user.additional_qualification_level = ""
        user.additional_qualification_title = ""
        user.additional_qualification_major = ""
        user.additional_qualification_institution = ""
        user.additional_qualification_year = ""
        user.additional_qualification_grade = ""

    user.degree_file = ""
    user.additional_qualification_file = ""
    user.profile_stage = "approved"
    user.is_verified_tutor = True
    user.is_public_tutor = True
    user.tutor_category = random.choice(["academic", "language", "exam_prep"])
    user.credits_balance = random.randint(0, 150)
    user.bonus_credits = random.choice([0, 50, 100])
    user.total_earnings_pkr = random.choice([25000, 40000, 55000, 80000, 120000])
    user.monthly_earnings_pkr = random.choice([8000, 12000, 18000, 25000])
    user.pending_payout_pkr = random.choice([0, 3000, 5000, 8000])
    user.sessions_completed = random.randint(3, 40)
    user.rating_avg = round(random.uniform(4.2, 5.0), 1)
    user.rating_count = random.randint(3, 25)
    user.approved_at = datetime.utcnow() - timedelta(days=random.randint(1, 120))


def fill_student_fields(user):
    user.role = "student"
    user.student_level = random.choice(LEVELS)
    user.student_subject_needed = random.choice(SUBJECTS)
    user.preferred_tutor_gender = random.choice(PREFERRED_TUTOR_GENDER)
    user.learning_mode = random.choice(LEARNING_MODES)
    user.credits_balance = random.choice([100, 200, 300, 500])
    user.profile_stage = "basic_complete"
    user.is_verified_tutor = False
    user.is_public_tutor = False
    user.bio = ""
    user.experience_years = 0
    user.hourly_rate = 0


def upsert_demo_user(email, role, full_name, public_name, gender, city, profile_image, index_num):
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, full_name=full_name, public_name=public_name, role=role)
        db.session.add(user)
    set_common_user_fields(user, full_name, public_name, email, gender, city, profile_image)
    if role == "tutor":
        fill_tutor_fields(user, index_num)
    else:
        fill_student_fields(user)
    return user


def seed_demo_users(tutor_count=10, student_count=10):
    used_emails = set(u.email for u in User.query.all())
    created_tutors = []
    created_students = []

    for i in range(1, tutor_count + 1):
        first, last, full_name, email = pick_name(used_emails, "tutor")
        public_name = f"{first} {last[0]}."
        profile_image = make_avatar(f"tutor_{i}_{slug(full_name)}.png", public_name)
        tutor = upsert_demo_user(
            email=email,
            role="tutor",
            full_name=full_name,
            public_name=public_name,
            gender=random.choice(GENDERS),
            city=random.choice(CITIES),
            profile_image=profile_image,
            index_num=i,
        )
        created_tutors.append(tutor)

    for i in range(1, student_count + 1):
        first, last, full_name, email = pick_name(used_emails, "student")
        public_name = f"{first} {last[0]}."
        profile_image = make_avatar(f"student_{i}_{slug(full_name)}.png", public_name)
        student = upsert_demo_user(
            email=email,
            role="student",
            full_name=full_name,
            public_name=public_name,
            gender=random.choice(GENDERS),
            city=random.choice(CITIES),
            profile_image=profile_image,
            index_num=i,
        )
        created_students.append(student)

    db.session.commit()

    # Create a few demo bookings so dashboards look alive
    tutor_ids = [t.id for t in created_tutors]
    student_ids = [s.id for s in created_students]
    existing_demo_bookings = Booking.query.filter(Booking.subject.like("Demo Seed %")).count()
    if existing_demo_bookings == 0 and tutor_ids and student_ids:
        for i in range(1, 9):
            booking = Booking(
                student_id=random.choice(student_ids),
                tutor_id=random.choice(tutor_ids),
                subject=f"Demo Seed {random.choice(SUBJECTS).replace('_', ' ').title()}",
                class_level=random.choice(LEVELS),
                scheduled_at=datetime.utcnow() + timedelta(days=random.randint(1, 20), hours=random.randint(1, 18)),
                duration_minutes=random.choice([45, 60, 90]),
                credits_cost=random.choice([80, 100, 120]),
                status=random.choice(["scheduled", "scheduled", "completed"]),
                room_code=uuid4().hex[:10],
            )
            db.session.add(booking)
        db.session.commit()

    print("\nDemo seeding complete")
    print(f"Password for all demo users: {DEMO_PASSWORD}")
    print("\nTutors:")
    for t in created_tutors:
        print(f"- {t.email} | {t.public_name} | {t.main_subject} | {t.city}")
    print("\nStudents:")
    for s in created_students:
        print(f"- {s.email} | {s.public_name} | {s.student_subject_needed} | {s.city}")


if __name__ == "__main__":
    with app.app_context():
        seed_demo_users(10, 10)
