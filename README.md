# DocuBot

DocuBot is a small documentation assistant that helps answer developer questions about a codebase.  
It can operate in three different modes:

1. **Naive LLM mode**  
   Sends the entire documentation corpus to a Gemini model and asks it to answer the question.

2. **Retrieval only mode**  
   Uses a simple indexing and scoring system to retrieve relevant snippets without calling an LLM.

3. **RAG mode (Retrieval Augmented Generation)**  
   Retrieves relevant snippets, then asks Gemini to answer using only those snippets.

The docs folder contains realistic developer documents (API reference, authentication notes, database notes), but these files are **just text**. They support retrieval experiments and do not require students to set up any backend systems.

---

## Setup

### 1. Install Python dependencies

    pip install -r requirements.txt

### 2. Configure environment variables

Copy the example file:

    cp .env.example .env

Then edit `.env` to include your Gemini API key:

    GEMINI_API_KEY=your_api_key_here

If you do not set a Gemini key, you can still run retrieval only mode.

---

## Running DocuBot

Start the program:

    python main.py

Choose a mode:

- **1**: Naive LLM (Gemini reads the full docs)  
- **2**: Retrieval only (no LLM)  
- **3**: RAG (retrieval + Gemini)

You can use built in sample queries or type your own.

---

## Running Retrieval Evaluation (optional)

    python evaluation.py

This prints simple retrieval hit rates for sample queries.

---

## Modifying the Project

You will primarily work in:

- `docubot.py`  
  Implement or improve the retrieval index, scoring, and snippet selection.

- `llm_client.py`  
  Adjust the prompts and behavior of LLM responses.

- `dataset.py`  
  Add or change sample queries for testing.

---

## Requirements

- Python 3.9+
- A Gemini API key for LLM features (only needed for modes 1 and 3)
- No database, no server setup, no external services besides LLM calls


## TF README

Submit a short summary added to the README. The summary should be 5–7 sentences covering:
1. The core concept students needed to understand
2. Where students are most likely to struggle
3. Where AI was helpful vs misleading
4. One way they would guide a student without giving the answer

Before student dive into the Tinker activity, they need to understand the basics of RAG, and that we are not implementing a traditional RAG. Students are likely to struggle when trying to bridge the build_index() and the retrieval() function. build_index() was a bit confusing, but the general idea is to map each word to its document. retrieval() is also a bit challenging as it requires using the function the student build without having an "internal" or "pre-established" map they can refer to. I did not encounter any misleading moments with AI, but Claude's code is not the most "reader" friendly, therefore almost half the time I was revising the functions to understand what's doing exactly. 

I will guide student to ask Claude to given an UMP style map and how each of the class method connect among themselves. 
