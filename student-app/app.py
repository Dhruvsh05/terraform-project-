import os, uuid
import pymysql, boto3
from functools import wraps
from flask import Flask, render_template, request, redirect, session, flash, url_for
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me")

# ====== YAHAN APNI VALUES DAALNI HAIN (ya environment variables set karo) ======
DB_HOST = os.getenv("DB_HOST", "student-db.c7k0wg424ac2.ap-south-1.rds.amazonaws.com")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "student_db")
S3_BUCKET = os.getenv("S3_BUCKET", "student-docs-190444051295")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
# ================================================================================

s3 = boto3.client("s3", region_name=AWS_REGION)


def conn(with_db=True):
    return pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
                           database=DB_NAME if with_db else None,
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True)


def q(sql, args=(), one=False):
    c = conn()
    try:
        with c.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
    finally:
        c.close()


def init_db():
    c = conn(with_db=False)
    with c.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`")
    c.close()
    q("""CREATE TABLE IF NOT EXISTS admins(
        id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(50) UNIQUE, password VARCHAR(255))""")
    q("""CREATE TABLE IF NOT EXISTS courses(
        id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), duration VARCHAR(50))""")
    q("""CREATE TABLE IF NOT EXISTS students(
        id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), username VARCHAR(50) UNIQUE,
        password VARCHAR(255), course_id INT NULL)""")
    q("""CREATE TABLE IF NOT EXISTS documents(
        id INT AUTO_INCREMENT PRIMARY KEY, student_id INT, title VARCHAR(150),
        filename VARCHAR(255), s3_key VARCHAR(400))""")
    if not q("SELECT id FROM admins WHERE username='admin'", one=True):
        q("INSERT INTO admins(username,password) VALUES(%s,%s)",
          ("admin", generate_password_hash("admin123")))


def login_required(role):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if session.get("role") != role:
                return redirect(url_for(f"{role}_login"))
            return f(*a, **k)
        return w
    return deco


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ---------------- ADMIN ----------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u = q("SELECT * FROM admins WHERE username=%s", (request.form["username"],), one=True)
        if u and check_password_hash(u["password"], request.form["password"]):
            session.clear(); session["role"] = "admin"
            return redirect("/admin")
        flash("Galat username/password")
    return render_template("login.html", role="Admin")


@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    courses = q("SELECT * FROM courses")
    students = q("""SELECT s.*, c.name AS course FROM students s
                    LEFT JOIN courses c ON c.id=s.course_id""")
    return render_template("admin.html", courses=courses, students=students)


@app.route("/admin/course/add", methods=["POST"])
@login_required("admin")
def course_add():
    q("INSERT INTO courses(name,duration) VALUES(%s,%s)",
      (request.form["name"], request.form["duration"]))
    return redirect("/admin")


@app.route("/admin/course/delete/<int:cid>")
@login_required("admin")
def course_delete(cid):
    q("DELETE FROM courses WHERE id=%s", (cid,))
    return redirect("/admin")


@app.route("/admin/student/add", methods=["POST"])
@login_required("admin")
def student_add():
    try:
        q("INSERT INTO students(name,username,password,course_id) VALUES(%s,%s,%s,%s)",
          (request.form["name"], request.form["username"],
           generate_password_hash(request.form["password"]), request.form.get("course_id") or None))
    except pymysql.err.IntegrityError:
        flash("Ye username pehle se hai")
    return redirect("/admin")


@app.route("/admin/student/delete/<int:sid>")
@login_required("admin")
def student_delete(sid):
    for d in q("SELECT s3_key FROM documents WHERE student_id=%s", (sid,)):
        s3.delete_object(Bucket=S3_BUCKET, Key=d["s3_key"])
    q("DELETE FROM documents WHERE student_id=%s", (sid,))
    q("DELETE FROM students WHERE id=%s", (sid,))
    return redirect("/admin")


# ---------------- STUDENT ----------------
@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        u = q("SELECT * FROM students WHERE username=%s", (request.form["username"],), one=True)
        if u and check_password_hash(u["password"], request.form["password"]):
            session.clear(); session["role"] = "student"; session["sid"] = u["id"]; session["name"] = u["name"]
            return redirect("/student")
        flash("Galat username/password")
    return render_template("login.html", role="Student")


@app.route("/student")
@login_required("student")
def student_dashboard():
    docs = q("SELECT * FROM documents WHERE student_id=%s", (session["sid"],))
    return render_template("student.html", docs=docs)


def upload(f, sid):
    key = f"student_{sid}/{uuid.uuid4().hex}_{f.filename}"
    s3.upload_fileobj(f, S3_BUCKET, key)
    return key


@app.route("/student/doc/add", methods=["POST"])
@login_required("student")
def doc_add():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("File choose karo"); return redirect("/student")
    key = upload(f, session["sid"])
    q("INSERT INTO documents(student_id,title,filename,s3_key) VALUES(%s,%s,%s,%s)",
      (session["sid"], request.form["title"], f.filename, key))
    return redirect("/student")


@app.route("/student/doc/edit/<int:did>", methods=["POST"])
@login_required("student")
def doc_edit(did):
    d = q("SELECT * FROM documents WHERE id=%s AND student_id=%s", (did, session["sid"]), one=True)
    if not d:
        return redirect("/student")
    f = request.files.get("file")
    if f and f.filename:
        s3.delete_object(Bucket=S3_BUCKET, Key=d["s3_key"])
        key = upload(f, session["sid"])
        q("UPDATE documents SET title=%s, filename=%s, s3_key=%s WHERE id=%s",
          (request.form["title"], f.filename, key, did))
    else:
        q("UPDATE documents SET title=%s WHERE id=%s", (request.form["title"], did))
    return redirect("/student")


@app.route("/student/doc/delete/<int:did>")
@login_required("student")
def doc_delete(did):
    d = q("SELECT * FROM documents WHERE id=%s AND student_id=%s", (did, session["sid"]), one=True)
    if d:
        s3.delete_object(Bucket=S3_BUCKET, Key=d["s3_key"])
        q("DELETE FROM documents WHERE id=%s", (did,))
    return redirect("/student")


@app.route("/student/doc/view/<int:did>")
@login_required("student")
def doc_view(did):
    d = q("SELECT * FROM documents WHERE id=%s AND student_id=%s", (did, session["sid"]), one=True)
    if not d:
        return redirect("/student")
    url = s3.generate_presigned_url("get_object", Params={"Bucket": S3_BUCKET, "Key": d["s3_key"]}, ExpiresIn=300)
    return redirect(url)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
