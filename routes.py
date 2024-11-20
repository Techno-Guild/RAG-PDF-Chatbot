from flask import render_template, request, redirect, flash, url_for, jsonify
from flask_login import login_user, logout_user, current_user, login_required
from models import User
from sqlalchemy.exc import IntegrityError
import os
from langchain_groq import ChatGroq
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from langchain.chains import create_retrieval_chain
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from dotenv import load_dotenv
import time
import logging

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.ERROR)

groq_api_key = os.getenv('GROQ_API_KEY')
google_api_key = os.getenv("GOOGLE_API_KEY")

if google_api_key is None:
    raise ValueError("GOOGLE_API_KEY environment variable is not set.")

os.environ["GOOGLE_API_KEY"] = google_api_key

llm = ChatGroq(groq_api_key=groq_api_key, model_name="Llama3-8b-8192")

prompt = ChatPromptTemplate.from_template("""
Answer the questions based on the provided context only.
Please provide the most accurate response based on the question
<context>
{context}
<context>
Questions:{input}
""")

embeddings = None
loader = None
docs = None
vectors = None
initialized = False

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploaded_docs')

def register_routes(app, db, bcrypt):
    @app.route("/")
    def index():
        try:
            return render_template("index.html", user=current_user)
        except Exception as e:
            logging.error(f"Error during index: {e}")
            flash("An unexpected error occurred. Please try again later.", "danger")
            return render_template("index.html", user=current_user)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        try:
            if request.method == "POST":
                username = request.form["username"]
                password = request.form["password"]
                user = User.query.filter_by(username=username).first()
                if user and bcrypt.check_password_hash(user.password, password):
                    login_user(user)
                    return redirect(url_for("protected"))
                flash("Invalid username or password", "warning")
        except Exception as e:
            logging.error(f"Error during login: {e}")
            flash("An unexpected error occurred. Please try again later.", "danger")
        return render_template("login.html")

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        try:
            if request.method == "POST":
                username = request.form["username"]
                name = request.form["name"]
                password = request.form["password"]
                email = request.form["email"]
                hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
                new_user = User(
                    name=name, username=username, email=email, password=hashed_password
                )
                db.session.add(new_user)
                db.session.commit()
                return redirect(url_for("login"))
        except IntegrityError:
            db.session.rollback()
            flash("Username or email already exists", "warning")
        except Exception as e:
            logging.error(f"Error during signup: {e}")
            flash("An unexpected error occurred. Please try again later.", "danger")
        return render_template("signup.html")

    @app.route("/logout")
    @login_required
    def logout():
        try:
            logout_user()
            return redirect(url_for("index"))
        except Exception as e:
            logging.error(f"Error during logout: {e}")
            flash("An unexpected error occurred. Please try again later.", "danger")
            return redirect(url_for("index"))

    @app.route("/protected")
    @login_required
    def protected():
        try:
            return render_template("protected.html")
        except Exception as e:
            logging.error(f"Error during protected: {e}")
            flash("An unexpected error occurred. Please try again later.", "danger")
            return render_template("protected.html")

    @app.route("/upload", methods=["GET", "POST"])
    @login_required
    def upload_files():
        try:
            if request.method == "POST":
                uploaded_files = request.files.getlist("pdf_files")
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                for uploaded_file in uploaded_files:
                    uploaded_file.save(os.path.join(UPLOAD_FOLDER, uploaded_file.filename))
                flash("Files uploaded successfully.", "success")
                return redirect(url_for("initialize_embeddings"))
        except Exception as e:
            logging.error(f"Error during file upload: {e}")
            flash("An unexpected error occurred. Please try again later.", "danger")
        return render_template("upload.html")

    @app.route("/initialize_embeddings")
    @login_required
    def initialize_embeddings():
        global embeddings, loader, docs, vectors, initialized
        try:
            if vectors is None:
                embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
                loader = PyPDFDirectoryLoader(UPLOAD_FOLDER)
                docs = loader.load()

                if len(docs) < 4:
                    flash("Not enough documents loaded. Please ensure there are at least 4 documents.", "warning")
                    return redirect(url_for("upload_files"))

                text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
                final_documents = text_splitter.split_documents(docs[:4])
                vectors = FAISS.from_documents(final_documents, embeddings)
                initialized = True
                flash("Document embeddings have been initialized successfully.", "success")
        except Exception as e:
            logging.error(f"Error during embeddings initialization: {e}")
            flash("An unexpected error occurred. Please try again later.", "danger")
        return redirect(url_for("ask_question"))

    @app.route("/ask_question", methods=["GET", "POST"])
    @login_required
    def ask_question():
        uploaded_files = os.listdir(UPLOAD_FOLDER) if os.path.exists(UPLOAD_FOLDER) else []
        try:
            if request.method == "POST":
                question = request.form.get("question")
                if initialized:
                    document_chain = create_stuff_documents_chain(llm, prompt)
                    retriever = vectors.as_retriever()
                    retrieval_chain = create_retrieval_chain(retriever, document_chain)
                    response = retrieval_chain.invoke({'input': question})
                    answer = response['answer']
                    return render_template("ask.html", uploaded_files=uploaded_files, answer=answer, question=question)
                else:
                    flash("Please initialize the document embeddings first.", "warning")
        except Exception as e:
            logging.error(f"Error during question processing: {e}")
            flash("An unexpected error occurred. Please try again later.", "danger")
        return render_template("ask.html", uploaded_files=uploaded_files)
