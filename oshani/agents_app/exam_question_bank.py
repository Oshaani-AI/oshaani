"""Load and format MCQ exam questions from agent training data."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Agent

logger = logging.getLogger(__name__)

# (agent_id, level) -> parsed questions
_BANK_CACHE: Dict[tuple, List[Dict[str, Any]]] = {}

QUESTION_SPLIT_RE = re.compile(r'\*\*Question\s+(\d+)\*\*', re.IGNORECASE)
CORRECT_ANSWER_RE = re.compile(r'\*\*Correct\s+Answer:\s*([A-Da-d])\*\*', re.IGNORECASE)
OPTION_RE = re.compile(r'^([A-Da-d])\)\s*(.+)$')
LEVEL_SECTION_RE = re.compile(
    r'###\s*LEVEL\s+(\d+)\s*:[^\n]*\n(.*?)(?=###\s*LEVEL\s+\d+\s*:|##\s+\d+\.\s|\Z)',
    re.IGNORECASE | re.DOTALL,
)


def _read_training_data_text(training_data) -> str:
    """Extract plain text from a TrainingData row (mirrors AgentLoop extraction)."""
    try:
        if training_data.data_type == 'file' and training_data.file_path:
            file_path = getattr(training_data.file_path, 'path', None)
            if not file_path and getattr(training_data.file_path, 'name', None):
                from django.conf import settings
                media_root = getattr(settings, 'MEDIA_ROOT', '')
                if media_root:
                    file_path = os.path.join(media_root, training_data.file_path.name)
            if file_path and os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        if training_data.content:
            if isinstance(training_data.content, dict):
                text = (
                    training_data.content.get('text')
                    or training_data.content.get('content')
                    or training_data.content.get('data')
                )
                if text:
                    return str(text)
            elif isinstance(training_data.content, str):
                return training_data.content
    except Exception as e:
        logger.warning('Failed to read training data %s: %s', training_data.pk, e)
    return ''


def extract_level_section(text: str, level: int) -> str:
    """Keep only the question bank section for the given level."""
    for match in LEVEL_SECTION_RE.finditer(text):
        if int(match.group(1)) == level:
            return match.group(2)
    return text


def parse_questions_from_markdown(text: str, level: Optional[int] = 1) -> List[Dict[str, Any]]:
    """
    Parse questions like:
      **Question 1**
      Stem line
      A) ...
      **Correct Answer: B**
    """
    if not text or not text.strip():
        return []

    scope = extract_level_section(text, level) if level else text
    parts = QUESTION_SPLIT_RE.split(scope)
    questions: List[Dict[str, Any]] = []

    for i in range(1, len(parts), 2):
        try:
            number = int(parts[i])
        except (TypeError, ValueError):
            continue
        body = parts[i + 1] if i + 1 < len(parts) else ''
        correct_match = CORRECT_ANSWER_RE.search(body)
        correct = correct_match.group(1).upper() if correct_match else None
        body_before_answer = CORRECT_ANSWER_RE.split(body, maxsplit=1)[0]

        lines = [ln.strip() for ln in body_before_answer.strip().splitlines() if ln.strip()]
        stem = lines[0] if lines else ''
        options: Dict[str, str] = {}
        for line in lines[1:]:
            opt = OPTION_RE.match(line)
            if opt:
                options[opt.group(1).upper()] = opt.group(2).strip()

        if stem and options:
            questions.append({
                'number': number,
                'stem': stem,
                'options': options,
                'correct': correct,
            })

    questions.sort(key=lambda q: q['number'])
    return questions


def load_question_bank(agent: 'Agent', level: int = 1) -> List[Dict[str, Any]]:
    """Load MCQ bank from agent training files (cached per agent + level)."""
    level = max(1, min(3, int(level or 1)))
    cache_key = (agent.id, level)
    if cache_key in _BANK_CACHE:
        return _BANK_CACHE[cache_key]

    combined: Dict[int, Dict[str, Any]] = {}
    for td in agent.training_data.all():
        text = _read_training_data_text(td)
        if not text:
            continue
        for q in parse_questions_from_markdown(text, level=level):
            combined[q['number']] = q

    bank = [combined[n] for n in sorted(combined)]
    _BANK_CACHE[cache_key] = bank
    if bank:
        logger.info('Loaded %s exam questions for agent %s level %s', len(bank), agent.id, level)
    return bank


def question_by_number(bank: List[Dict[str, Any]], number: int) -> Optional[Dict[str, Any]]:
    for q in bank:
        if q['number'] == number:
            return q
    if 1 <= number <= len(bank):
        return bank[number - 1]
    return None


def format_question_display(q: Dict[str, Any], index: int, total: int) -> str:
    """User-facing question text (no correct answer)."""
    lines = [f'**Question {index}/{total}**', '', q['stem'], '']
    for letter in ('A', 'B', 'C', 'D'):
        if letter in q.get('options', {}):
            lines.append(f'{letter}) {q["options"][letter]}')
    lines.extend(['', 'Reply with **A**, **B**, **C**, or **D** only.'])
    return '\n'.join(lines)


def grade_answer(user_letter: str, q: Dict[str, Any]) -> str:
    correct = (q.get('correct') or '').upper()
    if not correct:
        return f'✅ Answer **{user_letter.upper()}** recorded.'
    if user_letter.upper() == correct:
        return f'✅ **Correct!** (Answer: {correct})'
    return f'❌ **Incorrect.** The correct answer is **{correct}**.'


def compute_score(answers: Dict[str, str], bank: List[Dict[str, Any]]) -> int:
    score = 0
    for key, letter in answers.items():
        q = question_by_number(bank, int(key))
        if q and q.get('correct') and letter.upper() == q['correct'].upper():
            score += 1
    return score


def build_exam_deterministic_reply(agent: 'Agent', conversation, user_message: str) -> Optional[str]:
    """
    When a question bank exists in training data, grade MCQ answers and present the next
    question without calling the LLM (avoids invented questions and reasoning leaks).
    """
    from .conversation_session_state import (
        get_session_state,
        parse_mcq_answer,
        structured_session_state_enabled,
    )

    if not structured_session_state_enabled(agent):
        return None

    state = get_session_state(conversation)
    if not state.get('exam_started'):
        return None

    level = state.get('level') or 1
    bank = load_question_bank(agent, level)
    if not bank:
        return None

    total = int(state.get('total_questions') or len(bank))
    msg_upper = (user_message or '').strip().upper()

    if msg_upper in ('START', 'START EXAM', 'BEGIN', 'BEGIN EXAM'):
        q1 = question_by_number(bank, 1)
        if not q1:
            return None
        intro = 'Exam started. Good luck!\n\n'
        if state.get('user_name'):
            intro = f"Welcome, {state['user_name']}! " + intro
        return intro + format_question_display(q1, 1, total)

    letter = parse_mcq_answer(user_message)
    if not letter:
        return None

    answers = state.get('answers') or {}
    if not answers:
        return None

    last_q = max(int(k) for k in answers)
    answered_q = question_by_number(bank, last_q)
    if not answered_q:
        return None

    feedback = grade_answer(letter, answered_q)

    if state.get('exam_completed'):
        score = compute_score(answers, bank)
        pct = round(100 * score / total) if total else 0
        passing = 21 if total == 30 else int(total * 0.7)
        passed = score >= passing
        status = '**PASSED**' if passed else '**NOT PASSED**'
        return (
            f'{feedback}\n\n'
            f'---\n\n'
            f'🎉 **Exam complete!**\n\n'
            f'Final score: **{score}/{total}** ({pct}%)\n'
            f'Passing threshold: {passing}/{total}\n'
            f'Result: {status}\n\n'
            f'Thank you for completing the exam.'
        )

    next_num = state.get('awaiting_answer_for') or (last_q + 1)
    next_q = question_by_number(bank, next_num)
    if not next_q:
        return feedback

    return f'{feedback}\n\n{format_question_display(next_q, next_num, total)}'
