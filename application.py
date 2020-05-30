from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine
from helper import login_required
from goodreads import get_reviews
from flask_session import Session
from flask import Flask, request, session, render_template, redirect, url_for, flash, jsonify
from flask_bootstrap import Bootstrap
import requests
import os
import csv
import hashlib
import random
import string
import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

app = Flask(__name__)

# Check for environment variable
if not os.getenv("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is not set")

# Configure session to use filesystem
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Set up database
engine = create_engine(os.getenv("DATABASE_URL"))
db = scoped_session(sessionmaker(bind=engine))


@app.route("/")
def home():
    books = db.execute(
        "SELECT isbn, title, author, year FROM books").fetchmany(100)
    return render_template('home.html', books=books)


@app.route('/sign-up', methods=["get"])
def get_sign_up():
    return render_template('sign_up.html')


def make_salt() -> str:
    letters = string.ascii_lowercase
    return ''.join(random.choice(letters) for i in range(16))


def hash_password(password: str, salt: str) -> str:
    m = hashlib.sha256()
    m.update(f"{password}{salt}".encode("utf-8"))
    return m.hexdigest()


@app.route('/post-sign-up', methods=["post"])
def post_sign_up():
    username = request.form.get("username")
    email = request.form.get("email")
    password = request.form.get("password")

    # Check for existing user with this username or email.
    existing = db.execute(
        "SELECT * FROM users WHERE username = :username OR email = :email",
        {"username": username, "email": email}).fetchone()
    if existing:
        return render_template('sign_up.html', message="Username or email already taken")

    # Hash the password with a salt for secure storage.
    salt = make_salt()
    hashed_password = hash_password(password, salt)
    password_hash_with_salt = f"{salt}:{hashed_password}"

    # Insert the new user into the DB.
    logging.info("Inserting new user")
    db.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (:username, :email, :password_hash) RETURNING id",
        {
            "username": username,
            "email": email,
            "password_hash": password_hash_with_salt
        })
    db.commit()

    user_data = db.execute(
        "SELECT * FROM users WHERE username = :username",
        {"username": username}).fetchone()

    logging.info("Insert new user data: {}", user_data)

    # Log the new user in and redirect to the profile page
    session["user_data"] = user_data
    return redirect(url_for("home"))


@app.route("/login", methods=["get"])
def get_login():
    return render_template("login.html")


@app.route("/post-login", methods=["post"])
def post_login():
    username = request.form.get("username")

    user_data = db.execute(
        "SELECT * FROM users WHERE username = :username",
        {'username': username}).fetchone()

    if not user_data:
        # User not found: send back to login form.
        return render_template("login.html", message="User not found")

    submitted_password = request.form.get("password")

    # Password hash is stored in the DB like this: "salt:hash"
    stored_password = str(user_data["password_hash"]).split(":")
    stored_salt = stored_password[0]
    stored_hash = stored_password[1]

    # Make a salted hash of the submitted password.
    submitted_password_hash = hash_password(submitted_password, stored_salt)

    if stored_hash != submitted_password_hash:
        # Wrong password: send back to login.
        return render_template("login.html", message="Wrong password")

    # Log the user in in Flask by setting the user data in session
    session["user_data"] = user_data

    return redirect(url_for("home"))


@app.route("/logout", methods=["post"])
def logout():
    session.clear()
    flash("You have been logged out!")
    return redirect(url_for("home"))

# Show all books
@app.route("/books")
@login_required
def books():
    allbooks = db.execute(
        "SELECT isbn, title, author, year FROM books").fetchmany(50)
    return render_template('books.html', books=allbooks)

# Search for a book
@app.route("/search", methods=["get"])
@login_required
def search():
    query = request.args.get("q")

    if not query:
        return render_template("error.html", message="Not found. Please adjust the input and try again.")

    query_title_case = query.title()
    query_result = db.execute(
        "SELECT * FROM books WHERE isbn LIKE :query or title LIKE :query or author LIKE :query limit 10", {
            "query": f"%{query_title_case}%"}
    )

    if query_result == 0:
        return render_template("error.html", message="No book found, please adjust your input and try again")

    books = query_result.fetchall()

    return render_template('result.html', books=books)


@app.route("/book/<isbn>", methods=["get", "post"])
def book(isbn):
    user_id = session["user_data"]["id"]
    session["reviews"] = []
    book = db.execute(
        "SELECT * FROM books WHERE isbn = :isbn LIMIT 1", {"isbn": isbn}
    ).fetchone()

    review_with_users = db.execute(
        "SELECT reviews.*, users.* FROM reviews INNER JOIN users ON users.id = reviews.user_id WHERE reviews.book_id = :book_id",
        {"book_id": book["id"]}).fetchall()

    goodreads_counts = get_reviews(isbn)

    return render_template(
        "book.html",
        book=book,
        review_with_users=review_with_users,
        goodreads_counts=goodreads_counts
    )


@app.route("/book/<isbn>/review", methods=["post"])
@login_required
def post_review(isbn: str):
    isbn = str(isbn).strip()
    review = request.form.get("review")
    # query book
    book = db.execute("SELECT * from books WHERE isbn = :isbn LIMIT 1", {"isbn": isbn}
                      ).fetchone()
    if book is None:
        return "Not found"

    db.execute(
        "INSERT INTO reviews (comment, book_id, user_id) VALUES (:comment, :book_id, :user_id)",
        {"comment": review, "book_id": book.id, "user_id": session["user_data"]["id"]})
    db.commit()

    return redirect(url_for('book', isbn=str(book.isbn).strip()))


if __name__ == "__main__":
    main()
