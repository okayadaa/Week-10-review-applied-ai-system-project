"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob
import string
from typing import List, Tuple, Dict

class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Parse documents into sections: List of (filename, section_title, section_text)
        self.sections = []
        for filename, text in self.documents:
            self.sections.extend(self.parse_sections(filename, text))

        # Fast lookup: (filename, section_title) -> section_text
        self.section_lookup = {
            (fn, title): text for fn, title, text in self.sections
        }

        # Build a retrieval index (implemented in Phase 1)
        self.index = self.build_index(self.documents)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    def parse_sections(self, filename: str, text: str) -> List[Tuple]:
        """
        Splits a document into sections at each line starting with '#'.
        Returns a list of (filename, section_title, section_text) tuples.
        The header line is included in section_text so it contributes to scoring.
        Falls back to the whole document as one section if no headers are found.
        """
        sections = []
        current_title = None
        current_lines = []

        for line in text.splitlines():
            if line.startswith("#"):
                if current_lines:
                    sections.append((filename, current_title, "\n".join(current_lines)))
                current_title = line.strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            title = current_title if current_title else filename
            sections.append((filename, title, "\n".join(current_lines)))

        return sections

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_index(self, documents: List[Tuple]) -> Dict:
        """
        TODO (Phase 1):
        Build a tiny inverted index mapping lowercase words to the documents
        they appear in.

        Example structure:
        {
            "token": ["AUTH.md", "API_REFERENCE.md"],
            "database": ["DATABASE.md"]
        }

        Keep this simple: split on whitespace, lowercase tokens,
        ignore punctuation if needed.
        """
        index = {}
        for filename, section_title, section_text in self.sections:
            for word in section_text.lower().split():
                word = word.strip(string.punctuation)
                if word not in string.punctuation:
                    index.setdefault(word, set()).add((filename, section_title))

        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query: str, text: str) -> int:
        """
        TODO (Phase 1):
        Return a simple relevance score for how well the text matches the query.

        Suggested baseline:
        - Convert query into lowercase words
        - Count how many appear in the text
        - Return the count as the score
        """
        # TODO: implement scoring
        q = set()
        for word in query.lower().split(): 
             if word not in string.punctuation: 
                    q.add(word)
        
        counts = 0
        for word in text.lower().split(): 
            word = word.strip(string.punctuation)
            if word not in string.punctuation and word in q: 
                counts+=1

        return counts

    def retrieve(self, query, top_k=3):
        """
        TODO (Phase 1):
        Use the index and scoring function to select top_k relevant document snippets.

        Return a list of (filename, text) sorted by score descending.
        """
        # Step 1: Find candidate (filename, section_title) pairs using the index
        candidates = set()
        for word in query.lower().split():
            word = word.strip(string.punctuation)
            if word in self.index:
                candidates.update(self.index[word])

        # Step 2: Score each candidate section
        scored = []
        for filename, section_title in candidates:
            section_text = self.section_lookup[(filename, section_title)]
            score = self.score_document(query, section_text)
            scored.append((score, filename, section_title, section_text))

        # Step 3: Sort by score descending, return top k (label, text) pairs
        # Label includes section title so callers can see which section matched
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            (f"{filename}#{section_title}", section_text)
            for _, filename, section_title, section_text in scored
        ][:top_k]

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)

if __name__ == "__main__":
    bot = DocuBot() 

    query = "Where is the auth token generated?"
    result = bot.retrieve(query, 1)
    