"""
Option A patch for TutorsOnline.pk

This is a drop-in helper/patch file containing the safest Batch 1 changes:
- /register becomes choice page
- /register/student and /register/tutor
- /complete-google-signup/student and /complete-google-signup/tutor
- manual credit purchase v2 flow
- dashboard notifications helper
- tutor profile / featured tutor click redirect behavior
- privacy route
- subject list expansion

You should merge these functions and route blocks into your current app.py carefully.
"""

# 1) Add these imports in app.py if not already present:
# from urllib.parse import quote
# from flask import abort

# 2) Extend SUBJECT_OPTIONS in app.py with:
EXTRA_SUBJECT_OPTIONS = [
    ("quran", "Quran"),
    ("computer_course", "Computer Course"),
    ("ai_courses", "AI Courses"),
    ("content_creation", "Content Creation"),
]

# 3) Add these helper functions near your other helpers:

def get_multi_values(form, field_name: str):
    values = []
    getter = getattr(form, "getlist", None)
    if getter:
        values.extend([v.strip() for v in getter(field_name) if v and v.strip()])
    single = form.get(field_name, "").strip()
    if single and single not in values:
        values.append(single)
    return values

def normalize_subjects(form, role="tutor"):
    if role == "student":
        selected = get_multi_values(form, "student_subject_needed")
        manual = [form.get(f"student_other_subject_{i}", "").strip() for i in range(1, 4)]
    else:
        selected = get_multi_values(form, "main_subject")
        manual = [form.get(f"additional_subject_{i}", "").strip() for i in range(1, 4)]

    merged = []
    for item in selected + manual:
        if item and item not in merged:
            merged.append(item)
    return merged

def normalize_levels(form):
    levels = get_multi_values(form, "class_levels")
    if not levels:
        single = pick_with_other(form, "class_levels")
        if single:
            levels = [single]
    unique = []
    for item in levels:
        if item and item not in unique:
            unique.append(item)
    return unique

def qualification_allowed_levels(qualification: str):
    qualification = (qualification or "").strip().lower()
    if qualification == "intermediate":
        return {"grade_1_5", "grade_6_8", "matric"}
    if qualification == "bachelors":
        return {"grade_1_5", "grade_6_8", "matric", "intermediate"}
    if qualification in {"masters", "mphil", "phd"}:
        return {"grade_1_5", "grade_6_8", "matric", "intermediate", "o_level", "a_level", "university"}
    return {"grade_1_5", "grade_6_8", "matric", "intermediate"}

def validate_option_a_tutor_form(form):
    missing = []
    for field, label in [
        ("full_name", "full name"),
        ("public_name", "display name"),
        ("email", "email"),
        ("password", "password"),
        ("mobile_number", "mobile number"),
        ("cnic_number", "CNIC number"),
        ("qualification", "qualification"),
        ("degree_title", "highest degree title"),
        ("degree_major", "degree major"),
        ("degree_institution", "degree institution"),
        ("degree_year", "degree year"),
        ("degree_grade", "degree grade"),
        ("inter_program", "intermediate program"),
        ("inter_institution", "intermediate institution"),
        ("inter_year", "intermediate year"),
        ("inter_grade", "intermediate grade"),
        ("matric_program", "matric program"),
        ("matric_institution", "matric institution"),
        ("matric_year", "matric year"),
        ("matric_grade", "matric grade"),
        ("experience_years", "experience"),
        ("hourly_rate", "hourly rate"),
        ("bio", "teaching profile"),
        ("demo_video_url", "demo video URL"),
    ]:
        if not form.get(field, "").strip():
            missing.append(label)

    subjects = normalize_subjects(form, "tutor")
    levels = normalize_levels(form)

    if not subjects:
        missing.append("subjects")
    if not levels:
        missing.append("levels")

    if form.get("demo_length_confirmed", "").strip() != "yes":
        missing.append("demo length confirmation")

    if form.get("accept_privacy", "").strip() != "yes":
        missing.append("privacy acceptance")

    qualification = pick_with_other(form, "qualification")
    allowed = qualification_allowed_levels(qualification)
    if levels and any(level not in allowed for level in levels):
        return "Selected teaching level is above the tutor's qualification allowance."

    if len(subjects) > 5:
        return "Please select up to 5 subjects total."

    if missing:
        return "Please complete: " + ", ".join(missing) + "."
    return None

def validate_option_a_student_form(form):
    missing = []
    for field, label in [
        ("full_name", "full name"),
        ("public_name", "display name"),
        ("email", "email"),
        ("password", "password"),
        ("student_level", "level"),
    ]:
        if not form.get(field, "").strip():
            missing.append(label)

    if not normalize_subjects(form, "student"):
        missing.append("subjects you want to study")
    if form.get("accept_privacy", "").strip() != "yes":
        missing.append("privacy acceptance")
    if missing:
        return "Please complete: " + ", ".join(missing) + "."
    return None

def build_user_from_option_a_form(form, files, google_email=None, google_name=None):
    role = form.get("role", "").strip().lower()
    email = (google_email or form.get("email", "")).strip().lower()
    full_name = form.get("full_name", google_name or "").strip() or (google_name or "")
    public_name = form.get("public_name", full_name).strip() or full_name

    gender = pick_with_other(form, "gender")
    city = pick_with_other(form, "city")

    subjects = []
    class_levels = []
    main_subject = ""
    additional_subjects = ""
    qualification = ""
    bio = ""
    experience_years = 0
    hourly_rate = 0
    student_level = ""
    student_subject_needed = ""
    preferred_tutor_gender = ""
    learning_mode = "online"
    teaching_mode = "online"

    if role == "student":
        student_level = pick_with_other(form, "student_level")
        student_subjects = normalize_subjects(form, "student")
        student_subject_needed = ", ".join(student_subjects)
        subjects = student_subjects
        class_levels = [student_level] if student_level else []
        preferred_tutor_gender = form.get("preferred_tutor_gender", "").strip()
        bio = "Student account"
    else:
        qualification = pick_with_other(form, "qualification")
        tutor_subjects = normalize_subjects(form, "tutor")
        levels = normalize_levels(form)
        subjects = tutor_subjects
        class_levels = levels
        main_subject = ", ".join(tutor_subjects[:3])
        additional_subjects = ", ".join(tutor_subjects[3:])
        experience_years = int(form.get("experience_years") or 0)
        hourly_rate = int(form.get("hourly_rate") or 0)
        bio = form.get("bio", "").strip()

    user = User(
        email=email,
        role=role,
        full_name=full_name,
        public_name=public_name,
        qualification=qualification,
        subjects=", ".join(subjects),
        class_levels=", ".join(class_levels),
        experience_years=experience_years,
        bio=bio,
        modest_profile=bool(form.get("modest_profile")),
        audio_only=bool(form.get("audio_only")),
        gender=gender,
        city=city,
        main_subject=main_subject,
        additional_subjects=additional_subjects,
        student_level=student_level,
        student_subject_needed=student_subject_needed,
        preferred_tutor_gender=preferred_tutor_gender,
        learning_mode=learning_mode,
        teaching_mode=teaching_mode,
        hourly_rate=hourly_rate,
        demo_video_url=form.get("demo_video_url", "").strip(),
        degree_title=form.get("degree_title", ""),
        degree_major=form.get("degree_major", ""),
        degree_institution=form.get("degree_institution", ""),
        degree_year=form.get("degree_year", ""),
        degree_grade=form.get("degree_grade", ""),
        bachelor_title=form.get("bachelor_title", ""),
        bachelor_institution=form.get("bachelor_institution", ""),
        bachelor_year=form.get("bachelor_year", ""),
        bachelor_grade=form.get("bachelor_grade", ""),
        inter_program=form.get("inter_program", ""),
        inter_institution=form.get("inter_institution", ""),
        inter_grade=form.get("inter_grade", ""),
        inter_year=form.get("inter_year", ""),
        matric_program=form.get("matric_program", ""),
        matric_institution=form.get("matric_institution", ""),
        matric_grade=form.get("matric_grade", ""),
        matric_year=form.get("matric_year", ""),
    )

    # Optional new DB columns if you add them
    if hasattr(user, "mobile_number"):
        user.mobile_number = form.get("mobile_number", "").strip()
    if hasattr(user, "cnic_number"):
        user.cnic_number = form.get("cnic_number", "").strip()

    user.tutor_category = classify_teacher(user.subjects, user.class_levels)
    user.set_password(form.get("password") or uuid4().hex)

    image_file = files.get("profile_image_file")
    if image_file and image_file.filename:
        filename = f"{uuid4().hex}_{secure_filename(image_file.filename)}"
        image_file.save(Path(app.config["UPLOAD_FOLDER"]) / filename)
        user.profile_image = filename

    degree_file = files.get("degree_file")
    if degree_file and degree_file.filename:
        degree_filename = f"degree_{uuid4().hex}_{secure_filename(degree_file.filename)}"
        degree_file.save(Path(app.config["UPLOAD_FOLDER"]) / degree_filename)
        user.degree_file = degree_filename

    if role == "tutor":
        user.is_verified_tutor = False
        user.profile_stage = "verification_incomplete" if tutor_missing_requirements_from_user(user) else "basic_complete"
        user.is_public_tutor = False

    return user

def dashboard_notifications_for(user):
    notes = []
    if user.role == "student":
        for notice in PaymentNotice.query.filter_by(student_id=user.id, status="pending").order_by(PaymentNotice.created_at.desc()).limit(5).all():
            notes.append(f"Your credit purchase notice for PKR {notice.amount_sent_pkr} is under review.")
    if user.role == "tutor":
        if user.profile_stage == "fee_pending":
            notes.append("You have a pending activation fee action. Please follow the dashboard instructions.")
        elif user.profile_stage == "under_review":
            notes.append("Your tutor profile is under admin review.")
        elif user.profile_stage == "rejected":
            notes.append("Your tutor profile was not approved yet. Please review the admin note.")
    return notes

# 4) Replace /register route with these:

@app.route("/register")
def register():
    return render_template("register_choice.html")

@app.route("/register/student", methods=["GET", "POST"])
def register_student():
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    selected_student_subjects = request.form.getlist("student_subject_needed") if request.method == "POST" else []
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
        else:
            error = validate_option_a_student_form(request.form)
            if error:
                flash(error, "danger")
            else:
                user = build_user_from_option_a_form(request.form, request.files)
                db.session.add(user)
                db.session.commit()
                send_signup_emails(user)
                flash("Registration completed successfully. Please log in.", "success")
                return redirect(url_for("login"))
    return render_template(
        "register_student.html",
        form_data=form_data,
        selected_student_subjects=selected_student_subjects,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS,
    )

@app.route("/register/tutor", methods=["GET", "POST"])
def register_tutor():
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    form_data.setdefault("active_step", request.args.get("step", "1"))
    selected_tutor_subjects = request.form.getlist("main_subject") if request.method == "POST" else []
    selected_tutor_levels = request.form.getlist("class_levels") if request.method == "POST" else []
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
        else:
            error = validate_option_a_tutor_form(request.form)
            if error:
                flash(error, "danger")
                form_data["active_step"] = request.form.get("active_step", "4")
            else:
                user = build_user_from_option_a_form(request.form, request.files)
                db.session.add(user)
                db.session.commit()
                send_signup_emails(user)
                flash("Tutor application submitted. If selected after review, you will be asked to pay PKR 500 to activate your profile.", "success")
                return redirect(url_for("login"))
    return render_template(
        "register_tutor.html",
        form_data=form_data,
        selected_tutor_subjects=selected_tutor_subjects,
        selected_tutor_levels=selected_tutor_levels,
        subject_options=SUBJECT_OPTIONS,
    )

# 5) Replace /complete-google-signup flow with:
@app.route("/complete-google-signup")
def complete_google_signup():
    google_signup = session.get("google_signup")
    if not google_signup:
        flash("Your Google signup session expired. Please try again.", "warning")
        return redirect(url_for("login"))
    return render_template("register_choice.html", google_signup=True)

@app.route("/complete-google-signup/student", methods=["GET", "POST"])
def complete_google_signup_student():
    google_signup = session.get("google_signup")
    if not google_signup:
        flash("Your Google signup session expired. Please try again.", "warning")
        return redirect(url_for("login"))

    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    selected_student_subjects = request.form.getlist("student_subject_needed") if request.method == "POST" else []
    email = google_signup["email"]
    fallback_name = google_signup["name"]

    if request.method == "POST":
        error = validate_option_a_student_form(request.form)
        if error:
            flash(error, "danger")
        else:
            user = build_user_from_option_a_form(request.form, request.files, google_email=email, google_name=fallback_name)
            db.session.add(user)
            db.session.commit()
            send_signup_emails(user)
            session.pop("google_signup", None)
            login_user(user)
            flash("Google signup completed successfully.", "success")
            return redirect(url_for("dashboard"))

    return render_template(
        "google_complete_student.html",
        form_data=form_data,
        selected_student_subjects=selected_student_subjects,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS,
        google_email=email,
        google_name=fallback_name,
    )

@app.route("/complete-google-signup/tutor", methods=["GET", "POST"])
def complete_google_signup_tutor():
    google_signup = session.get("google_signup")
    if not google_signup:
        flash("Your Google signup session expired. Please try again.", "warning")
        return redirect(url_for("login"))

    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    form_data.setdefault("active_step", request.args.get("step", "1"))
    selected_tutor_subjects = request.form.getlist("main_subject") if request.method == "POST" else []
    selected_tutor_levels = request.form.getlist("class_levels") if request.method == "POST" else []
    email = google_signup["email"]
    fallback_name = google_signup["name"]

    if request.method == "POST":
        error = validate_option_a_tutor_form(request.form)
        if error:
            flash(error, "danger")
            form_data["active_step"] = request.form.get("active_step", "4")
        else:
            user = build_user_from_option_a_form(request.form, request.files, google_email=email, google_name=fallback_name)
            db.session.add(user)
            db.session.commit()
            send_signup_emails(user)
            session.pop("google_signup", None)
            login_user(user)
            flash("Google signup completed. Tutor profile created and sent for review.", "success")
            return redirect(url_for("dashboard"))

    return render_template(
        "google_complete_tutor.html",
        form_data=form_data,
        selected_tutor_subjects=selected_tutor_subjects,
        selected_tutor_levels=selected_tutor_levels,
        subject_options=SUBJECT_OPTIONS,
        google_email=email,
        google_name=fallback_name,
    )

# 6) Make homepage/profile click behavior redirect back after login:
# in tutor cards, use url_for('tutor_profile', tutor_id=tutor.id)
# for book buttons from public pages:
# url_for('login', next=url_for('tutor_profile', tutor_id=tutor.id)) for guests

# 7) Upgrade /buy-credits route:

@app.route("/buy-credits", methods=["GET", "POST"])
@login_required
def buy_credits():
    if current_user.role != "student":
        flash("Only students can buy credits.", "danger")
        return redirect(url_for("dashboard"))

    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    credit_rate = app.config.get("CREDIT_RATE", 10)

    if request.method == "POST":
        selected = request.form.get("credits_requested", "").strip()
        if selected == "other":
            credits = int(request.form.get("credits_requested_other") or 0)
        else:
            credits = int(selected or 0)

        if credits < 10:
            flash("Minimum purchase is 10 credits.", "danger")
            return render_template("buy_credits_v2.html", form_data=form_data, credit_rate=credit_rate)

        if request.form.get("payment_confirmed", "").strip() != "yes":
            flash("Please confirm the transfer before submitting.", "danger")
            return render_template("buy_credits_v2.html", form_data=form_data, credit_rate=credit_rate)

        amount = credits * credit_rate
        screenshot = request.files.get("screenshot")
        if not screenshot or not screenshot.filename:
            flash("Please attach transfer screenshot.", "danger")
            return render_template("buy_credits_v2.html", form_data=form_data, credit_rate=credit_rate)

        filename = f"payment_{uuid4().hex}_{secure_filename(screenshot.filename)}"
        screenshot.save(Path(app.config["UPLOAD_FOLDER"]) / filename)

        notice = PaymentNotice(
            student_id=current_user.id,
            amount_sent_pkr=amount,
            claimed_credits=credits,
            sender_name=request.form.get("sender_name", ""),
            sender_account=request.form.get("sender_account", ""),
            transfer_method=request.form.get("transfer_method", "easypaisa"),
            screenshot_filename=filename,
            note=request.form.get("note", ""),
        )
        db.session.add(notice)
        db.session.commit()

        safe_send_email(
            "superadmin@tutorsonline.pk",
            "TutorsOnline.pk Credit Purchase Notice",
            f"Student: {current_user.full_name} ({current_user.email})\nRequested credits: {credits}\nAmount: PKR {amount}\nSender: {notice.sender_name}\nAccount: {notice.sender_account}\nMethod: {notice.transfer_method}\nScreenshot: {filename}",
        )

        flash("Payment notice submitted successfully. Admin will review it shortly.", "success")
        return redirect(url_for("dashboard"))

    return render_template("buy_credits_v2.html", form_data=form_data, credit_rate=credit_rate)

# 8) Add privacy route:
@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html")

# 9) In dashboard route, build notifications and fix bookings variable:
# bookings = upcoming
# notifications = dashboard_notifications_for(current_user)
# pass notifications=notifications and tutor_fee_instructions="Deposit PKR 500 and wait for admin confirmation."

# 10) In admin review route, when action == 'request_fee':
# user.profile_stage = 'fee_pending'
# user.admin_review_note = reason or user.admin_review_note
