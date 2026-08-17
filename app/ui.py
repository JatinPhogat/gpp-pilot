"""Simple Streamlit interface for the indexed 38-series prototype."""

from __future__ import annotations

import re

import streamlit as st

from app.generation import answer
from app.retrieval import HybridRetriever

st.set_page_config(page_title="GPP-Pilot", page_icon="◈", layout="wide")


@st.cache_resource(show_spinner=False)
def load_retriever() -> HybridRetriever:
    return HybridRetriever()


def small_talk(question: str) -> str | None:
    text = question.strip().lower().rstrip("!.? ")
    technical = r"\b(3gpp|ts\s*\d|nr|rrc|gnb|ue|nas|pdu|srb|drb|procedure|message|clause|spec)\b"
    if re.match(r"^(hi|hello|hey)\b", text) and not re.search(technical, text):
        return "Hi — ask me about 3GPP Release 18 specifications in the indexed 38-series corpus."
    if text in {"thanks", "thank you", "bye", "goodbye", "ok bye", "okay bye"}:
        return "You’re welcome."
    return None


def history_question(question: str) -> bool:
    return bool(re.search(r"\b(what|which|list).{0,25}\b(question|ask|asked)\b", question, re.I))


def search_question(question: str, messages: list[dict]) -> str:
    """Only add the previous question for a short, clearly referential follow-up."""
    if len(question.split()) > 12 or not re.search(r"\b(it|that|this|they|those)\b", question, re.I):
        return question
    previous = [item["text"] for item in messages if item["role"] == "user"]
    return f"{previous[-1]}\nFollow-up: {question}" if previous else question


def show_assistant(message: dict) -> None:
    st.markdown(message["text"])
    if message.get("citations"):
        cards = "".join(f'<span class="source-card">{label}</span>' for label in message["citations"])
        st.markdown(f'<div class="source-row">{cards}</div>', unsafe_allow_html=True)


st.markdown("""
<style>
    .stApp {background: #0b1020;}
    .block-container {max-width: 1160px; padding-top: 2rem; padding-bottom: 6rem;}
    [data-testid="stSidebar"] {background: #121a2d; border-right: 1px solid #263552;}
    [data-testid="stChatMessage"] {background: #121a2d; border: 1px solid #263552; border-radius: 14px;
        padding: .8rem 1rem; margin-bottom: .75rem;}
    [data-testid="stChatMessage"] p {line-height: 1.65;}
    h1 {color: #e8f0ff; letter-spacing: -.03em;}
    .source-row {display:flex; flex-wrap:wrap; gap:.45rem; margin-top:.9rem;}
    .source-card {background:#102d38; border:1px solid #2ab7a9; color:#bff6ed; border-radius:999px;
        padding:.28rem .65rem; font-size:.82rem; font-weight:600;}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.title("◈ GPP-Pilot")
    st.caption("Release 18 · indexed 38-series prototype")
    st.divider()
    st.subheader("Examples")
    examples = [
        "Explain the NR RRC connection establishment procedure.",
        "What is the role of SRB1 during RRC connection establishment?",
        "What does RRCSetupRequest contain?",
    ]
    for number, example in enumerate(examples):
        if st.button(example, key=f"example_{number}", use_container_width=True):
            st.session_state.pending = example

st.title("GPP-Pilot")
st.caption("Grounded 3GPP Release 18 specification assistant")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"], avatar="👤" if message["role"] == "user" else "🛰️"):
        if message["role"] == "assistant":
            show_assistant(message)
        else:
            st.markdown(message["text"])

typed = st.chat_input("Ask about a 3GPP procedure, message, or clause")
question = typed or st.session_state.pop("pending", None)

if question:
    prior = list(st.session_state.messages)
    user_message = {"role": "user", "text": question}
    st.session_state.messages.append(user_message)
    with st.chat_message("user", avatar="👤"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🛰️"):
        reply = small_talk(question)
        if reply:
            assistant_message = {"role": "assistant", "text": reply, "citations": []}
        elif history_question(question):
            old_questions = [item["text"] for item in prior if item["role"] == "user"]
            text = "\n".join(f"{index}. {item}" for index, item in enumerate(old_questions, start=1))
            assistant_message = {"role": "assistant", "text": f"Earlier questions in this chat:\n\n{text}" if text else "There are no earlier questions in this chat.", "citations": []}
        else:
            with st.status("Working on your question", expanded=True) as status:
                status.write("Reading 3GPP terminology")
                status.write("Searching indexed clauses")
                results = load_retriever().retrieve(search_question(question, prior))
                status.write("Checking retrieved evidence")
                generated = answer(question, results)
                status.update(label="Answer ready", state="complete", expanded=False)
            assistant_message = {"role": "assistant", "text": generated.text, "citations": generated.citations}
        show_assistant(assistant_message)
    st.session_state.messages.append(assistant_message)
