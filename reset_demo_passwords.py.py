from app import app, db, User

NEW_PASSWORD = "Demo@12345"

with app.app_context():
    users = User.query.filter(
        User.email.like("%@example.com")
    ).order_by(User.id.asc()).all()

    if not users:
        print("No demo users found.")
    else:
        print("Demo users found:")
        for u in users:
            print(f"- ID {u.id} | {u.email} | {u.full_name} | {u.role}")
            u.set_password(NEW_PASSWORD)

        db.session.commit()
        print(f"\nReset password for {len(users)} demo users to: {NEW_PASSWORD}")