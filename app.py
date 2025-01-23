from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
import os
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import openai
import random
from models import User, Topic, Word, Progress
from extensions import db  # Убедитесь, что extensions содержит экземпляр SQLAlchemy

# Загружаем переменные окружения
load_dotenv()

# Создаём приложение Flask
app = Flask(__name__)

# Конфигурация базы данных
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, 'instance', 'database.db')
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

if not DATABASE_URL:
    raise ValueError("DATABASE_URL не настроен.")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Убедимся, что папка instance существует
os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)
with open(DATABASE_PATH, 'w') as db_file:
    db_file.write('')
print("Test: database file created manually.")
# Инициализация базы данных
db.init_app(app)

# Проверка API ключа OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise ValueError("OPENAI_API_KEY не настроен.")

# Отладочные выводы
print("BASE_DIR:", BASE_DIR)
print("DATABASE_PATH:", DATABASE_PATH)
print("DATABASE_URL:", DATABASE_URL)

@app.route("/", methods=["GET", "POST"])
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])

        try:
            user = User(username=username, email=email, password=password)
            db.session.add(user)
            db.session.commit()
            flash("Registration successful! You can now log in.", "success")
            return redirect(url_for("login"))
        except Exception as e:
            db.session.rollback()
            flash(f"Registration failed: {e}", "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session["user_id"] = user.id
            session["username"] = user.username
            flash("Login successful! Welcome back!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials. Please try again.", "error")
    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    topics = Topic.query.all()
    user_id = session["user_id"]

    progress_list = [
        {
            "topic": topic.name,
            "score": progress.score if progress else 0,
            "learned_words": [
                {"word": word.word, "translation": word.translation}
                for word in
                Word.query.filter(Word.topic_id == topic.id, Word.word.in_(progress.learned_words.split(","))).all()
            ] if progress and progress.learned_words else []
        }
        for topic in topics
        if (progress := Progress.query.filter_by(user_id=user_id, topic_id=topic.id).first())
    ]

    return render_template("dashboard.html", topics=topics, progress_list=progress_list)


@app.route("/study/<int:topic_id>")
def study(topic_id):
    if "user_id" not in session:
        flash("Please log in to access this page.", "error")
        return redirect(url_for("login"))

    print(f"[DEBUG] User ID: {session.get('user_id')}")

    topic = db.session.get(Topic, topic_id)
    if not topic:
        flash("Topic not found.", "error")
        print(f"[DEBUG] Topic ID {topic_id} not found.")
        return redirect(url_for("dashboard"))

    progress = Progress.query.filter_by(user_id=session["user_id"], topic_id=topic_id).first()
    if not progress:
        print(f"[DEBUG] No progress found for topic ID {topic_id}, creating new progress.")
        progress = Progress(user_id=session["user_id"], topic_id=topic_id, score=0, learned_words="")
        db.session.add(progress)
        db.session.commit()

    learned_words = progress.learned_words.split(",") if progress.learned_words else []
    print(f"[DEBUG] Learned words for topic {topic_id}: {learned_words}")

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful Spanish language tutor."},
                {"role": "user",
                 "content": f"Generate 10 unique Spanish words related to the topic '{topic.name}' that are not already learned: {learned_words}. Each line should follow this format: word - translation - example sentence."}
            ]
        )

        generated_text = response['choices'][0]['message']['content']
        print(f"[DEBUG] Generated text from OpenAI: {generated_text}")

        new_words = process_generated_words(generated_text, topic_id, learned_words)

        progress.learned_words = ",".join(set(learned_words + [word.word for word in new_words]))
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        flash(f"Error generating words: {e}", "error")
        print(f"[ERROR] Exception during word generation: {e}")
        return redirect(url_for("dashboard"))

    words = Word.query.filter(Word.topic_id == topic_id, ~Word.word.in_(learned_words)).limit(10).all()

    if not words:
        flash("No new words available for this topic. You’ve learned everything!", "success")
        print(f"[DEBUG] No new words available for topic ID {topic_id}.")
        return redirect(url_for("dashboard"))

    return render_template("study.html", topic=topic, words=words)


@app.route("/test/<int:topic_id>", methods=["GET", "POST"])
def test(topic_id):
    topic = db.session.get(Topic, topic_id)
    if not topic:
        flash("Topic not found.", "error")
        return redirect(url_for("dashboard"))

    progress = Progress.query.filter_by(user_id=session["user_id"], topic_id=topic_id).first()
    learned_words = progress.learned_words.split(",") if progress and progress.learned_words else []

    words = Word.query.filter(Word.topic_id == topic_id, Word.word.in_(learned_words)).order_by(Word.id.desc()).limit(
        10).all()

    if not words:
        flash("No words available for the test. Please study first.", "error")
        return redirect(url_for("study", topic_id=topic_id))

    if request.method == "POST":
        correct_answers = sum(
            1 for word in words if request.form.get(f"word_{word.id}") == word.translation
        )
        total_questions = len(words)
        score = (correct_answers / total_questions) * 100

        if progress:
            progress.score = max(progress.score, score)
        else:
            db.session.add(Progress(user_id=session["user_id"], topic_id=topic_id, score=score, learned_words=""))

        db.session.commit()

        return render_template("result.html", score=score, topic=topic)

    test_data = generate_test_data(words, topic_id)
    return render_template("test.html", topic=topic, test_data=test_data)


def process_generated_words(generated_text, topic_id, learned_words):
    new_words = []
    for word_info in generated_text.strip().split('\n'):
        word, translation, context = parse_word_info(word_info)
        if word and word not in learned_words and not Word.query.filter_by(word=word).first():
            new_word = Word(word=word, translation=translation, context=context, topic_id=topic_id)
            db.session.add(new_word)
            new_words.append(new_word)

        if len(new_words) == 10:
            break
    return new_words


def parse_word_info(word_info):
    try:
        parts = [part.strip() for part in word_info.split('-')]
        if len(parts) < 3:
            raise ValueError(f"Invalid format: {word_info}")
        return parts[0], parts[1], parts[2]
    except Exception as e:
        print(f"Error parsing word info: {e}")
        return None, None, None


def generate_test_data(words, topic_id):
    test_data = []
    for word in words:
        options = [word.translation]
        incorrect_translations = Word.query.filter(Word.id != word.id, Word.topic_id == topic_id).limit(3).all()
        options += [incorrect.translation for incorrect in incorrect_translations]
        random.shuffle(options)
        test_data.append({"id": word.id, "word": word.word, "options": options})
    return test_data


if __name__ == "__main__":
    with app.app_context():
        print("Creating tables...")
        db.create_all()

        if not Topic.query.first():
            print("Adding default topics to the database...")
            default_topics = [
                "Food", "Travel", "Technology", "Health", "Education", "Sports",
                "Music", "Nature", "Family", "Clothing", "Animals", "Hobbies",
                "Jobs", "Shopping", "Transportation", "Entertainment",
                "Weather", "Culture", "History", "Science"
            ]
            for name in default_topics:
                db.session.add(Topic(name=name))
            db.session.commit()
            print("Default topics added.")

        print("Tables created successfully.")
    app.run(debug=True, host='0.0.0.0', port=5000)
