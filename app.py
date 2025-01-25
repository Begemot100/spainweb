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
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_DIR, exist_ok=True)  # Убедитесь, что папка instance существует
DATABASE_PATH = os.path.join(INSTANCE_DIR, 'database.db')
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_default_secret_key")

# Проверка API ключа OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise ValueError("OPENAI_API_KEY не настроен.")

# Инициализация базы данных
db.init_app(app)

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

    # Прогресс по словам
    progress_list = [
        {
            "topic_id": topic.id,
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

    # Прогресс по грамматике
    grammar_progress = [
        {
            "lesson_id": progress.grammar_lesson_id,
            "lesson_title": "Ser vs Estar",  # Название урока можно динамически изменять
            "score": progress.score
        }
        for progress in Progress.query.filter(Progress.user_id == user_id, Progress.grammar_lesson_id.isnot(None)).all()
    ]

    return render_template("dashboard.html", topics=topics, progress_list=progress_list, grammar_progress=grammar_progress)
@app.route("/study/<int:topic_id>")
def study(topic_id):
    if "user_id" not in session:
        flash("Please log in to access this page.", "error")
        return redirect(url_for("login"))

    topic = db.session.get(Topic, topic_id)
    if not topic:
        flash("Topic not found.", "error")
        return redirect(url_for("dashboard"))

    progress = Progress.query.filter_by(user_id=session["user_id"], topic_id=topic_id).first()
    if not progress:
        progress = Progress(user_id=session["user_id"], topic_id=topic_id, score=0, learned_words="")
        db.session.add(progress)
        db.session.commit()

    learned_words = progress.learned_words.split(",") if progress.learned_words else []

    new_words = []
    while len(new_words) < 10:  # Повторяем до тех пор, пока не будет 10 уникальных слов
        try:
            # Генерация новых слов через OpenAI
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful language tutor."},
                    {"role": "user",
                     "content": f"Generate 10 unique Spanish words related to the topic '{topic.name}' with Russian translations. "
                                "Each line should follow this format: Russian translation (English word) - Spanish word - example sentence in Spanish. "
                                f"Do not include any words from this list: {learned_words}."}
                ]
            )

            generated_text = response['choices'][0]['message']['content']
            # Обработка сгенерированных слов
            words_from_response = process_generated_words(generated_text, topic_id, learned_words)

            # Добавляем новые слова к результату
            for word in words_from_response:
                if len(new_words) < 10:
                    new_words.append(word)
                else:
                    break

            if len(words_from_response) == 0:
                break  # Если больше нет новых слов, выходим из цикла

        except Exception as e:
            db.session.rollback()
            flash(f"Error generating words: {e}", "error")
            return redirect(url_for("dashboard"))

    # Обновление изученных слов
    learned_words += [word.word for word in new_words]
    progress.learned_words = ",".join(set(learned_words))
    db.session.commit()

    if not new_words:
        flash("No new words available for this topic. You’ve learned everything!", "success")
        return redirect(url_for("dashboard"))

    return render_template("study.html", topic=topic, words=new_words)

@app.route("/test/<int:topic_id>", methods=["GET", "POST"])
def test(topic_id):
    topic = Topic.query.get(topic_id)
    if not topic:
        flash("Topic not found.", "error")
        return redirect(url_for("dashboard"))

    progress = Progress.query.filter_by(user_id=session["user_id"], topic_id=topic_id).first()

    # Ensure progress exists
    if not progress or not progress.learned_words:
        flash("No words to test for this topic. Start studying first!", "error")
        return redirect(url_for("study", topic_id=topic_id))

    # Retrieve newly generated words for the test
    learned_words = progress.learned_words.split(",") if progress.learned_words else []

    # Limit to the 10 most recent learned words
    words = Word.query.filter(Word.topic_id == topic_id, Word.word.in_(learned_words))\
                      .order_by(Word.id.desc()).limit(10).all()

    if request.method == "POST":
        correct_answers = 0
        total_questions = len(words)

        # Evaluate the user's answers
        for word in words:
            user_answer = request.form.get(f"word_{word.id}")
            if user_answer == word.translation:
                correct_answers += 1

        score = (correct_answers / total_questions) * 100

        # Update the progress score
        if progress:
            progress.score = max(progress.score, score)
            db.session.commit()

        # Render results
        return render_template(
            "result.html",
            score=score,
            correct_answers=correct_answers,
            total_questions=total_questions,
            topic=topic
        )

    # Prepare test data
    test_data = []
    for word in words:
        options = [word.translation]

        # Generate incorrect options for the test
        incorrect_translations = Word.query.filter(Word.id != word.id, Word.topic_id == topic_id)\
                                           .limit(3).all()
        options += [incorrect.translation for incorrect in incorrect_translations]
        random.shuffle(options)

        test_data.append({
            "id": word.id,
            "word": word.word,
            "options": options
        })

    return render_template("test.html", topic=topic, test_data=test_data)

@app.route("/repeat_test/<int:topic_id>", methods=["GET", "POST"])
def repeat_test(topic_id):
    topic = Topic.query.get(topic_id)
    if not topic:
        flash("Topic not found.", "error")
        return redirect(url_for("dashboard"))

    progress = Progress.query.filter_by(user_id=session["user_id"], topic_id=topic_id).first()

    # Ensure progress exists and has learned words
    if not progress or not progress.learned_words:
        flash("No words available for repetition in this topic. Start studying first!", "error")
        return redirect(url_for("dashboard"))

    # Retrieve learned words
    learned_words = progress.learned_words.split(",") if progress.learned_words else []

    # Randomly select up to 10 words from learned words
    words = Word.query.filter(Word.topic_id == topic_id, Word.word.in_(learned_words)).all()
    random.shuffle(words)
    words = words[:10]  # Limit to 10 words

    if request.method == "POST":
        correct_answers = 0
        total_questions = len(words)

        # Evaluate the user's answers
        for word in words:
            user_answer = request.form.get(f"word_{word.id}")
            if user_answer == word.translation:
                correct_answers += 1

        score = (correct_answers / total_questions) * 100
        flash(f"Your score: {score:.2f}% ({correct_answers}/{total_questions} correct answers).", "info")

        return render_template(
            "result.html",
            score=score,
            correct_answers=correct_answers,
            total_questions=total_questions,
            topic=topic
        )

    # Prepare test data
    test_data = []
    for word in words:
        options = [word.translation]
        incorrect_translations = Word.query.filter(Word.id != word.id, Word.topic_id == topic_id)\
                                           .limit(3).all()
        options += [incorrect.translation for incorrect in incorrect_translations]
        random.shuffle(options)

        test_data.append({
            "id": word.id,
            "word": word.word,
            "options": options
        })

    return render_template("test.html", topic=topic, test_data=test_data)

def process_generated_words(generated_text, topic_id, learned_words):
    """
    Обрабатывает текст, сгенерированный OpenAI, добавляя только уникальные слова.
    """
    new_words = []
    for word_info in generated_text.strip().split('\n'):
        # Разбираем строку формата: "русский перевод (английское слово) - испанское слово - пример"
        word, translation, context = parse_word_info(word_info)

        # Проверяем, что слово не в изученных и не существует в базе данных
        if word and word not in learned_words and not Word.query.filter_by(word=word, topic_id=topic_id).first():
            new_word = Word(word=word, translation=translation, context=context, topic_id=topic_id)
            db.session.add(new_word)
            new_words.append(new_word)

    db.session.commit()
    return new_words
def parse_word_info(word_info):
    """
    Разбирает строку формата: 'Русский перевод (Английское слово) - Испанское слово - Пример предложения'
    """
    try:
        parts = [part.strip() for part in word_info.split('-')]
        if len(parts) < 3:
            raise ValueError(f"Invalid format: {word_info}")

        # Извлекаем русский перевод и английский оригинал из первой части
        translation_part = parts[0]
        if '(' in translation_part and ')' in translation_part:
            translation, original = translation_part.split('(')
            translation = translation.strip()
            original = original.strip(') ')
        else:
            raise ValueError(f"Invalid format for translation and original: {translation_part}")

        spanish_word = parts[1]
        context = parts[2]

        return f"{translation} ({original})", spanish_word, context
    except Exception as e:
        print(f"Error parsing word info: {e}")
        return None, None, None

@app.route("/grammar")
def grammar():
    lessons = [
        {"id": 1, "title": "Ser vs Estar", "description": "Урок о различиях между глаголами ser и estar."}
    ]
    return render_template("grammar.html", lessons=lessons)

@app.route("/grammar/<int:lesson_id>")
def grammar_lesson(lesson_id):
    # Словарь с данными всех уроков
    lessons = {
        1: {
            "id": 1,  # ID урока
            "title": "Глаголы Ser и Estar",
            "content": """
                Глаголы <strong>ser</strong> и <strong>estar</strong> переводятся как "быть", но используются в разных ситуациях.
                <ul>
                    <li><strong>Ser</strong>: используется для постоянных характеристик, профессий, времени, происхождения.</li>
                    <li><strong>Estar</strong>: используется для временных состояний, эмоций и местоположения.</li>
                </ul>
            """,
            "ser_conjugations": {
                "Presente": ["soy", "eres", "es", "somos", "sois", "son"],
                "Pretérito": ["fui", "fuiste", "fue", "fuimos", "fuisteis", "fueron"],
                "Futuro": ["seré", "serás", "será", "seremos", "seréis", "serán"]
            },
            "estar_conjugations": {
                "Presente": ["estoy", "estás", "está", "estamos", "estáis", "están"],
                "Pretérito": ["estuve", "estuviste", "estuvo", "estuvimos", "estuvisteis", "estuvieron"],
                "Futuro": ["estaré", "estarás", "estará", "estaremos", "estaréis", "estarán"]
            },
            "examples": [
                {"sentence": "Yo soy estudiante.", "translation": "Я студент."},
                {"sentence": "Ella está cansada.", "translation": "Она устала."},
                {"sentence": "El libro está en la mesa.", "translation": "Книга на столе."},
                {"sentence": "Nosotros somos de España.", "translation": "Мы из Испании."},
            ]
        },
        2: {
            "id": 2,  # ID второго урока
            "title": "Глагол Haber",
            "content": """
                Глагол <strong>haber</strong> используется для выражения существования и для образования сложных времён.
                <ul>
                    <li>Используется в конструкции <strong>hay</strong>, чтобы сказать "есть", "существует".</li>
                    <li>В сложных временах используется как вспомогательный глагол.</li>
                </ul>
            """,
            "haber_conjugations": {
                "Presente": ["he", "has", "ha", "hemos", "habéis", "han"],
                "Pretérito": ["hube", "hubiste", "hubo", "hubimos", "hubisteis", "hubieron"],
                "Futuro": ["habré", "habrás", "habrá", "habremos", "habréis", "habrán"]
            },
            "examples": [
                {"sentence": "Hay un libro en la mesa.", "translation": "На столе есть книга."},
                {"sentence": "He estudiado mucho hoy.", "translation": "Я много учился сегодня."},
                {"sentence": "Habrá una reunión mañana.", "translation": "Завтра будет собрание."},
            ]
        }
    }

    # Получаем урок по его ID
    lesson = lessons.get(lesson_id)
    if not lesson:
        flash("Урок не найден.", "error")
        return redirect(url_for("dashboard"))

    # Отображение урока
    return render_template("grammar_lesson.html", lesson=lesson)

@app.route("/grammar/test/<int:lesson_id>", methods=["GET", "POST"])
def grammar_test(lesson_id):
    questions_data = {
        1: [
            {"id": 1, "question": "¿Quién es el profesor?", "translation": "Кто учитель?", "options": ["ser", "estar", "tener", "hacer"], "answer": "ser"},
            {"id": 2, "question": "¿Dónde está el libro?", "translation": "Где находится книга?", "options": ["ser", "estar", "tener", "hacer"], "answer": "estar"},
            {"id": 3, "question": "¿De dónde somos?", "translation": "Откуда мы?", "options": ["ser", "estar", "tener", "hacer"], "answer": "ser"},
            {"id": 4, "question": "¿Cómo está ella?", "translation": "Как она себя чувствует?", "options": ["ser", "estar", "tener", "hacer"], "answer": "estar"},
        ],
        2: [
            {"id": 1, "question": "¿Qué hay en la mesa?", "translation": "Что есть на столе?", "options": ["Hay", "Ser", "Estar", "Haber"], "answer": "Hay"},
            {"id": 2, "question": "¿Has hecho los deberes?", "translation": "Ты сделал домашнюю работу?", "options": ["Has", "Ser", "Estar", "Haber"], "answer": "Has"},
            {"id": 3, "question": "¿Habrá una reunión mañana?", "translation": "Завтра будет собрание?", "options": ["Habrá", "Hay", "Hacer", "Hubo"], "answer": "Habrá"},
        ],
    }

    # Получение данных вопросов для урока
    questions = questions_data.get(lesson_id)
    if not questions:
        flash("Тест для данного урока не найден.", "error")
        return redirect(url_for("grammar"))  # Возврат к списку уроков, если урока нет

    if request.method == "POST":
        user_answers = request.form
        correct_answers = sum(1 for q in questions if user_answers.get(f"question-{q['id']}") == q["answer"])
        score = (correct_answers / len(questions)) * 100

        # Сохранение прогресса в базе данных
        progress = Progress.query.filter_by(user_id=session["user_id"], grammar_lesson_id=lesson_id).first()
        if not progress:
            progress = Progress(user_id=session["user_id"], grammar_lesson_id=lesson_id, score=score)
            db.session.add(progress)
        else:
            progress.score = max(progress.score, score)  # Обновление, если результат выше предыдущего
        db.session.commit()

        # Возврат результатов
        return render_template("test_result.html", score=score, total=len(questions), correct=correct_answers)

    # Отображение теста
    return render_template("grammar_test.html", questions=questions, lesson_title=f"Урок {lesson_id}")

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